"""Explicit setup of local paths and optional audio dependencies; no WhatsApp writes."""

from __future__ import annotations

import hashlib
import http.client
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import audio
from .config import ConfigError, config_directory, config_path, load_config, private_directory, save_config, setting
from .repository import Repository, RepositoryError


MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base.bin"
MODEL_SHA256 = "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"
MODEL_BYTES = 147951465
MODEL_NAME = "ggml-base.bin"
DOWNLOAD_TIMEOUT_SECONDS = 600
DOWNLOAD_SOCKET_TIMEOUT_SECONDS = 10
INSTALL_TIMEOUT_SECONDS = 900
_SETUP_LOCK = threading.Lock()


class OnboardingError(Exception):
    """A setup failure with actionable text and no captured process diagnostics."""


def _cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise OnboardingError("Local setup was cancelled.")


def _brew() -> str | None:
    located = shutil.which("brew")
    candidates = [Path(located)] if located else []
    candidates.extend(Path(directory) / "brew" for directory in ("/opt/homebrew/bin", "/usr/local/bin"))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return None


def _safe_audio_status() -> dict:
    try:
        return audio.audio_status()
    except ConfigError as exc:
        return {"available": False, "error": str(exc)}


def _candidates() -> list[dict]:
    entries = []
    for index, path in enumerate(Repository.discover_candidates(), start=1):
        entry = {"index": index, "path": str(path), "available": False}
        try:
            Repository(db_path=path, media_root=path.parent / "Message").status()
            entry["available"] = True
        except (RepositoryError, ConfigError, OSError, ValueError) as exc:
            entry["error"] = str(exc) if isinstance(exc, (RepositoryError, ConfigError)) else "Cannot read this database candidate."
        entries.append(entry)
    return entries


def onboarding_status() -> dict:
    """Inspect paths and schemas only; do not install, save settings, or read messages."""
    supported = sys.platform == "darwin"
    config_state = {"path": str(config_path()), "exists": config_path().exists(), "valid": True}
    try:
        saved = load_config()
        config_state["configured_keys"] = sorted(saved)
    except ConfigError as exc:
        config_state.update(valid=False, error=str(exc))
    try:
        candidates = _candidates() if supported else []
    except (RepositoryError, OSError):
        candidates = []
    try:
        database = Repository().status() if supported else {"available": False, "error": "macOS is required."}
    except (RepositoryError, ConfigError, OSError, ValueError) as exc:
        database = {"available": False, "error": str(exc) if isinstance(exc, (RepositoryError, ConfigError)) else "Cannot inspect the local database."}
    audio_state = _safe_audio_status()
    steps = []
    if not supported:
        steps.append("Use the native WhatsApp application on macOS.")
    elif not config_state["valid"]:
        steps.append("Repair or remove the invalid local configuration file, then run setup again.")
    elif not database.get("available"):
        if len(candidates) > 1:
            steps.append("Choose a database_index from database_candidates and run setup; no account is chosen automatically.")
        elif candidates:
            steps.append("Run setup to validate and save the discovered database; review macOS file-access permission if access fails.")
        else:
            steps.append("Open native WhatsApp and let it synchronize. If macOS denies access, allow the application that starts this MCP server to read WhatsApp's files.")
    elif not config_state["exists"]:
        steps.append("Run setup to save discovered paths for future sessions.")
    if supported and not audio_state.get("available"):
        steps.append("To enable voice notes, run setup with enable_audio=true. Missing binaries require an existing Homebrew installation; a verified speech model can be downloaded automatically.")
    return {
        "supported_platform": supported,
        "config": config_state,
        "database": database,
        "database_candidates": candidates,
        "audio": audio_state,
        "homebrew_available": _brew() is not None if supported else False,
        "next_steps": steps,
        "setup_writes": "Private local configuration; optional Homebrew packages and a verified Whisper model. WhatsApp storage is never modified.",
    }


def _choose_database(database_index: int | None) -> Path:
    configured = setting("WHATSAPP_MCP_DB_PATH")
    if database_index is None and configured:
        return Path(configured).expanduser().resolve()
    candidates = Repository.discover_candidates()
    if database_index is not None:
        if type(database_index) is not int or not 1 <= database_index <= len(candidates):
            raise OnboardingError("database_index must be a 1-based index returned by onboarding status.")
        chosen = candidates[database_index - 1]
        environment = os.environ.get("WHATSAPP_MCP_DB_PATH")
        if environment and Path(environment).expanduser().resolve() != chosen.resolve():
            raise OnboardingError("WHATSAPP_MCP_DB_PATH overrides the selected account. Remove that environment override or choose the matching database.")
        return chosen
    if not candidates:
        raise OnboardingError("No WhatsApp database was found. Open native WhatsApp, let it synchronize, and review macOS file-access permissions.")
    if len(candidates) > 1:
        raise OnboardingError("Multiple WhatsApp databases were found. Inspect onboarding status and choose database_index explicitly.")
    return candidates[0]


def _audio_settings(state: dict) -> dict[str, str]:
    return {key: state[field] for key, field in (
        ("whisper_bin", "whisper_bin"), ("ffmpeg_bin", "ffmpeg_bin"), ("whisper_model", "model_path")
    ) if state.get(field)}


def _install_binaries(state: dict, cancel_event: threading.Event | None) -> None:
    formulas = []
    for field, formula, env_name in (
        ("ffmpeg_bin", "ffmpeg", "WHATSAPP_MCP_FFMPEG_BIN"),
        ("whisper_bin", "whisper-cpp", "WHATSAPP_MCP_WHISPER_BIN"),
    ):
        if not state.get(field):
            if os.environ.get(env_name):
                raise OnboardingError(f"The {env_name} override is unavailable. Correct or remove it before automatic audio setup.")
            formulas.append(formula)
    if not formulas:
        return
    brew = _brew()
    if brew is None:
        raise OnboardingError("Text setup is ready. Install Homebrew from https://brew.sh or install ffmpeg and whisper-cpp yourself, then rerun setup with enable_audio=true. This tool does not install Homebrew or use sudo.")
    _cancelled(cancel_event)
    try:
        with tempfile.TemporaryDirectory(prefix="whatsapp-mcp-setup-") as temporary:
            result = audio._run([
                "/usr/bin/env", "NONINTERACTIVE=1", "HOMEBREW_NO_AUTO_UPDATE=1",
                "HOMEBREW_NO_ANALYTICS=1", "HOMEBREW_NO_INSTALL_CLEANUP=1",
                brew, "install", *formulas,
            ], INSTALL_TIMEOUT_SECONDS, "instalação das dependências", Path(temporary), cancel_event)
        if result.returncode:
            raise OnboardingError("Homebrew could not install the missing audio dependencies. Check Homebrew and its developer-tool requirements, then retry. Text setup remains available.")
    except audio.AudioError:
        _cancelled(cancel_event)
        raise OnboardingError("Audio dependency installation failed or timed out. Check Homebrew and retry; text setup remains available.") from None


def _trusted_download_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        trusted = host == "huggingface.co" or host.endswith(".huggingface.co") or host.endswith(".hf.co")
        return parsed.scheme == "https" and trusted and parsed.username is None and parsed.password is None and parsed.port in (None, 443)
    except ValueError:
        return False


class _TrustedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        if not _trusted_download_url(newurl):
            raise OnboardingError("The model download redirected outside the allowed HTTPS model hosts.")
        return super().redirect_request(request, response, code, message, headers, newurl)


def _model_matches(path: Path, cancel_event: threading.Event | None) -> bool:
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != MODEL_BYTES:
            return False
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                _cancelled(cancel_event)
                digest.update(chunk)
        return digest.hexdigest() == MODEL_SHA256
    except OSError:
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _download_model(cancel_event: threading.Event | None = None) -> Path:
    """Download the fixed, revision-pinned multilingual base model and verify it."""
    _cancelled(cancel_event)
    private_directory(config_directory())
    directory = private_directory(config_directory() / "models")
    destination = directory / MODEL_NAME
    if _model_matches(destination, cancel_event):
        return destination
    if not _trusted_download_url(MODEL_URL):
        raise OnboardingError("The bundled model URL is not an allowed HTTPS model host.")
    temporary = None
    try:
        descriptor, filename = tempfile.mkstemp(prefix=".model-", suffix=".tmp", dir=directory)
        temporary = Path(filename)
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            opener = urllib.request.build_opener(_TrustedRedirects())
            request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "whatsapp-mcp-setup/1"})
            deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
            with opener.open(request, timeout=DOWNLOAD_SOCKET_TIMEOUT_SECONDS) as response:
                if not _trusted_download_url(response.geturl()):
                    raise OnboardingError("The model response came from an untrusted host.")
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) != MODEL_BYTES:
                    raise OnboardingError("The model download size did not match the pinned release.")
                digest, total = hashlib.sha256(), 0
                while True:
                    _cancelled(cancel_event)
                    if time.monotonic() >= deadline:
                        raise OnboardingError("The model download exceeded its time limit. Retry audio setup when the connection is available.")
                    # read1 performs one buffered/socket read, so a slow continuous
                    # stream cannot defer cancellation until an entire MiB fills.
                    chunk = response.read1(min(1024 * 1024, MODEL_BYTES - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MODEL_BYTES:
                        raise OnboardingError("The model download exceeded the pinned release size.")
                    digest.update(chunk)
                    output.write(chunk)
            if total != MODEL_BYTES or digest.hexdigest() != MODEL_SHA256:
                raise OnboardingError("The model download failed its size or SHA-256 verification. No downloaded model was installed.")
            output.flush()
            os.fsync(output.fileno())
        _cancelled(cancel_event)
        os.replace(temporary, destination)
        return destination
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
        raise OnboardingError("The verified Whisper model could not be downloaded. Check the connection and available disk space, then retry audio setup.") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                raise OnboardingError("Cannot remove a partial model file. Review permissions in the private models directory.") from None


def setup(database_index: int | None = None, enable_audio: bool = False,
          cancel_event: threading.Event | None = None) -> dict:
    """Save local paths, optionally install audio tools and a verified speech model."""
    if sys.platform != "darwin":
        raise OnboardingError("Automatic setup is supported on macOS only.")
    if type(enable_audio) is not bool:
        raise OnboardingError("enable_audio must be true or false.")
    if not _SETUP_LOCK.acquire(blocking=False):
        raise OnboardingError("Local setup is already running. Wait for it to finish before retrying.")
    try:
        _cancelled(cancel_event)
        selected = _choose_database(database_index)
        prior = load_config()
        previous_database = prior.get("db_path")
        explicit_media = os.environ.get("WHATSAPP_MCP_MEDIA_ROOT")
        if not explicit_media and previous_database and Path(previous_database).resolve() == selected.resolve():
            explicit_media = prior.get("media_root")
        media_root = Path(explicit_media).expanduser().resolve() if explicit_media else selected.parent / "Message"
        repository = Repository(db_path=selected, media_root=media_root)
        database = repository.status()
        values = {"db_path": str(repository.db_path), "media_root": str(repository.media_root)}
        state = audio.audio_status()
        values.update(_audio_settings(state))
        # Save text setup first. Drop stale persisted audio paths, allowing discovery
        # to repair them; explicit environment overrides remain authoritative.
        _cancelled(cancel_event)
        save_config(values)
        state = audio.audio_status()
        if enable_audio:
            _install_binaries(state, cancel_event)
            state = audio.audio_status()
            if not state.get("ffmpeg_bin") or not state.get("whisper_bin"):
                raise OnboardingError("Audio binaries remain unavailable after setup. Restart the MCP client or configure their absolute paths.")
            if not state.get("model_path"):
                if os.environ.get("WHATSAPP_MCP_WHISPER_MODEL"):
                    raise OnboardingError("WHATSAPP_MCP_WHISPER_MODEL points to an unavailable model. Correct or remove the override before automatic setup.")
                values["whisper_model"] = str(_download_model(cancel_event))
            values.update(_audio_settings(state))
            _cancelled(cancel_event)
            save_config(values)
            state = audio.audio_status()
        elif _audio_settings(state):
            values.update(_audio_settings(state))
            _cancelled(cancel_event)
            save_config(values)
        return {
            "ok": True,
            "config_path": str(config_path()),
            "database": database,
            "audio": state,
            "text_ready": True,
            "audio_requested": enable_audio,
            "whatsapp_modified": False,
            "message": "Local setup saved. The existing WhatsApp database was only read.",
        }
    except (RepositoryError, ConfigError, OSError, ValueError) as exc:
        if isinstance(exc, (RepositoryError, ConfigError)):
            raise OnboardingError(str(exc)) from None
        raise OnboardingError("Local setup could not complete. Review file permissions and the selected configuration.") from None
    finally:
        _SETUP_LOCK.release()
