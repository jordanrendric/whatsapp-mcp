"""Real synthetic process trees and log floods; no media, models or network."""

from pathlib import Path
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest
from unittest.mock import Mock, patch

from whatsapp_mcp import audio


_CHILD = """
import os, signal, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path('child.pid').write_text(str(os.getpid()))
while True:
    time.sleep(0.05)
"""


def parent_program(normal_exit=False):
    return f"""
import os, subprocess, sys, time
from pathlib import Path
subprocess.Popen([sys.executable, '-c', {_CHILD!r}])
while not Path('child.pid').exists():
    time.sleep(0.005)
Path('parent.ready').write_text(str(os.getpid()))
if {normal_exit!r}:
    sys.exit(0)
while True:
    time.sleep(0.05)
"""


class AudioProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.cleanup_synthetic_children)

    def cleanup_synthetic_children(self):
        path = self.directory / "child.pid"
        if path.exists():
            try:
                os.kill(int(path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def wait_for_file(self, name, timeout=5):
        deadline = time.monotonic() + timeout
        path = self.directory / name
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists(), f"Synthetic process did not create {name}")
        return path

    def assert_process_gone(self, pid):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.01)
        self.fail(f"Synthetic process {pid} survived process-group cleanup")

    def assert_tree_gone(self):
        self.assert_process_gone(int(self.wait_for_file("parent.ready").read_text()))
        self.assert_process_gone(int(self.wait_for_file("child.pid").read_text()))

    def test_stop_kills_child_ignoring_term_after_parent_exits(self):
        process = subprocess.Popen([sys.executable, "-c", parent_program()], cwd=self.directory,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        self.wait_for_file("parent.ready")
        audio._stop_process(process, owns_group=True)
        self.assertEqual(process.returncode, -signal.SIGTERM)
        self.assert_tree_gone()

    def test_normal_wrapper_exit_also_cleans_its_descendants(self):
        result = audio._run([sys.executable, "-c", parent_program(normal_exit=True)],
                            5, "teste", self.directory)
        self.assertEqual(result.returncode, 0)
        self.assert_tree_gone()

    def test_timeout_cleans_parent_and_term_ignoring_child(self):
        with self.assertRaisesRegex(audio.AudioError, "Tempo limite"):
            audio._run([sys.executable, "-c", parent_program()], 0.75, "teste", self.directory)
        self.assert_tree_gone()

    def test_cancel_cleans_parent_and_term_ignoring_child(self):
        event = threading.Event()
        def cancel_when_ready():
            deadline = time.monotonic() + 3
            while not (self.directory / "parent.ready").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            event.set()
        canceller = threading.Thread(target=cancel_when_ready, daemon=True)
        canceller.start()
        try:
            with self.assertRaisesRegex(audio.AudioError, "cancelada"):
                audio._run([sys.executable, "-c", parent_program()], 5, "teste", self.directory, event)
            self.assert_tree_gone()
        finally:
            canceller.join(timeout=4)

    def test_cleanup_does_not_signal_an_unrelated_session(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            audio._run([sys.executable, "-c", parent_program(normal_exit=True)], 5, "teste", self.directory)
            self.assertIsNone(unrelated.poll())
            self.assert_tree_gone()
        finally:
            unrelated.kill()
            unrelated.wait()

    def test_already_reaped_leader_does_not_authorize_killing_a_reused_group_id(self):
        process = Mock(pid=12345, returncode=0)
        with patch.object(audio.os, "killpg") as killpg:
            audio._stop_process(process, owns_group=True)
        killpg.assert_not_called()

    def test_no_longer_owned_child_does_not_authorize_group_signals(self):
        process = Mock(pid=12345, returncode=None)
        with patch.object(audio.os, "waitid", side_effect=ChildProcessError, create=True), patch.object(audio.os, "killpg") as killpg:
            audio._stop_process(process, owns_group=True)
        killpg.assert_not_called()

    def test_large_stderr_is_drained_with_bounded_memory_and_no_disk_log(self):
        program = "import os; os.write(2, b'SYNTHETIC-PREFIX:'); chunk=b'x'*65536\nfor _ in range(512): os.write(2, chunk)"
        tracemalloc.start()
        try:
            with patch.object(audio.tempfile, "TemporaryFile", side_effect=AssertionError("Disk diagnostics are forbidden")), patch.object(audio.subprocess, "Popen", wraps=subprocess.Popen) as popen:
                result = audio._run([sys.executable, "-c", program], 10, "teste", self.directory)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stderr), audio.MAX_DIAGNOSTIC_BYTES)
        self.assertTrue(result.stderr.startswith(b"SYNTHETIC-PREFIX:"))
        self.assertLess(peak, 4 * 1024 * 1024)
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertIs(popen.call_args.kwargs["shell"], False)
        self.assertIs(popen.call_args.kwargs["start_new_session"], True)
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.PIPE)
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)

    def test_endless_stderr_flood_cannot_hide_timeout_or_leak_diagnostics(self):
        program = "import os,signal; signal.signal(signal.SIGTERM, signal.SIG_IGN)\nwhile True: os.write(2, b'SYNTHETIC-PRIVATE' * 4096)"
        before = time.monotonic()
        with self.assertRaises(audio.AudioError) as error:
            audio._run([sys.executable, "-c", program], 0.2, "teste", self.directory)
        self.assertIn("Tempo limite", str(error.exception))
        self.assertNotIn("SYNTHETIC-PRIVATE", str(error.exception))
        self.assertLess(time.monotonic() - before, 3)
        self.assertEqual(list(self.directory.iterdir()), [])


@unittest.skipUnless(sys.platform == "darwin", "Darwin native ABI fallback")
class DarwinCompatibilityTests(AudioProcessTests):
    """Exercise every process guarantee without the CPython os.waitid binding."""

    def setUp(self):
        super().setUp()
        missing_waitid = patch.object(audio.os, "waitid", None, create=True)
        missing_waitid.start()
        self.addCleanup(missing_waitid.stop)

    def test_native_probe_keeps_leader_waitable_and_preserves_echild(self):
        probe = audio._make_exit_probe()
        process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not probe(process.pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(probe(process.pid))
            self.assertTrue(probe(process.pid))
            self.assertEqual(process.wait(timeout=1), 0)
            with self.assertRaises(ChildProcessError):
                probe(process.pid)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_missing_native_capability_fails_before_starting_a_process(self):
        with patch.object(audio.ctypes, "CDLL", side_effect=OSError), patch.object(audio.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(audio.AudioError, "supervisão segura"):
                audio._run([sys.executable, "-c", "pass"], 1, "teste", self.directory)
        popen.assert_not_called()

    def test_fallback_rejects_another_platform_before_starting_a_process(self):
        with patch.object(audio.sys, "platform", "unsupported"), patch.object(audio.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(audio.AudioError, "supervisão segura"):
                audio._run([sys.executable, "-c", "pass"], 1, "teste", self.directory)
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
