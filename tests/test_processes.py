"""Real subprocess cancellation plus durable model-attempt accounting."""

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.errors import AudioError
from matter_audio_core.execution import checkpoints, execution_records, record_execution, summarize_execution
from matter_audio_core.processes import run_process, temporary_process_directory

FIXTURE = Path(__file__).with_name("process_fixture.py")


def active(pid):
    if os.name == "nt":
        from ctypes import wintypes as w
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes, api.OpenProcess.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
        api.WaitForSingleObject.argtypes, api.WaitForSingleObject.restype = [w.HANDLE, w.DWORD], w.DWORD
        api.CloseHandle.argtypes = [w.HANDLE]
        handle = api.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258
        finally:
            api.CloseHandle(handle)
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except FileNotFoundError:
        return False


class ProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.events = []

    def execute(self, args, **kwargs):
        with execution_records(self.events.append):
            return run_process([sys.executable, *args], cwd=self.root, audio_model="synthetic-test-process", **kwargs)

    def wait_file(self, name):
        deadline = time.monotonic() + 15
        path = self.root / name
        while time.monotonic() < deadline:
            if path.exists() and path.stat().st_size:
                return
            time.sleep(.03)
        self.fail(f"Fixture did not create {name}")

    def assert_tree_stopped(self):
        pids = [json.loads(path.read_text())["pid"] for path in self.root.glob("*.json")]
        deadline = time.monotonic() + 8
        while any(active(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(.03)
        self.assertTrue(pids)
        self.assertFalse([pid for pid in pids if active(pid)])

    def test_success_and_failure_record_actual_launch_and_exit(self):
        result = self.execute(["-c", "print('fixture output')"])
        self.assertEqual(result.stdout.strip(), "fixture output")
        self.assertTrue(result.report["tree_stopped"])
        launch = next(event for event in self.events if event["phase"] == "started")
        self.assertTrue(launch["gate_ownership_verified"])
        self.assertGreater(launch["gate_pid"], 0)
        self.assertEqual(summarize_execution(self.events)["audio_model_calls"], 1)
        self.events.clear()
        with self.assertRaises(AudioError) as caught:
            self.execute(["-c", "import sys; print('fixture error', file=sys.stderr); sys.exit(7)"])
        self.assertEqual(caught.exception.code, "backend_failed")
        self.assertEqual(self.events[-1]["returncode"], 7)
        self.assertIn("fixture error", self.events[-1]["stderr_tail"])
        self.assertEqual(summarize_execution(self.events)["audio_model_calls"], 1)

    def test_cancellation_stops_child_and_grandchild_before_acknowledgement(self):
        def cancel(force):
            leaf = self.root / "leaf.json"
            if leaf.exists() and leaf.stat().st_size:
                raise AudioError("job_cancelled", "Automated cancellation fixture")
        with checkpoints(cancel), self.assertRaises(AudioError) as caught:
            self.execute([str(FIXTURE), "tree", str(self.root)])
        self.assertEqual(caught.exception.code, "job_cancelled")
        self.assertTrue(self.events[-1]["tree_stopped"])
        self.assertEqual(self.events[-1]["outcome"], "cancelled")
        self.assert_tree_stopped()

    def test_normal_backend_exit_also_cleans_remaining_descendants(self):
        result = self.execute([str(FIXTURE), "short", str(self.root)])
        self.assertTrue(result.report["tree_stopped"])
        self.assert_tree_stopped()

    def test_timeout_stops_backend(self):
        with self.assertRaises(AudioError) as caught:
            self.execute([str(FIXTURE), "tree", str(self.root)], timeout_seconds=1.5)
        self.assertEqual(caught.exception.code, "backend_timeout")
        self.assert_tree_stopped()

    def test_prelaunch_cancellation_has_zero_attempts(self):
        def cancel(force):
            raise AudioError("job_cancelled", "Cancelled before launch")
        with checkpoints(cancel), self.assertRaises(AudioError):
            self.execute(["-c", "print('must not execute')"])
        self.assertEqual(self.events, [])

    def test_temporary_cleanup_retries_a_late_windows_handle_release(self):
        cleanup = tempfile.TemporaryDirectory.cleanup
        attempts = []
        def delayed(temporary):
            attempts.append(1)
            if len(attempts) == 1:
                raise PermissionError("Synthetic delayed handle release")
            return cleanup(temporary)
        with patch.object(tempfile.TemporaryDirectory, "cleanup", delayed):
            with temporary_process_directory(directory=self.root) as directory:
                (directory / "fixture.log").write_text("test")
        self.assertEqual(len(attempts), 2)
        self.assertFalse(directory.exists())

    def test_missing_executable_is_known_zero_calls(self):
        with execution_records(self.events.append), self.assertRaises(AudioError) as caught:
            run_process([str(self.root / "missing.exe")], cwd=self.root, audio_model="test")
        self.assertEqual(caught.exception.code, "backend_launch_failed")
        self.assertEqual(summarize_execution(self.events)["audio_model_calls"], 0)

    def test_owner_crash_kills_the_owned_tree(self):
        owner = subprocess.Popen([sys.executable, str(Path(__file__).with_name("process_host.py")), str(self.root)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            self.wait_file("leaf.json")
        finally:
            owner.kill()
            owner.wait(timeout=10)
        self.assert_tree_stopped()

    def test_journal_survives_result_publication_failure_without_reexecution(self):
        store = ArtifactStore(self.root / "workspace")
        publish = store._publish
        def fault(target, files):
            if target.parent.name == "objects":
                raise OSError("Synthetic final publication failure")
            return publish(target, files)
        def produce(publication):
            run_process([sys.executable, "-c", "print('fixture')"], cwd=self.root, audio_model="test")
            return {"findings": []}
        with patch.object(store, "_publish", side_effect=fault), self.assertRaises(OSError):
            store.transact("attempt", {"operation": "test"}, produce)
        evidence = store.execution_evidence("attempt")
        self.assertEqual(evidence["audio_model_calls"], 1)
        self.assertTrue(evidence["execution"]["events"][-1]["tree_stopped"])
        with self.assertRaises(AudioError) as caught:
            store.transact("attempt", {"operation": "test"}, lambda p: self.fail("Must not run again"))
        self.assertEqual(caught.exception.code, "recovery_pending")
        self.assertEqual(caught.exception.details["audio_model_calls"], 1)

    def test_crash_between_launch_intent_and_acknowledgement_is_not_reported_as_zero(self):
        store = ArtifactStore(self.root / "workspace")
        def interrupted(publication):
            record_execution({"audio_model": "synthetic", "call_id": "fixture", "phase": "launch_intent", "backend_started": None})
            raise OSError("Fixture interruption before acknowledgement")
        with self.assertRaises(OSError):
            store.transact("uncertain", {}, interrupted)
        evidence = store.execution_evidence("uncertain")
        self.assertIsNone(evidence["audio_model_calls"])
        self.assertEqual(evidence["execution"]["uncertain_launch_count"], 1)


if __name__ == "__main__":
    unittest.main()
