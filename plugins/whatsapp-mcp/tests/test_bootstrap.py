"""Launcher bootstrap tests use copied scripts and synthetic local archives only."""

import hashlib
import io
import json
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest


_PINNED_DIGESTS = (
    "546f7f8a6c70ff13a3a9d2bc958db3427298cebf3e0cb756f9177133b7068843",
    "4c9f52262a14da336e4a42ed24992d12d0c956acde87619e4611d321dffa602b",
)


@unittest.skipUnless(sys.platform == "darwin", "Production bootstrap uses macOS system tools")
class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="whatsapp-mcp-bootstrap-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "A home with spaces and á"
        self.home.mkdir()
        self.tools = self.root / "mock tools"
        self.tools.mkdir()
        self.plugin = self.root / "Relocated plugin with spaces"
        (self.plugin / "scripts").mkdir(parents=True)
        self.capture = self.root / "curl-calls.json"
        self.uv_capture = self.root / "uv-call.json"
        self.fixture = self.root / "synthetic-uv.tar.gz"
        self.binary = (
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['BOOTSTRAP_TEST_UV_CAPTURE']).write_text(json.dumps({\n"
            "'args': sys.argv[1:], 'venv': os.environ.get('UV_PROJECT_ENVIRONMENT')}))\n"
            "print('SYNTHETIC_MCP_STDOUT')\n"
        ).encode()
        self.write_archive()
        self.fake_curl = self.make_tool("curl", """
import json, os, pathlib, shutil, stat, sys
args = sys.argv[1:]
output = pathlib.Path(args[args.index('--output') + 1])
capture = pathlib.Path(os.environ['BOOTSTRAP_TEST_CURL_CAPTURE'])
calls = json.loads(capture.read_text()) if capture.exists() else []
calls.append({'args': args, 'directory_mode': stat.S_IMODE(output.parent.stat().st_mode)})
capture.write_text(json.dumps(calls))
mode = os.environ.get('BOOTSTRAP_TEST_CURL_MODE', 'ok')
if mode == 'network_failure':
    output.write_bytes(b'synthetic partial download')
    sys.exit(7)
if mode == 'wrong_hash':
    output.write_bytes(b'synthetic invalid archive')
elif mode == 'oversized':
    with output.open('wb') as handle:
        handle.truncate(67108865)
else:
    shutil.copyfile(os.environ['BOOTSTRAP_TEST_ARCHIVE'], output)
""")
        self.fake_uname = self.make_tool("uname", """
import os, sys
print('Darwin' if sys.argv[1] == '-s' else os.environ.get('BOOTSTRAP_TEST_ARCH', 'arm64'))
""")
        self.homebrew = self.root / "homebrew" / "uv"
        self.intel_homebrew = self.root / "intel-homebrew" / "uv"
        self.original = Path(__file__).resolve().parents[1]
        self.environment = {
            "HOME": str(self.home), "PATH": "/usr/bin:/bin",
            "BOOTSTRAP_TEST_CURL_CAPTURE": str(self.capture),
            "BOOTSTRAP_TEST_UV_CAPTURE": str(self.uv_capture),
            "BOOTSTRAP_TEST_ARCHIVE": str(self.fixture),
        }
        self.copy_scripts()

    def make_tool(self, name, body):
        path = self.tools / name
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o700)
        return path

    def write_archive(self, expected_member=True):
        with tarfile.open(self.fixture, "w:gz") as archive:
            members = ["uv-aarch64-apple-darwin/uv", "uv-x86_64-apple-darwin/uv"] if expected_member else ["unexpected/file"]
            # A tar extraction into the filesystem would create this unwanted file.
            members.append("unexpected-side-file")
            for member in members:
                metadata = tarfile.TarInfo(member)
                metadata.size = len(self.binary)
                metadata.mode = 0o755
                archive.addfile(metadata, io.BytesIO(self.binary))

    def copy_scripts(self):
        fixture_digest = hashlib.sha256(self.fixture.read_bytes()).hexdigest()
        for name in ("run.sh", "bootstrap-uv.sh"):
            source = (self.original / "scripts" / name).read_text()
            source = source.replace("/opt/homebrew/bin/uv", shlex.quote(str(self.homebrew)))
            source = source.replace("/usr/local/bin/uv", shlex.quote(str(self.intel_homebrew)))
            if name == "bootstrap-uv.sh":
                # Only this isolated test copy trusts the synthetic archive.
                # Production has no environment override for digests or download URLs.
                for digest in _PINNED_DIGESTS:
                    self.assertIn(digest, source)
                    source = source.replace(digest, fixture_digest)
                source = source.replace("/usr/bin/curl", shlex.quote(str(self.fake_curl)))
                source = source.replace("/usr/bin/uname", shlex.quote(str(self.fake_uname)))
            (self.plugin / "scripts" / name).write_text(source)
        (self.plugin / "uv.lock").write_text("synthetic lock file\n")

    @property
    def runtime(self):
        return self.home / "Library/Application Support/whatsapp-mcp"

    @property
    def uv(self):
        return self.runtime / "bin/uv"

    def launch(self, script="bootstrap-uv.sh", **overrides):
        return subprocess.run(["/bin/sh", str(self.plugin / "scripts" / script)],
                              env={**self.environment, **overrides}, cwd=self.root,
                              capture_output=True, text=True, timeout=15)

    def assert_clean(self):
        self.assertEqual(list(self.runtime.glob(".uv-download.*")), [])

    def test_verified_arm_archive_installs_privately_and_outputs_only_path(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(self.uv) + "\n")
        self.assertIn("preparando uv 0.12.3", result.stderr)
        self.assertEqual(self.uv.read_bytes(), self.binary)
        for path in (self.runtime, self.uv.parent, self.uv):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        calls = json.loads(self.capture.read_text())
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["directory_mode"], 0o700)
        args = calls[0]["args"]
        self.assertEqual(args[0], "--disable")
        self.assertEqual(args[args.index("--proto") + 1], "=https")
        self.assertEqual(args[args.index("--proto-redir") + 1], "=https")
        self.assertEqual(args[args.index("--max-time") + 1], "180")
        self.assertEqual(args[args.index("--max-filesize") + 1], "67108864")
        self.assertEqual(args[args.index("--url") + 1], "https://github.com/astral-sh/uv/releases/download/0.12.3/uv-aarch64-apple-darwin.tar.gz")
        self.assertFalse((self.root / "unexpected-side-file").exists())
        self.assertEqual(list(self.runtime.iterdir()), [self.runtime / "bin"])
        self.assert_clean()

    def test_intel_selects_the_pinned_intel_artifact(self):
        result = self.launch(BOOTSTRAP_TEST_ARCH="x86_64")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(self.capture.read_text())[0]["args"]
        self.assertTrue(args[args.index("--url") + 1].endswith("/uv-x86_64-apple-darwin.tar.gz"))
        self.assert_clean()

    def test_existing_private_uv_is_reused_without_download(self):
        self.uv.parent.mkdir(parents=True)
        self.uv.write_bytes(self.binary)
        self.uv.chmod(0o700)
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(self.uv) + "\n")
        self.assertFalse(self.capture.exists())

    def test_existing_path_uv_precedes_homebrew_and_private_install(self):
        existing = self.make_tool("uv", "pass\n")
        result = self.launch(PATH=str(self.tools) + ":/usr/bin:/bin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(existing) + "\n")
        self.assertFalse(self.runtime.exists())
        self.assertFalse(self.capture.exists())

    def test_existing_homebrew_uv_precedes_private_install(self):
        self.homebrew.parent.mkdir()
        self.homebrew.write_bytes(self.binary)
        self.homebrew.chmod(0o700)
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(self.homebrew) + "\n")
        self.assertFalse(self.runtime.exists())
        self.assertFalse(self.capture.exists())

    def test_wrong_hash_never_installs_or_leaves_downloads(self):
        result = self.launch(BOOTSTRAP_TEST_CURL_MODE="wrong_hash")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.uv.exists())
        self.assert_clean()

    def test_network_failure_removes_partial_download(self):
        result = self.launch(BOOTSTRAP_TEST_CURL_MODE="network_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conexão", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.uv.exists())
        self.assert_clean()

    def test_download_limit_also_applies_at_the_operating_system(self):
        result = self.launch(BOOTSTRAP_TEST_CURL_MODE="oversized")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.uv.exists())
        self.assert_clean()

    def test_missing_expected_archive_member_cleans_up(self):
        self.write_archive(expected_member=False)
        self.copy_scripts()
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.uv.exists())
        self.assert_clean()

    def test_runtime_symlink_cannot_redirect_writes_into_whatsapp_fixture(self):
        whatsapp = self.home / "WhatsApp data fixture"
        whatsapp.mkdir()
        protected = whatsapp / "ChatStorage.sqlite"
        protected.write_bytes(b"synthetic database, never opened")
        self.runtime.parent.mkdir(parents=True)
        self.runtime.symlink_to(whatsapp, target_is_directory=True)
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("link simbólico", result.stderr)
        self.assertEqual(list(whatsapp.iterdir()), [protected])
        self.assertEqual(protected.read_bytes(), b"synthetic database, never opened")
        self.assertFalse(self.capture.exists())

    def test_private_binary_symlink_is_rejected_without_modifying_target(self):
        target = self.root / "unrelated-file"
        target.write_bytes(b"unchanged")
        self.uv.parent.mkdir(parents=True)
        self.uv.symlink_to(target)
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("link simbólico", result.stderr)
        self.assertEqual(target.read_bytes(), b"unchanged")
        self.assertFalse(self.capture.exists())

    def test_home_symlink_is_rejected_before_creating_runtime_directories(self):
        alias = self.root / "Alias home"
        alias.symlink_to(self.home, target_is_directory=True)
        result = self.launch(HOME=str(alias))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.runtime.exists())
        self.assertFalse(self.capture.exists())

    def test_launcher_bootstraps_and_keeps_setup_logs_out_of_mcp_stdout(self):
        result = self.launch(script="run.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "SYNTHETIC_MCP_STDOUT\n")
        self.assertIn("preparando uv", result.stderr)
        arguments = json.loads(self.uv_capture.read_text())["args"]
        self.assertIn("--locked", arguments)
        self.assertIn("--no-env-file", arguments)
        self.assertEqual(arguments[arguments.index("--directory") + 1], str(self.plugin))
        self.assertFalse((self.plugin / ".venv").exists())
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
