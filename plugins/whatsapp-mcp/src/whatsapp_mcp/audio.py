"""Offline audio transcription through installed ffmpeg and whisper.cpp binaries.

The caller supplies an attachment path already authorized by the WhatsApp repository.
This module never downloads a model, calls a service, or modifies the source file.
"""

from __future__ import annotations

import json
import ctypes
import errno
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections.abc import Callable

from .config import setting


MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_AUDIO_SECONDS = 30 * 60
MAX_JSON_BYTES = 8 * 1024 * 1024
CONVERSION_TIMEOUT_SECONDS = 120
TRANSCRIPTION_TIMEOUT_SECONDS = 30 * 60
MAX_DIAGNOSTIC_BYTES = 64 * 1024
PROCESS_STOP_GRACE_SECONDS = 0.25
_MODEL_ORDER = ("large-v3-turbo", "large-v3", "medium", "small", "base", "tiny", "large-v2", "large-v1")
_MODEL_NAME = re.compile(r"ggml-(tiny|base|small|medium|large-v[12]|large-v3(?:-turbo)?)(?:\.en)?(?:-q[458]_[01])?\.bin$")


class AudioError(Exception):
    """A safe, content-free error suitable for returning from an MCP tool."""


class _DarwinSigval(ctypes.Union):
    _fields_ = [("sival_int", ctypes.c_int), ("sival_ptr", ctypes.c_void_p)]


class _DarwinSiginfo(ctypes.Structure):
    # Darwin sys/signal.h siginfo_t, identical on supported arm64/x86_64 Macs.
    _fields_ = [
        ("si_signo", ctypes.c_int), ("si_errno", ctypes.c_int), ("si_code", ctypes.c_int),
        ("si_pid", ctypes.c_int), ("si_uid", ctypes.c_uint), ("si_status", ctypes.c_int),
        ("si_addr", ctypes.c_void_p), ("si_value", _DarwinSigval),
        ("si_band", ctypes.c_long), ("reserved", ctypes.c_ulong * 7),
    ]


def _make_exit_probe() -> Callable[[int], bool]:
    """Observe a child without reaping it, including macOS Python 3.11.

    Older CPython macOS builds omit os.waitid even though Darwin has waitid(2).
    Use its documented native ABI only on 64-bit Darwin; resolve it before spawn.
    """
    required = ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT")
    if not all(hasattr(os, name) for name in required):
        raise AudioError("Este Python não oferece a supervisão segura exigida pela transcrição local.")
    options = os.WEXITED | os.WNOHANG | os.WNOWAIT
    python_waitid = getattr(os, "waitid", None)
    if callable(python_waitid):
        return lambda pid: python_waitid(os.P_PID, pid, options) is not None
    if sys.platform != "darwin" or ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(_DarwinSiginfo) != 104:
        raise AudioError("Este Python não oferece a supervisão segura exigida pela transcrição local.")
    try:
        native_waitid = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True).waitid
        native_waitid.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_DarwinSiginfo), ctypes.c_int]
        native_waitid.restype = ctypes.c_int
    except (OSError, AttributeError):
        raise AudioError("Não foi possível preparar a supervisão segura dos processos locais.") from None

    def native_probe(pid: int) -> bool:
        information = _DarwinSiginfo()
        while native_waitid(os.P_PID, pid, ctypes.byref(information), options) == -1:
            error = ctypes.get_errno()
            if error != errno.EINTR:
                # ECHILD becomes ChildProcessError, preserving the ownership gate.
                raise OSError(error, "Falha na supervisão do processo local.")
        return information.si_pid != 0

    return native_probe


def _executable(env_name: str, binary_name: str) -> str | None:
    override = setting(env_name)
    if override:
        # An invalid explicit override must not silently choose another executable.
        candidate = Path(override).expanduser()
        if not candidate.is_absolute():
            return None
        return str(candidate.resolve()) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    located = shutil.which(binary_name)
    if located:
        return str(Path(located).resolve())
    for directory in ("/opt/homebrew/bin", "/usr/local/bin"):
        candidate = Path(directory) / binary_name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return None


def _model_directories() -> tuple[Path, ...]:
    home = Path.home()
    return (
        home / ".claude-video-vision/models",
        home / ".cache/whisper.cpp",
        home / ".cache/whisper",
        home / ".local/share/whisper.cpp/models",
        home / "Library/Caches/whisper.cpp",
        home / "Library/Application Support/whisper.cpp/models",
        home / "whisper.cpp/models",
        Path("/opt/homebrew/share/whisper-cpp/models"),
        Path("/opt/homebrew/share/whisper-cpp"),
        Path("/usr/local/share/whisper-cpp/models"),
        Path("/usr/local/share/whisper-cpp"),
    )


def _readable_model(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0 and os.access(path, os.R_OK)
    except OSError:
        return False


def _model() -> Path | None:
    override = setting("WHATSAPP_MCP_WHISPER_MODEL")
    if override:
        candidate = Path(override).expanduser()
        return candidate.resolve() if candidate.is_absolute() and _readable_model(candidate) else None
    candidates = []
    for directory in _model_directories():
        try:
            candidates.extend(path for path in directory.glob("ggml-*.bin")
                              if _MODEL_NAME.fullmatch(path.name) and _readable_model(path))
        except OSError:
            continue
    # Prefer multilingual models and the fast large-v3-turbo model already used by
    # the local video workflow; never mistake Silero or Homebrew test weights for ASR.
    def rank(path: Path) -> tuple[int, int, int]:
        match = _MODEL_NAME.fullmatch(path.name)
        return (int(".en" in path.name), _MODEL_ORDER.index(match.group(1)), int("-q" in path.name))
    return min(candidates, key=rank).resolve() if candidates else None


def audio_status() -> dict:
    """Report paths and readiness without opening audio or invoking a subprocess."""
    whisper = _executable("WHATSAPP_MCP_WHISPER_BIN", "whisper-cli")
    ffmpeg = _executable("WHATSAPP_MCP_FFMPEG_BIN", "ffmpeg")
    model = _model()
    vad = model.parent / "ggml-silero-v5.1.2.bin" if model else None
    vad = vad.resolve() if vad and _readable_model(vad) else None
    supported = sys.platform == "darwin"
    missing = [name for name, value in (("whisper-cli", whisper), ("ffmpeg", ffmpeg), ("whisper_model", model)) if not value]
    return {
        "available": supported and not missing,
        "supported_platform": supported,
        "engine": "whisper.cpp",
        "whisper_bin": whisper,
        "ffmpeg_bin": ffmpeg,
        "model_path": str(model) if model else None,
        "model": model.name if model else None,
        "vad_model_path": str(vad) if vad else None,
        "missing": missing,
        "local_only": True,
        "max_input_bytes": MAX_INPUT_BYTES,
        "max_audio_seconds": MAX_AUDIO_SECONDS,
    }


def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise AudioError("Transcrição local cancelada.")


def _copy_source(source: Path | int, destination: Path, cancel_event: threading.Event | None = None) -> None:
    """Copy a pinned file; MCP supplies a descriptor authorized by Repository.

    The Path variant is an internal helper for callers that already own their
    source file. Repository containment must not rely on that variant.
    """
    descriptor = None
    try:
        if isinstance(source, int) and not isinstance(source, bool):
            # Keep ownership of the repository descriptor with its context manager.
            descriptor = os.dup(source)
            before = os.fstat(descriptor)
        else:
            before = source.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise AudioError("O áudio precisa ser um arquivo local regular, sem link simbólico.")
        if before.st_size <= 0 or before.st_size > MAX_INPUT_BYTES:
            raise AudioError("O áudio está vazio ou excede o limite de 128 MiB.")
        if descriptor is None:
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise AudioError("O arquivo de áudio mudou durante a leitura; tente novamente.")
        copied = 0
        with os.fdopen(descriptor, "rb") as reader:
            descriptor = None
            with destination.open("xb") as writer:
                os.chmod(destination, 0o600)
                while chunk := reader.read(1024 * 1024):
                    _check_cancelled(cancel_event)
                    copied += len(chunk)
                    if copied > MAX_INPUT_BYTES:
                        raise AudioError("O áudio excede o limite de 128 MiB.")
                    writer.write(chunk)
            after = os.fstat(reader.fileno())
            if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns) or copied != opened.st_size:
                raise AudioError("O arquivo de áudio mudou durante a leitura; tente novamente.")
    except OSError:
        raise AudioError("Não foi possível ler o arquivo local de áudio.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _stop_process(process: subprocess.Popen, *, owns_group: bool = False,
                  exit_probe: Callable[[int], bool] | None = None) -> None:
    """Stop the owned session before reaping its leader and releasing its PID.

    _run observes exit with WNOWAIT, so even an exited wrapper still reserves its
    process/group ID here. Never signal an already-reaped or unrelated group.
    """
    if process.returncode is not None:
        return
    if exit_probe is None:
        exit_probe = _make_exit_probe()
    try:
        exit_probe(process.pid)
    except ChildProcessError:
        # Another owner may already have reaped it. The numeric PID alone no
        # longer establishes that a process group belongs to this invocation.
        return
    if not owns_group:
        try:
            owns_group = os.getpgid(process.pid) == process.pid and os.getsid(process.pid) == process.pid
        except ProcessLookupError:
            process.wait()
            return
    if owns_group:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                # Darwin can report EPERM for a group containing only zombies.
                # Its unreaped leader still proves ownership of the group ID.
                if not exit_probe(process.pid):
                    raise
            if sig == signal.SIGTERM:
                # Leader exit is insufficient: a child can ignore SIGTERM.
                time.sleep(PROCESS_STOP_GRACE_SECONDS)
    else:
        # A non-session process must never cause us to signal our own or another
        # caller's process group. _run always requests start_new_session=True.
        process.kill()
    process.wait()


def _run(arguments: list[str], timeout: float, step: str, directory: Path,
         cancel_event: threading.Event | None = None) -> subprocess.CompletedProcess:
    process = None
    diagnostic = bytearray()
    _check_cancelled(cancel_event)
    exit_probe = _make_exit_probe()
    selector = selectors.DefaultSelector()

    def read_diagnostic() -> bool:
        try:
            chunk = os.read(process.stderr.fileno(), MAX_DIAGNOSTIC_BYTES)
        except BlockingIOError:
            return True
        if chunk:
            remaining = MAX_DIAGNOSTIC_BYTES - len(diagnostic)
            diagnostic.extend(chunk[:remaining])
            return True
        return False

    try:
        # Keep recognized words out of MCP/stdout and off disk. Drain the pipe
        # throughout execution, retaining only a fixed-size diagnostic prefix.
        process = subprocess.Popen(arguments, shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   cwd=directory, start_new_session=True, bufsize=0)
        os.set_blocking(process.stderr.fileno(), False)
        selector.register(process.stderr, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        while True:
            _check_cancelled(cancel_event)
            if time.monotonic() >= deadline:
                raise AudioError(f"Tempo limite excedido na {step} local do áudio.")
            # Do not use Popen.poll/wait here: reaping would release the leader's
            # PID before cleanup can safely terminate descendants in its group.
            if exit_probe(process.pid):
                break
            for key, _ in selector.select(timeout=min(0.05, max(0.001, deadline - time.monotonic()))):
                if not read_diagnostic():
                    selector.unregister(key.fileobj)
        _check_cancelled(cancel_event)
    except OSError:
        raise AudioError(f"Não foi possível executar a {step} local do áudio.") from None
    finally:
        try:
            if process is not None:
                try:
                    _stop_process(process, owns_group=True, exit_probe=exit_probe)
                finally:
                    if process.stderr is not None:
                        # After group termination one final nonblocking read is
                        # sufficient to fill the bounded prefix; discard the rest.
                        if len(diagnostic) < MAX_DIAGNOSTIC_BYTES:
                            read_diagnostic()
                        process.stderr.close()
        finally:
            selector.close()
    _check_cancelled(cancel_event)
    return subprocess.CompletedProcess(arguments, process.returncode, stderr=bytes(diagnostic))


def _metal_failed(stderr: bytes | str | None) -> bool:
    if isinstance(stderr, bytes):
        stderr = stderr[:65536].decode("utf-8", errors="replace")
    for line in (stderr or "")[:65536].lower().splitlines():
        if "metal" in line and any(marker in line for marker in ("failed", "error", "no device", "not available", "not supported")):
            return True
    return False


def _duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) != (1, 16000, 2):
                raise AudioError("A conversão local não gerou o formato de áudio esperado.")
            duration = wav.getnframes() / wav.getframerate()
            if not 0 < duration <= MAX_AUDIO_SECONDS:
                raise AudioError("O áudio está vazio ou excede o limite de 30 minutos.")
            return duration
    except (OSError, EOFError, wave.Error):
        raise AudioError("A conversão local não gerou um arquivo WAV válido.") from None


def _parse_result(path: Path, language: str, model: str) -> dict:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_JSON_BYTES:
            raise AudioError("O Whisper local não produziu um resultado JSON válido.")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("transcription"), list):
            raise ValueError
        segments = []
        for segment in data["transcription"]:
            offsets = segment["offsets"]
            start, end = offsets["from"], offsets["to"]
            if (isinstance(start, bool) or isinstance(end, bool)
                    or not isinstance(start, (float, int)) or not isinstance(end, (float, int))
                    or not math.isfinite(start) or not math.isfinite(end)
                    or not 0 <= start <= end <= (MAX_AUDIO_SECONDS + 1) * 1000
                    or not isinstance(segment["text"], str)):
                raise ValueError
            text = segment["text"].strip()
            if text:
                segments.append({"start_seconds": start / 1000, "end_seconds": end / 1000, "text": text})
        detected = data.get("result", {}).get("language", language)
        if not isinstance(detected, str) or not re.fullmatch(r"auto|[a-z]{2,3}", detected):
            raise ValueError
        return {"text": " ".join(segment["text"] for segment in segments), "segments": segments,
                "language": detected, "engine": "whisper.cpp", "model": model}
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise AudioError("O Whisper local não produziu um resultado JSON válido.") from None


def transcribe_audio(path: Path | int, language: str = "auto",
                     cancel_event: threading.Event | None = None) -> dict:
    """Transcribe one authorized local attachment and remove every working file."""
    if not isinstance(language, str) or not re.fullmatch(r"auto|[a-z]{2,3}", language):
        raise AudioError("Use 'auto' ou um código de idioma como 'pt', 'en' ou 'es'.")
    _check_cancelled(cancel_event)
    status = audio_status()
    if not status["supported_platform"]:
        raise AudioError("A transcrição deste plugin está disponível apenas no macOS.")
    if not status["available"]:
        raise AudioError("Transcrição local indisponível; verifique audio_status e configure os binários e o modelo existentes.")
    try:
        with tempfile.TemporaryDirectory(prefix="whatsapp-mcp-audio-") as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            source, wav = directory / "source.audio", directory / "audio.wav"
            output = directory / "transcription"
            result_path = output.with_suffix(".json")
            _copy_source(path if isinstance(path, int) else Path(path), source, cancel_event)
            # Restrict input protocols/demuxers so a disguised playlist cannot cause
            # network requests or instruct ffmpeg to open unrelated local files.
            converted = _run([
                status["ffmpeg_bin"], "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-protocol_whitelist", "file,pipe", "-format_whitelist", "ogg,mov,mp3,wav,flac,aac,amr,aiff,caf",
                "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
                "-t", str(MAX_AUDIO_SECONDS + 1), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                "-fs", str((MAX_AUDIO_SECONDS + 2) * 32000 + 4096), str(wav),
            ], CONVERSION_TIMEOUT_SECONDS, "conversão", directory, cancel_event)
            if converted.returncode:
                raise AudioError("Falha na conversão local do áudio; o arquivo pode estar incompleto ou em formato não suportado.")
            _duration(wav)
            arguments = [status["whisper_bin"], "-m", status["model_path"], "-f", str(wav),
                         "-l", language, "-oj", "-of", str(output)]
            if status.get("vad_model_path"):
                arguments.extend(["--vad", "--vad-model", status["vad_model_path"]])
            transcribed = _run(arguments, TRANSCRIPTION_TIMEOUT_SECONDS, "transcrição", directory, cancel_event)
            if transcribed.returncode and _metal_failed(transcribed.stderr):
                result_path.unlink(missing_ok=True)
                transcribed = _run(arguments + ["-ng"], TRANSCRIPTION_TIMEOUT_SECONDS, "transcrição", directory, cancel_event)
            if transcribed.returncode:
                raise AudioError("Falha no Whisper local; verifique se o modelo é compatível com o whisper-cli instalado.")
            _check_cancelled(cancel_event)
            return _parse_result(result_path, language, status["model"])
    except OSError:
        raise AudioError("Não foi possível preparar ou remover os arquivos temporários da transcrição local.") from None
