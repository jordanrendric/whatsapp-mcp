"""Machine-independent path and launcher checks, using only temporary fixtures."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from whatsapp_mcp import audio
from whatsapp_mcp.repository import Repository


class DiscoveryPortabilityTests(unittest.TestCase):
    def setUp(self):
        configuration = patch("whatsapp_mcp.config.load_config", return_value={})
        configuration.start()
        self.addCleanup(configuration.stop)
        self.temporary = tempfile.TemporaryDirectory(prefix="whatsapp-mcp-portability-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "Home with spaces and á"
        self.home.mkdir()

    def test_database_discovery_uses_current_home_with_spaces(self):
        database = self.home / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"
        database.parent.mkdir(parents=True)
        database.touch()
        with patch.dict(os.environ, {}, clear=True), patch("whatsapp_mcp.repository.Path.home", return_value=self.home), patch("whatsapp_mcp.repository.sys.platform", "darwin"):
            repository = Repository()
        self.assertEqual(repository.db_path, database.resolve())
        self.assertEqual(repository.media_root, (database.parent / "Message").resolve())

    def test_model_discovery_uses_current_home_without_optional_plugins(self):
        # Highest-priority speech model, so unrelated Homebrew models cannot win.
        model = self.home / ".cache/whisper.cpp/ggml-large-v3-turbo.bin"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"synthetic model, never executed")
        with patch.dict(os.environ, {}, clear=True), patch.object(audio.Path, "home", return_value=self.home):
            self.assertEqual(audio._model(), model.resolve())
        self.assertFalse((self.home / ".claude-video-vision").exists())

    def test_binary_discovery_accepts_path_outside_homebrew(self):
        executable = self.home / "local tools/whisper-cli"
        executable.parent.mkdir()
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o700)
        with patch.dict(os.environ, {"PATH": str(executable.parent)}, clear=True):
            self.assertEqual(audio._executable("WHATSAPP_MCP_WHISPER_BIN", "whisper-cli"), str(executable.resolve()))


@unittest.skipUnless(sys.platform == "darwin", "The production launcher is macOS-only")
class LauncherPortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="whatsapp-mcp-launcher-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.plugin = self.root / "Copied plugin with spaces"
        (self.plugin / "scripts").mkdir(parents=True)
        original = Path(__file__).resolve().parents[1]
        shutil.copyfile(original / "scripts/run.sh", self.plugin / "scripts/run.sh")
        shutil.copyfile(original / "uv.lock", self.plugin / "uv.lock")
        self.home = self.root / "Another User"
        self.home.mkdir()
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.capture = self.root / "uv-call.json"
        fake_uv = self.tools / "uv"
        fake_uv.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['WHATSAPP_TEST_CAPTURE']).write_text(json.dumps({\n"
            "  'args': sys.argv[1:],\n"
            "  'venv': os.environ.get('UV_PROJECT_ENVIRONMENT'),\n"
            "  'bytecode': os.environ.get('PYTHONDONTWRITEBYTECODE'),\n"
            "}))\n"
        )
        fake_uv.chmod(0o700)
        self.environment = {
            "HOME": str(self.home),
            "PATH": str(self.tools) + ":/usr/bin:/bin",
            "WHATSAPP_TEST_CAPTURE": str(self.capture),
        }

    def launch(self, **overrides):
        subprocess.run(
            ["/bin/sh", str(self.plugin / "scripts/run.sh"), "--check"],
            cwd=self.root, env={**self.environment, **overrides},
            check=True, capture_output=True, text=True, timeout=10,
        )
        return json.loads(self.capture.read_text())

    def test_launcher_relocates_plugin_and_uses_current_users_cache(self):
        result = self.launch()
        lock_hash = hashlib.sha256((self.plugin / "uv.lock").read_bytes()).hexdigest()[:16]
        self.assertEqual(result["venv"], str(self.home / ".cache/whatsapp-mcp/venvs" / lock_hash))
        arguments = result["args"]
        self.assertEqual(arguments[arguments.index("--directory") + 1], str(self.plugin))
        self.assertEqual(arguments[-3:], ["-B", "scripts/server.py", "--check"])
        self.assertIn("--locked", arguments)
        self.assertIn("--no-env-file", arguments)
        self.assertEqual(result["bytecode"], "1")
        self.assertFalse((self.plugin / ".venv").exists())

    def test_launcher_honors_xdg_cache_and_explicit_environment(self):
        cache = self.root / "Cache with spaces"
        result = self.launch(XDG_CACHE_HOME=str(cache))
        self.assertTrue(Path(result["venv"]).is_relative_to(cache))
        custom_environment = self.root / "Chosen runtime"
        result = self.launch(XDG_CACHE_HOME=str(cache), WHATSAPP_MCP_VENV=str(custom_environment))
        self.assertEqual(result["venv"], str(custom_environment))


if __name__ == "__main__":
    unittest.main()
