"""Synthetic audio/JSON fixtures only: no WhatsApp content or downloaded models."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

from whatsapp_mcp import audio


def write_wav(path, seconds=1):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * int(16000 * seconds))


class AudioTests(unittest.TestCase):
    def setUp(self):
        configuration = patch("whatsapp_mcp.config.load_config", return_value={})
        configuration.start()
        self.addCleanup(configuration.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "fake $(private).ogg"
        self.source.write_bytes(b"synthetic attachment")
        self.state = {
            "supported_platform": True, "available": True,
            "ffmpeg_bin": "/fake/ffmpeg", "whisper_bin": "/fake/whisper-cli",
            "model_path": "/fake/ggml-base.bin", "model": "ggml-base.bin",
            "vad_model_path": None,
        }
        self.calls = []
        self.output = {"result": {"language": "pt"}, "transcription": [
            {"timestamps": {"from": "00:00:00,000", "to": "00:00:00,500"},
             "offsets": {"from": 0, "to": 500}, "text": " Olá."},
            {"timestamps": {"from": "00:00:00,500", "to": "00:00:01,000"},
             "offsets": {"from": 500, "to": 1000}, "text": " Teste local. "},
        ]}

    def simulate(self, arguments, timeout, step, directory, cancel_event=None):
        self.calls.append((arguments, timeout, step, directory))
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        if step == "conversão":
            write_wav(Path(arguments[-1]))
        else:
            Path(arguments[arguments.index("-of") + 1] + ".json").write_text(json.dumps(self.output))
        return subprocess.CompletedProcess(arguments, 0, stderr=b"")

    def transcribe(self, **kwargs):
        with patch.object(audio, "audio_status", return_value=self.state), patch.object(audio, "_run", side_effect=self.simulate):
            return audio.transcribe_audio(self.source, **kwargs)

    def test_real_whisper_cpp_json_shape_and_safe_arguments(self):
        original = self.source.read_bytes()
        result = self.transcribe(language="pt")
        self.assertEqual(result, {
            "text": "Olá. Teste local.", "language": "pt", "engine": "whisper.cpp", "model": "ggml-base.bin",
            "segments": [{"start_seconds": 0.0, "end_seconds": 0.5, "text": "Olá."},
                         {"start_seconds": 0.5, "end_seconds": 1.0, "text": "Teste local."}],
        })
        conversion, whisper = self.calls
        arguments = conversion[0]
        self.assertIn("-nostdin", arguments)
        self.assertEqual(arguments[arguments.index("-protocol_whitelist") + 1], "file,pipe")
        self.assertEqual(arguments[arguments.index("-ar") + 1], "16000")
        self.assertEqual(arguments[arguments.index("-ac") + 1], "1")
        self.assertEqual(arguments[arguments.index("-c:a") + 1], "pcm_s16le")
        self.assertNotIn(str(self.source), arguments)
        self.assertIn("-oj", whisper[0])
        self.assertNotIn("-ng", whisper[0])
        self.assertFalse(conversion[3].exists())
        self.assertEqual(self.source.read_bytes(), original)

    def test_transcription_accepts_pinned_descriptor_without_reopening_path(self):
        descriptor = os.open(self.source, os.O_RDONLY)
        try:
            original = self.source.read_bytes()
            self.source.rename(self.directory / "original.ogg")
            self.source.write_bytes(b"replacement must never be transcribed")
            def check_source(arguments, timeout, step, directory, cancel_event=None):
                if step == "conversão":
                    self.assertEqual((directory / "source.audio").read_bytes(), original)
                return self.simulate(arguments, timeout, step, directory, cancel_event)
            with patch.object(audio, "audio_status", return_value=self.state), patch.object(audio, "_run", side_effect=check_source):
                self.assertEqual(audio.transcribe_audio(descriptor)["language"], "pt")
            self.assertEqual(os.fstat(descriptor).st_size, len(original))
        finally:
            os.close(descriptor)

    def test_vad_uses_only_an_existing_model(self):
        self.state["vad_model_path"] = "/fake/ggml-silero-v5.1.2.bin"
        self.transcribe()
        self.assertEqual(self.calls[-1][0][-3:], ["--vad", "--vad-model", self.state["vad_model_path"]])

    def test_conversion_failure_does_not_expose_diagnostics_and_cleans_up(self):
        def fail(arguments, timeout, step, directory, cancel_event):
            self.calls.append((arguments, timeout, step, directory))
            return subprocess.CompletedProcess(arguments, 1, stderr=b"PRIVATE ATTACHMENT CONTENT")
        with patch.object(audio, "audio_status", return_value=self.state), patch.object(audio, "_run", side_effect=fail):
            with self.assertRaises(audio.AudioError) as error:
                audio.transcribe_audio(self.source)
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertFalse(self.calls[0][3].exists())
        self.assertEqual(len(self.calls), 1)

    def test_metal_failure_retries_once_on_cpu(self):
        def fail_metal(arguments, timeout, step, directory, cancel_event):
            if step == "transcrição" and "-ng" not in arguments:
                self.calls.append((arguments, timeout, step, directory))
                return subprocess.CompletedProcess(arguments, 1, stderr=b"ggml_metal_init: error: no Metal devices found")
            return self.simulate(arguments, timeout, step, directory, cancel_event)
        with patch.object(audio, "audio_status", return_value=self.state), patch.object(audio, "_run", side_effect=fail_metal):
            result = audio.transcribe_audio(self.source)
        self.assertEqual(result["language"], "pt")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[-1][0][-1], "-ng")

    def test_general_whisper_failure_does_not_retry(self):
        def fail_model(arguments, timeout, step, directory, cancel_event):
            if step == "transcrição":
                self.calls.append((arguments, timeout, step, directory))
                return subprocess.CompletedProcess(arguments, 1, stderr=b"loaded Metal backend; invalid model PRIVATE")
            return self.simulate(arguments, timeout, step, directory, cancel_event)
        with patch.object(audio, "audio_status", return_value=self.state), patch.object(audio, "_run", side_effect=fail_model):
            with self.assertRaises(audio.AudioError) as error:
                audio.transcribe_audio(self.source)
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.calls[-1][3].exists())

    def test_empty_recognition_is_valid(self):
        self.output["transcription"] = []
        self.assertEqual(self.transcribe()["text"], "")

    def test_json_missing_offsets_or_invalid_timing_is_rejected(self):
        for segment in ({"text": "x"}, {"text": "x", "offsets": {"from": -1, "to": 100}},
                        {"text": "x", "offsets": {"from": 100, "to": 1}},
                        {"text": "x", "offsets": {"from": 0, "to": float("nan")}},
                        {"text": "x", "offsets": {"from": 0, "to": True}}):
            with self.subTest(segment=segment):
                self.output["transcription"] = [segment]
                with self.assertRaises(audio.AudioError):
                    self.transcribe()
                self.assertFalse(self.calls[-1][3].exists())

    def test_missing_or_invalid_json_is_sanitized(self):
        result = self.directory / "result.json"
        for contents in (None, "not json PRIVATE", "null", "{}", '{"transcription": [], "result": null}'):
            if contents is not None:
                result.write_text(contents)
            with self.assertRaises(audio.AudioError) as error:
                audio._parse_result(result, "auto", "fake")
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_large_json_is_rejected(self):
        result = self.directory / "result.json"
        result.write_text(json.dumps(self.output))
        with patch.object(audio, "MAX_JSON_BYTES", 1), self.assertRaises(audio.AudioError):
            audio._parse_result(result, "auto", "fake")

    def test_large_input_is_rejected_before_conversion(self):
        with patch.object(audio, "MAX_INPUT_BYTES", 2), self.assertRaises(audio.AudioError):
            self.transcribe()
        self.assertEqual(self.calls, [])

    def test_missing_empty_directory_and_symlink_are_rejected(self):
        link = self.directory / "link.ogg"
        link.symlink_to(self.source)
        empty = self.directory / "empty.ogg"
        empty.touch()
        for candidate in (link, empty, self.directory, self.directory / "missing.ogg"):
            self.source = candidate
            with self.subTest(path=candidate), self.assertRaises(audio.AudioError):
                self.transcribe()
        self.assertEqual(self.calls, [])

    def test_excessive_duration_rejected_before_whisper(self):
        with patch.object(audio, "MAX_AUDIO_SECONDS", 0.5), self.assertRaises(audio.AudioError):
            self.transcribe()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.calls[0][3].exists())

    def test_invalid_language_rejected_before_subprocess(self):
        for language in ("en --prompt secret", "PT", "", None):
            with self.subTest(language=language), self.assertRaises(audio.AudioError):
                self.transcribe(language=language)
        self.assertEqual(self.calls, [])

    def test_pre_cancelled_request_performs_no_work(self):
        event = threading.Event()
        event.set()
        with self.assertRaisesRegex(audio.AudioError, "cancelada"):
            self.transcribe(cancel_event=event)
        self.assertEqual(self.calls, [])

    def test_cancelled_transcription_removes_converted_audio(self):
        event = threading.Event()
        def cancel_during_transcription(arguments, timeout, step, directory, cancel_event):
            if step == "transcrição":
                event.set()
                audio._check_cancelled(cancel_event)
            return self.simulate(arguments, timeout, step, directory, cancel_event)
        with patch.object(audio, "audio_status", return_value=self.state), patch.object(audio, "_run", side_effect=cancel_during_transcription):
            with self.assertRaisesRegex(audio.AudioError, "cancelada"):
                audio.transcribe_audio(self.source, cancel_event=event)
        self.assertFalse(self.calls[0][3].exists())

    def test_missing_dependencies_are_actionable(self):
        self.state["available"] = False
        with self.assertRaisesRegex(audio.AudioError, "audio_status"):
            self.transcribe()
        self.assertEqual(self.calls, [])

    def test_model_discovery_prefers_existing_turbo_and_excludes_vad_and_test_weights(self):
        for name in ("ggml-base.bin", "ggml-large-v3-turbo.bin", "ggml-base.en.bin", "ggml-silero-v5.1.2.bin", "for-tests-ggml-tiny.bin"):
            (self.directory / name).write_bytes(b"fake weights")
        with patch.dict(os.environ, {}, clear=True), patch.object(audio, "_model_directories", return_value=(self.directory,)):
            self.assertEqual(audio._model().name, "ggml-large-v3-turbo.bin")
            for path in self.directory.glob("ggml-*.bin"):
                if "silero" not in path.name:
                    path.unlink()
            self.assertIsNone(audio._model())

    def test_invalid_explicit_model_does_not_fall_back(self):
        (self.directory / "ggml-base.bin").write_bytes(b"fake")
        with patch.dict(os.environ, {"WHATSAPP_MCP_WHISPER_MODEL": str(self.directory / "missing.bin")}), patch.object(audio, "_model_directories", return_value=(self.directory,)):
            self.assertIsNone(audio._model())

    def test_invalid_executable_override_does_not_fall_back(self):
        with patch.dict(os.environ, {"WHATSAPP_MCP_WHISPER_BIN": "/missing/whisper-cli"}), patch.object(audio.shutil, "which") as which:
            self.assertIsNone(audio._executable("WHATSAPP_MCP_WHISPER_BIN", "whisper-cli"))
            which.assert_not_called()

    def test_status_is_read_only_and_does_not_invoke_binaries(self):
        model = self.directory / "ggml-base.bin"
        model.write_bytes(b"fake")
        with patch.object(audio, "_executable", side_effect=["/fake/whisper-cli", "/fake/ffmpeg"]), patch.object(audio, "_model", return_value=model), patch.object(audio.sys, "platform", "darwin"), patch.object(audio.subprocess, "Popen") as popen:
            status = audio.audio_status()
        self.assertTrue(status["available"])
        self.assertTrue(status["local_only"])
        self.assertEqual(status["model_path"], str(model))
        self.assertEqual(status["missing"], [])
        popen.assert_not_called()


class SubprocessTests(unittest.TestCase):
    def test_process_uses_no_shell_and_bounded_private_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            result = audio._run([sys.executable, "-c", "import sys; sys.stderr.write('x' * 70000)"], 5, "teste", Path(directory))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(len(result.stderr), 65536)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_timeout_reaps_process_without_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "pid"
            program = "import os,time; open('pid','w').write(str(os.getpid())); time.sleep(30)"
            with self.assertRaisesRegex(audio.AudioError, "Tempo limite"):
                audio._run([sys.executable, "-c", program], 0.5, "teste", Path(directory))
            pid = int(pid_path.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_cancellation_reaps_process(self):
        event = threading.Event()
        timer = threading.Timer(0.5, event.set)
        with tempfile.TemporaryDirectory() as directory:
            timer.start()
            try:
                with self.assertRaisesRegex(audio.AudioError, "cancelada"):
                    audio._run([sys.executable, "-c", "import os,time; open('pid','w').write(str(os.getpid())); time.sleep(30)"], 10, "teste", Path(directory), event)
                pid = int((Path(directory) / "pid").read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            finally:
                timer.cancel()


if __name__ == "__main__":
    unittest.main()
