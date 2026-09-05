"""Local setup uses synthetic databases, fake processes, and in-memory downloads."""

from contextlib import ExitStack
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

import test_repository
from whatsapp_mcp import config, onboarding


class PrivateHomeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="whatsapp-mcp-setup-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "Another User with spaces"
        self.home.mkdir()
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(Path, "home", return_value=self.home))
        self.patches.enter_context(patch.dict(os.environ, {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}, clear=True))


class ConfigurationTests(PrivateHomeTests):
    def test_missing_configuration_is_read_only_and_empty(self):
        self.assertEqual(config.load_config(), {})
        self.assertIsNone(config.setting("WHATSAPP_MCP_DB_PATH"))
        self.assertFalse(config.config_directory().exists())

    def test_configuration_is_private_atomic_and_outside_plugin(self):
        values = {"db_path": str(self.home / "WhatsApp data/ChatStorage.sqlite")}
        config.save_config(values)
        self.assertEqual(config.load_config(), values)
        self.assertEqual(stat.S_IMODE(config.config_path().stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(config.config_directory().stat().st_mode), 0o700)
        self.assertEqual(config.config_path().parent, self.home / "Library/Application Support/whatsapp-mcp")
        self.assertEqual(list(config.config_directory().iterdir()), [config.config_path()])

    def test_environment_overrides_saved_value_including_empty_value(self):
        config.save_config({"db_path": "/saved/ChatStorage.sqlite"})
        self.assertEqual(config.setting("WHATSAPP_MCP_DB_PATH"), "/saved/ChatStorage.sqlite")
        with patch.dict(os.environ, {"WHATSAPP_MCP_DB_PATH": "/explicit/ChatStorage.sqlite"}):
            self.assertEqual(config.setting("WHATSAPP_MCP_DB_PATH"), "/explicit/ChatStorage.sqlite")
        with patch.dict(os.environ, {"WHATSAPP_MCP_DB_PATH": ""}):
            self.assertEqual(config.setting("WHATSAPP_MCP_DB_PATH"), "")

    def test_failed_replace_keeps_previous_configuration_and_cleans_temporary(self):
        config.save_config({"db_path": "/before/ChatStorage.sqlite"})
        with patch.object(config.os, "replace", side_effect=OSError("private diagnostic")):
            with self.assertRaises(config.ConfigError) as failure:
                config.save_config({"db_path": "/after/ChatStorage.sqlite"})
        self.assertNotIn("private diagnostic", str(failure.exception))
        self.assertEqual(config.load_config()["db_path"], "/before/ChatStorage.sqlite")
        self.assertEqual(list(config.config_directory().iterdir()), [config.config_path()])

    def test_unknown_nonabsolute_and_invalid_settings_are_rejected(self):
        for values in ({"url": "https://example.com"}, {"db_path": "relative.sqlite"}, {"db_path": 1}, {"db_path": "/path\x00bad"}):
            with self.subTest(values=values), self.assertRaises(config.ConfigError):
                config.save_config(values)
        self.assertFalse(config.config_directory().exists())

    def test_invalid_large_and_unsupported_json_are_sanitized(self):
        config.private_directory(config.config_directory())
        for contents in ("PRIVATE invalid", "[]", '{"version": true}', '{"version": 2}', '{"version": 1, "db_path":"relative"}', "x" * (config.MAX_CONFIG_BYTES + 1)):
            config.config_path().write_text(contents)
            with self.subTest(contents=contents[:30]), self.assertRaises(config.ConfigError) as failure:
                config.load_config()
            self.assertNotIn("PRIVATE", str(failure.exception))

    def test_symlink_and_fifo_configuration_are_rejected(self):
        config.private_directory(config.config_directory())
        other = self.root / "other.json"
        other.write_text('{"version": 1}')
        config.config_path().symlink_to(other)
        with self.assertRaises(config.ConfigError):
            config.load_config()
        config.config_path().unlink()
        os.mkfifo(config.config_path())
        with self.assertRaises(config.ConfigError):
            config.load_config()

    def test_symlink_ancestor_cannot_redirect_config_into_whatsapp_storage(self):
        outside = self.root / "synthetic-whatsapp-storage"
        outside.mkdir()
        (self.home / "Library").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(config.ConfigError):
            config.save_config({"db_path": "/synthetic/ChatStorage.sqlite"})
        with self.assertRaises(config.ConfigError):
            config.load_config()
        self.assertEqual(list(outside.iterdir()), [])

    def test_application_support_symlink_is_rejected_without_changing_target(self):
        library = self.home / "Library"
        library.mkdir()
        outside = self.root / "outside-data"
        outside.mkdir(mode=0o755)
        (library / "Application Support").symlink_to(outside, target_is_directory=True)
        before = stat.S_IMODE(outside.stat().st_mode)
        with self.assertRaises(config.ConfigError):
            config.private_directory(config.config_directory() / "models")
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), before)
        self.assertEqual(list(outside.iterdir()), [])

    def test_setup_does_not_chmod_home_or_existing_library_directories(self):
        library = self.home / "Library"
        support = library / "Application Support"
        support.mkdir(parents=True)
        for directory in (self.home, library, support):
            directory.chmod(0o755)
        config.save_config({})
        self.assertEqual([stat.S_IMODE(directory.stat().st_mode) for directory in (self.home, library, support)], [0o755] * 3)

    def test_wrong_owner_and_relative_home_are_rejected(self):
        with patch.object(config.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises(config.ConfigError):
                config.save_config({})
        with patch.object(Path, "home", return_value=Path("relative-home")):
            with self.assertRaises(config.ConfigError):
                config.save_config({})


class OnboardingTests(PrivateHomeTests):
    def setUp(self):
        super().setUp()
        self.fixture = test_repository.RepositoryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.patches.enter_context(patch.object(onboarding.sys, "platform", "darwin"))
        self.candidates = self.patches.enter_context(patch.object(onboarding.Repository, "discover_candidates", return_value=[self.fixture.db]))
        self.state = {
            "available": False, "supported_platform": True,
            "ffmpeg_bin": None, "whisper_bin": None, "model_path": None,
            "missing": ["ffmpeg", "whisper-cli", "whisper_model"],
        }
        self.audio_status = self.patches.enter_context(patch.object(onboarding.audio, "audio_status", side_effect=lambda: self.state.copy()))

    def test_status_reads_schema_but_creates_no_config_and_runs_no_setup(self):
        with patch.object(onboarding.audio, "_run") as process, patch.object(onboarding, "_download_model") as download, patch.object(onboarding.Repository, "get_messages") as messages:
            result = onboarding.onboarding_status()
        self.assertTrue(result["database"]["available"])
        self.assertEqual(result["database_candidates"][0]["index"], 1)
        self.assertFalse(config.config_directory().exists())
        process.assert_not_called()
        download.assert_not_called()
        messages.assert_not_called()

    def test_text_setup_validates_database_saves_paths_without_audio_operations(self):
        before = self.fixture.db.read_bytes()
        with patch.object(onboarding, "_install_binaries") as install, patch.object(onboarding, "_download_model") as download:
            result = onboarding.setup()
        self.assertTrue(result["text_ready"])
        self.assertFalse(result["audio_requested"])
        self.assertFalse(result["whatsapp_modified"])
        self.assertEqual(config.load_config()["db_path"], str(self.fixture.db.resolve()))
        self.assertEqual(config.load_config()["media_root"], str(self.fixture.media.resolve()))
        self.assertEqual(self.fixture.db.read_bytes(), before)
        install.assert_not_called()
        download.assert_not_called()

    def test_multiple_accounts_require_explicit_selection(self):
        self.candidates.return_value = [self.fixture.db, self.root / "Second account.sqlite"]
        with self.assertRaisesRegex(onboarding.OnboardingError, "Multiple"):
            onboarding.setup()
        self.assertFalse(config.config_path().exists())
        result = onboarding.setup(database_index=1)
        self.assertTrue(result["text_ready"])

    def test_bad_index_and_conflicting_environment_are_rejected(self):
        for value in (0, -1, 2, True, "1"):
            with self.subTest(value=value), self.assertRaises(onboarding.OnboardingError):
                onboarding.setup(database_index=value)
        with patch.dict(os.environ, {"WHATSAPP_MCP_DB_PATH": "/other/account.sqlite"}):
            with self.assertRaisesRegex(onboarding.OnboardingError, "overrides"):
                onboarding.setup(database_index=1)
        self.assertFalse(config.config_path().exists())

    def test_previously_selected_account_is_not_guessed_again(self):
        config.save_config({"db_path": str(self.fixture.db), "media_root": str(self.fixture.media)})
        self.candidates.return_value = [self.root / "Another.sqlite", self.fixture.db]
        self.assertTrue(onboarding.setup()["text_ready"])
        self.candidates.assert_not_called()

    def test_switching_accounts_does_not_reuse_previous_media_root(self):
        config.save_config({"db_path": "/previous/ChatStorage.sqlite", "media_root": "/previous/private-media"})
        onboarding.setup(database_index=1)
        self.assertEqual(config.load_config()["media_root"], str(self.fixture.media.resolve()))

    def test_schema_failure_prevents_configuration_and_package_install(self):
        self.fixture.writer.execute("DROP TABLE ZWAMESSAGE")
        self.fixture.writer.commit()
        with patch.object(onboarding, "_install_binaries") as install:
            with self.assertRaisesRegex(onboarding.OnboardingError, "schema"):
                onboarding.setup(enable_audio=True)
        self.assertFalse(config.config_path().exists())
        install.assert_not_called()

    def test_optional_audio_reuses_existing_tools_and_model(self):
        self.state.update(available=True, ffmpeg_bin="/existing/ffmpeg", whisper_bin="/existing/whisper-cli", model_path="/existing/ggml-base.bin", missing=[])
        with patch.object(onboarding, "_brew") as brew, patch.object(onboarding, "_download_model") as download:
            result = onboarding.setup(enable_audio=True)
        self.assertTrue(result["audio"]["available"])
        self.assertEqual(config.load_config()["whisper_model"], "/existing/ggml-base.bin")
        brew.assert_not_called()
        download.assert_not_called()

    def test_model_download_path_is_saved_only_after_success(self):
        self.state.update(ffmpeg_bin="/existing/ffmpeg", whisper_bin="/existing/whisper-cli")
        target = config.config_directory() / "models/ggml-base.bin"
        with patch.object(onboarding, "_download_model", return_value=target) as download:
            onboarding.setup(enable_audio=True)
        download.assert_called_once()
        self.assertEqual(config.load_config()["whisper_model"], str(target))

    def test_download_failure_preserves_working_text_setup(self):
        self.state.update(ffmpeg_bin="/existing/ffmpeg", whisper_bin="/existing/whisper-cli")
        with patch.object(onboarding, "_download_model", side_effect=onboarding.OnboardingError("network unavailable")):
            with self.assertRaises(onboarding.OnboardingError):
                onboarding.setup(enable_audio=True)
        self.assertEqual(config.load_config()["db_path"], str(self.fixture.db.resolve()))
        self.assertNotIn("whisper_model", config.load_config())

    def test_brew_installs_only_missing_fixed_formulas_without_shell_or_sudo(self):
        self.state["ffmpeg_bin"] = "/existing/ffmpeg"
        with patch.object(onboarding, "_brew", return_value="/opt/homebrew/bin/brew"), patch.object(onboarding.audio, "_run", return_value=subprocess.CompletedProcess([], 0)) as process:
            onboarding._install_binaries(self.state, None)
        arguments, timeout, _, directory, event = process.call_args.args
        self.assertEqual(arguments[-3:], ["/opt/homebrew/bin/brew", "install", "whisper-cpp"])
        self.assertEqual(arguments[0], "/usr/bin/env")
        self.assertIn("HOMEBREW_NO_AUTO_UPDATE=1", arguments)
        self.assertIn("HOMEBREW_NO_ANALYTICS=1", arguments)
        self.assertNotIn("sudo", arguments)
        self.assertEqual(timeout, onboarding.INSTALL_TIMEOUT_SECONDS)
        self.assertFalse(directory.exists())
        self.assertIsNone(event)

    def test_no_homebrew_does_not_install_homebrew_or_download_models(self):
        with patch.object(onboarding, "_brew", return_value=None), patch.object(onboarding, "_download_model") as download, patch.object(onboarding.audio, "_run") as process:
            with self.assertRaisesRegex(onboarding.OnboardingError, "does not install Homebrew"):
                onboarding.setup(enable_audio=True)
        self.assertEqual(config.load_config()["db_path"], str(self.fixture.db.resolve()))
        process.assert_not_called()
        download.assert_not_called()

    def test_cancellation_and_unsupported_platform_do_not_write(self):
        event = threading.Event()
        event.set()
        with self.assertRaisesRegex(onboarding.OnboardingError, "cancelled"):
            onboarding.setup(cancel_event=event)
        with patch.object(onboarding.sys, "platform", "linux"):
            with self.assertRaisesRegex(onboarding.OnboardingError, "macOS"):
                onboarding.setup()
            self.assertFalse(onboarding.onboarding_status()["supported_platform"])
        self.assertFalse(config.config_path().exists())

    def test_concurrent_setup_is_rejected_without_work(self):
        onboarding._SETUP_LOCK.acquire()
        try:
            with self.assertRaisesRegex(onboarding.OnboardingError, "already running"):
                onboarding.setup()
        finally:
            onboarding._SETUP_LOCK.release()
        self.candidates.assert_not_called()
        self.assertFalse(config.config_path().exists())

    def test_cancelled_schema_check_does_not_save_configuration(self):
        event = threading.Event()
        original_status = onboarding.Repository.status
        def status_and_cancel(repository):
            result = original_status(repository)
            event.set()
            return result
        with patch.object(onboarding.Repository, "status", status_and_cancel):
            with self.assertRaisesRegex(onboarding.OnboardingError, "cancelled"):
                onboarding.setup(cancel_event=event)
        self.assertFalse(config.config_path().exists())

    def test_brew_diagnostics_are_not_exposed(self):
        result = subprocess.CompletedProcess([], 1, stderr=b"PRIVATE HOMEBREW DIAGNOSTICS")
        with patch.object(onboarding, "_brew", return_value="/trusted/brew"), patch.object(onboarding.audio, "_run", return_value=result):
            with self.assertRaises(onboarding.OnboardingError) as failure:
                onboarding._install_binaries(self.state, None)
        self.assertNotIn("PRIVATE", str(failure.exception))


class ModelDownloadTests(PrivateHomeTests):
    def setUp(self):
        super().setUp()
        self.content = b"synthetic model weights, never executed"
        self.patches.enter_context(patch.object(onboarding, "MODEL_BYTES", len(self.content)))
        self.patches.enter_context(patch.object(onboarding, "MODEL_SHA256", hashlib.sha256(self.content).hexdigest()))
        self.response = io.BytesIO(self.content)
        self.response.headers = {"Content-Length": str(len(self.content))}
        self.response.geturl = lambda: onboarding.MODEL_URL
        self.opener = SimpleNamespace(open=lambda *args, **kwargs: self.response)
        self.open = self.patches.enter_context(patch.object(onboarding.urllib.request, "build_opener", return_value=self.opener))

    def assert_no_partial_model(self):
        directory = config.config_directory() / "models"
        self.assertEqual(list(directory.iterdir()), [])

    def test_verified_fixed_url_download_is_private_and_reused(self):
        result = onboarding._download_model()
        self.assertEqual(result.read_bytes(), self.content)
        self.assertEqual(stat.S_IMODE(result.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(result.parent.stat().st_mode), 0o700)
        self.assertEqual(list(result.parent.iterdir()), [result])
        self.open.reset_mock()
        self.assertEqual(onboarding._download_model(), result)
        self.open.assert_not_called()

    def test_hash_mismatch_does_not_install_unverified_data(self):
        with patch.object(onboarding, "MODEL_SHA256", "0" * 64):
            with self.assertRaisesRegex(onboarding.OnboardingError, "SHA-256"):
                onboarding._download_model()
        self.assert_no_partial_model()

    def test_size_limit_is_enforced_without_content_length(self):
        self.response.headers = {}
        with patch.object(onboarding, "MODEL_BYTES", len(self.content) - 1):
            with self.assertRaisesRegex(onboarding.OnboardingError, "exceeded"):
                onboarding._download_model()
        self.assert_no_partial_model()

    def test_content_length_mismatch_is_rejected_before_body_read(self):
        self.response.headers = {"Content-Length": "999999999999"}
        with self.assertRaisesRegex(onboarding.OnboardingError, "size"):
            onboarding._download_model()
        self.assert_no_partial_model()

    def test_network_failure_is_sanitized_and_partial_file_removed(self):
        self.opener.open = lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.URLError("PRIVATE diagnostics"))
        with self.assertRaises(onboarding.OnboardingError) as failure:
            onboarding._download_model()
        self.assertNotIn("PRIVATE", str(failure.exception))
        self.assert_no_partial_model()

    def test_incomplete_http_stream_is_sanitized_and_partial_file_removed(self):
        self.response.read1 = lambda size: (_ for _ in ()).throw(http.client.IncompleteRead(b"PRIVATE partial response"))
        with self.assertRaises(onboarding.OnboardingError) as failure:
            onboarding._download_model()
        self.assertNotIn("PRIVATE", str(failure.exception))
        self.assert_no_partial_model()

    def test_untrusted_response_host_and_redirects_are_rejected(self):
        self.response.geturl = lambda: "https://example.com/weights.bin"
        with self.assertRaisesRegex(onboarding.OnboardingError, "untrusted"):
            onboarding._download_model()
        self.assert_no_partial_model()
        for url in ("http://huggingface.co/model", "https://huggingface.co.evil.example/model", "file:///etc/passwd", "https://user:password@huggingface.co/model", "https://huggingface.co:444/model"):
            self.assertFalse(onboarding._trusted_download_url(url))
            with self.assertRaises(onboarding.OnboardingError):
                onboarding._TrustedRedirects().redirect_request(None, None, 302, "", {}, url)
        self.assertTrue(onboarding._trusted_download_url("https://cas-bridge.xethub.hf.co/model"))

    def test_precancelled_download_creates_no_files_or_requests(self):
        event = threading.Event()
        event.set()
        with self.assertRaisesRegex(onboarding.OnboardingError, "cancelled"):
            onboarding._download_model(event)
        self.assertFalse(config.config_directory().exists())
        self.open.assert_not_called()

    def test_download_deadline_cleans_partial_file(self):
        with patch.object(onboarding.time, "monotonic", side_effect=[0, onboarding.DOWNLOAD_TIMEOUT_SECONDS + 1]):
            with self.assertRaisesRegex(onboarding.OnboardingError, "time limit"):
                onboarding._download_model()
        self.assert_no_partial_model()

    def test_cancelled_inflight_download_cleans_partial_file(self):
        event = threading.Event()
        original_read = self.response.read1
        def read_and_cancel(size):
            value = original_read(size)
            event.set()
            return value
        self.response.read1 = read_and_cancel
        with self.assertRaisesRegex(onboarding.OnboardingError, "cancelled"):
            onboarding._download_model(event)
        self.assert_no_partial_model()


if __name__ == "__main__":
    unittest.main()
