"""Managed execution failure, concurrency, migration and batch contracts."""

from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from array import array
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from matter_audio_core.actions import ActionService, Registry
from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.contracts import canonical, request
from matter_audio_core.errors import AudioError
from matter_audio_core.execution import checkpoint, checkpoints, job_lock
from matter_audio_core.jobs import JobService
from matter_audio_core.media import PCM, Q24, encode_wav, gain, inspect, sample_bytes
from matter_audio_core.session_db import DATABASE_NAME, DATABASE_VERSION, MIGRATIONS
from matter_audio_core.sessions import SessionService


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="matter jobs # ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.wav"
        self.source.write_bytes(encode_wav(PCM(sample_bytes(array("h", [-12000, 12000] * 800)), 8000, 1)))
        self.store = ArtifactStore(self.root / "workspace")
        self.asset = self.store.import_wav(self.source, "import")["outputs"][0]["asset_id"]
        self.sessions = SessionService(self.store)
        self.sessions.mutate("create", {"schema": "matter-session-create/v1", "request_id": "session",
                                       "session_id": "main", "name": "Test", "asset_id": self.asset})
        self.jobs = JobService(self.store)

    def spec(self, job_id="edit", db=-3):
        return {"schema": "matter-job-submit/v1", "request_id": "submit-" + job_id,
                "job_id": job_id, "session_id": "main",
                "action": {"operation": "gain/v1", "inputs": [self.asset], "parameters": {"db": db}}}

    def mutate(self, operation, *, job_id="edit", attempt=1, request_id=None):
        value = {"schema": "matter-job-" + operation + "/v1", "request_id": request_id or operation,
                 "job_id": job_id, "expected_attempt": attempt}
        return self.jobs.mutate(operation, value)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(AudioError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def worker(self, mode, job_id="edit"):
        signals = self.root / (mode + "-" + job_id)
        signals.mkdir()
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("job_worker.py")),
                                    str(self.store.root), job_id, mode, str(signals)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=10)

        self.addCleanup(cleanup)
        return process, signals

    def ready(self, process, signals):
        deadline = time.monotonic() + 10
        while not (signals / "ready").exists():
            if process.poll() is not None:
                self.fail(process.communicate()[1].decode())
            if time.monotonic() > deadline:
                self.fail("Worker did not reach test barrier")
            time.sleep(0.01)

    def test_completed_job_reopens_with_one_result_and_stable_submission_receipt(self):
        submitted = self.jobs.mutate("submit", self.spec())
        self.assertEqual(submitted["job"]["state"], "queued")
        self.assertEqual(self.jobs.show("edit")["resolution"]["effective_parameters"]["db"], -3)
        result = self.jobs.run("edit")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.jobs.run("edit"), result)
        self.assertEqual(JobService(self.store).show("edit"), result)
        self.assertEqual(self.jobs.mutate("submit", self.spec()), submitted)
        self.assertEqual(self.sessions.request_status("submit-edit"), submitted)
        self.assertEqual(self.sessions.show("main")["session"]["head_revision"], 1)
        self.assertEqual(self.sessions.context("main")["jobs"]["items"][0]["state"], "succeeded")
        self.assertEqual(len(list((self.store.root / "objects").iterdir())), 2)

    def test_guarded_selection_and_result_registration_commit_once(self):
        spec = self.spec()
        spec["selection"] = {"expected_revision": 1, "output_index": 0}
        self.jobs.mutate("submit", spec)
        completed = self.jobs.run("edit")
        self.assertEqual(completed["job"]["selection"], {"status": "selected", "revision": 2})
        self.assertEqual(self.jobs.recover("edit"), completed)
        self.assertEqual(self.sessions.show("main")["session"]["head_revision"], 2)

    def test_late_result_keeps_candidate_without_overwriting_new_selection(self):
        spec = self.spec()
        spec["selection"] = {"expected_revision": 1, "output_index": 0}
        self.jobs.mutate("submit", spec)
        process, signals = self.worker("hold")
        self.ready(process, signals)
        self.sessions.mutate("select", {"schema": "matter-session-select/v1", "request_id": "newer",
                                       "session_id": "main", "expected_revision": 1, "asset_id": self.asset})
        (signals / "continue").touch()
        _, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, error)
        result = self.jobs.show("edit")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["job"]["selection"]["reason"], "revision_conflict")
        self.assertEqual(self.sessions.show("main")["current"]["selected_asset"]["asset_id"], self.asset)
        self.assertEqual(len(result["playback"]), 1)

    def test_crash_after_publication_recovers_without_executing_audio(self):
        spec = self.spec()
        spec["selection"] = {"expected_revision": 1, "output_index": 0}
        self.jobs.mutate("submit", spec)
        process, _ = self.worker("after_publish")
        _, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 17, error)
        self.assertEqual(self.jobs.show("edit")["status"], "running")
        before = {p: p.read_bytes() for p in (self.store.root / "objects").rglob("*") if p.is_file()}
        with patch.object(self.jobs.actions, "execute", side_effect=AssertionError("must not execute")):
            recovered = self.jobs.recover("edit")
        self.assertEqual(recovered["status"], "succeeded")
        self.assertEqual(recovered["job"]["selection"]["revision"], 2)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_crash_after_claim_is_interrupted_then_explicit_new_attempt(self):
        self.jobs.mutate("submit", self.spec())
        process, _ = self.worker("after_claim")
        _, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 18, error)
        self.assert_code("recovery_required", self.jobs.run, "edit")
        self.assertEqual(self.jobs.recover("edit")["status"], "interrupted")
        old_id = self.jobs.show("edit")["attempts"][0]["action_request_id"]
        self.assert_code("recovery_pending", self.store.show_request, old_id)
        receipt = self.mutate("retry")
        self.assertEqual(receipt["job"]["attempt"], 2)
        self.assertEqual(self.jobs.run("edit")["status"], "succeeded")
        self.assertEqual(self.mutate("retry"), receipt)
        self.assertEqual([a["state"] for a in self.jobs.show("edit")["attempts"]], ["succeeded", "interrupted"])
        self.assert_code("recovery_pending", self.store.show_request, old_id)

    def test_live_worker_excludes_duplicate_run_recovery_and_retry(self):
        self.jobs.mutate("submit", self.spec())
        process, signals = self.worker("hold")
        self.ready(process, signals)
        for call in (self.jobs.run, self.jobs.recover):
            self.assert_code("job_busy", call, "edit")
        self.assert_code("job_busy", self.mutate, "retry")
        (signals / "continue").touch()
        _, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, error)
        self.assertEqual(len(list((self.store.root / "objects").iterdir())), 2)

    def test_cancel_request_waits_for_worker_acknowledgement(self):
        self.jobs.mutate("submit", self.spec())
        process, signals = self.worker("hold")
        self.ready(process, signals)
        cancelled = self.mutate("cancel")
        self.assertEqual(cancelled["job"]["state"], "cancel_requested")
        self.assertIsNone(process.poll())
        self.assert_code("job_busy", self.jobs.recover, "edit")
        (signals / "continue").touch()
        _, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, error)
        final = self.jobs.show("edit")
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(final["result"]["outputs"], [])
        self.assertEqual(self.mutate("cancel"), cancelled)

    def test_cooperative_worker_stops_before_operation_returns(self):
        self.jobs.mutate("submit", self.spec())
        process, signals = self.worker("cooperative")
        self.ready(process, signals)
        self.mutate("cancel")
        _, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, error)
        self.assertFalse((signals / "continue").exists())
        self.assertEqual(self.jobs.show("edit")["status"], "cancelled")

    def test_queued_cancel_never_calls_audio_and_stale_cancel_cannot_hit_retry(self):
        self.jobs.mutate("submit", self.spec())
        self.assertEqual(self.mutate("cancel")["job"]["state"], "cancelled")
        with patch.object(self.jobs.actions, "execute", side_effect=AssertionError("must not execute")):
            self.assertEqual(self.jobs.run("edit")["status"], "cancelled")
        self.mutate("retry")
        self.assert_code("attempt_conflict", self.mutate, "cancel", request_id="late-cancel")
        self.assertEqual(self.jobs.show("edit")["status"], "queued")

    def test_cpu_gain_and_inspection_check_inside_large_buffers(self):
        pcm = PCM(sample_bytes(array("h", [-12000, 12000] * 40000)), 8000, 1)
        for function in (lambda: gain(pcm, Q24), lambda: inspect(pcm)):
            calls = []

            def cancel(force):
                calls.append(force)
                if len(calls) == 3:
                    raise AudioError("job_cancelled", "fixture")

            with checkpoints(cancel):
                self.assert_code("job_cancelled", function)
            self.assertEqual(len(calls), 3)
        checkpoint()  # Context is restored after both exceptions.

    def test_completed_output_wins_late_cancel_without_selecting(self):
        spec = self.spec()
        spec["selection"] = {"expected_revision": 1, "output_index": 0}
        self.jobs.mutate("submit", spec)
        original = self.jobs._finish

        def finish(job_id, result):
            self.mutate("cancel")
            original(job_id, result)

        with patch.object(self.jobs, "_finish", side_effect=finish):
            result = self.jobs.run("edit")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["job"]["selection"]["reason"], "cancel_requested")
        self.assertEqual(self.sessions.show("main")["session"]["head_revision"], 1)

    def test_registration_failure_rolls_back_selection_then_recovers(self):
        spec = self.spec()
        spec["selection"] = {"expected_revision": 1, "output_index": 0}
        self.jobs.mutate("submit", spec)
        original = self.jobs.sessions._select

        def broken(*args):
            original(*args)
            raise RuntimeError("after selection write")

        with patch.object(self.jobs.sessions, "_select", side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, "selection write"):
                self.jobs.run("edit")
        self.assertEqual(self.sessions.show("main")["session"]["head_revision"], 1)
        self.assertEqual(self.jobs.recover("edit")["job"]["selection"]["revision"], 2)

    def test_partial_batch_retries_only_failed_items_and_preserves_successes(self):
        specs = [self.spec("candidate-" + str(i), -i - 1) for i in range(4)]
        batch = {"schema": "matter-batch-submit/v1", "request_id": "batch-create", "batch_id": "four",
                 "session_id": "main", "items": [{"job_id": s["job_id"], "action": s["action"]} for s in specs]}
        self.jobs.mutate("batch_submit", batch)
        operation = self.jobs.registry.get("gain/v1")
        calls = []

        def once(parameters, pcm):
            calls.append(parameters["db"])
            if parameters["db"] == -2 and calls.count(-2) == 1:
                raise AudioError("fixture_failure", "Synthetic transient failure")
            return operation.execute(parameters, pcm)

        self.jobs.registry._operations[operation.name] = replace(operation, execute=once)
        self.assertEqual(self.jobs.batch_run("four")["counts"], {"succeeded": 3, "failed": 1})
        before = [self.jobs.show(s["job_id"])["result"] for s in specs]
        retry = {"schema": "matter-batch-retry/v1", "request_id": "retry-failed", "batch_id": "four",
                 "items": [{"job_id": "candidate-1", "expected_attempt": 1}]}
        receipt = self.jobs.mutate("batch_retry", retry)
        self.assertEqual(self.jobs.batch_run("four")["status"], "succeeded")
        self.assertEqual(calls, [-1, -2, -3, -4, -2])
        self.assertEqual(self.jobs.mutate("batch_retry", retry), receipt)
        for index in (0, 2, 3):
            self.assertEqual(self.jobs.show(specs[index]["job_id"])["result"], before[index])
        retry["request_id"] = "cannot-retry-success"
        self.assert_code("attempt_conflict", self.jobs.mutate, "batch_retry", retry)
        retry["items"] = [{"job_id": "candidate-0", "expected_attempt": 1}]
        self.assert_code("job_not_retryable", self.jobs.mutate, "batch_retry", retry)

    def test_cancel_pending_when_worker_dies_is_confirmed_by_recovery(self):
        self.jobs.mutate("submit", self.spec())
        process, signals = self.worker("hold")
        self.ready(process, signals)
        self.assertEqual(self.mutate("cancel")["job"]["state"], "cancel_requested")
        process.terminate()
        process.communicate(timeout=10)
        self.assertEqual(self.jobs.recover("edit")["status"], "cancelled")
        self.assertEqual(len(list((self.store.root / "objects").iterdir())), 1)

    def test_batch_retry_rolls_back_earlier_items_if_a_success_is_included(self):
        batch = {"schema": "matter-batch-submit/v1", "request_id": "mixed", "batch_id": "mixed",
                 "session_id": "main", "items": [
            {"job_id": "failed", "action": self.spec(db=24)["action"]},
            {"job_id": "passed", "action": self.spec()["action"]}]}
        self.jobs.mutate("batch_submit", batch)
        self.jobs.batch_run("mixed")
        retry = {"schema": "matter-batch-retry/v1", "request_id": "bad-retry", "batch_id": "mixed",
                 "items": [{"job_id": name, "expected_attempt": 1} for name in ("failed", "passed")]}
        self.assert_code("job_not_retryable", self.jobs.mutate, "batch_retry", retry)
        failed = self.jobs.show("failed")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["job"]["attempt"], 1)
        self.assertEqual(len(failed["attempts"]), 1)
        self.assert_code("request_not_found", self.sessions.request_status, "bad-retry")

    def test_deterministic_failures_remain_failed_until_parameters_change_in_new_job(self):
        self.jobs.mutate("submit", self.spec(db=24))
        self.assertEqual(self.jobs.run("edit")["error"]["code"], "clipping_rejected")
        self.mutate("retry")
        self.assertEqual(self.jobs.run("edit")["status"], "failed")
        self.assertEqual(self.jobs.show("edit")["job"]["attempt"], 2)

    def test_changed_profile_or_resolver_never_executes_an_old_submission(self):
        self.jobs.mutate("submit", self.spec())
        operation = self.jobs.registry.get("gain/v1")
        self.jobs.registry._operations[operation.name] = replace(operation, profile="different/v1")
        result = self.jobs.run("edit")
        self.assertEqual(result["error"]["code"], "resolution_conflict")
        self.assert_code("resolution_conflict", self.mutate, "retry")
        self.assertEqual(result["result"], None)

    def test_corrupt_published_result_blocks_recovery_and_retry(self):
        self.jobs.mutate("submit", self.spec())
        process, _ = self.worker("after_publish")
        process.communicate(timeout=10)
        current = self.jobs.show("edit")["attempts"][0]
        result = self.store.show_request(current["action_request_id"])
        path = self.store.root / result["outputs"][0]["locator"]
        data = path.read_bytes()
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        self.assert_code("integrity_error", self.jobs.recover, "edit")
        self.assert_code("job_not_retryable", self.mutate, "retry")

    def test_submit_validation_conflicts_and_batch_atomicity(self):
        self.jobs.mutate("submit", self.spec())
        changed = self.spec(db=-6)
        self.assert_code("request_conflict", self.jobs.mutate, "submit", changed)
        changed["request_id"] = "other"
        self.assert_code("job_exists", self.jobs.mutate, "submit", changed)
        malformed = self.spec("extra")
        malformed["shell"] = "no"
        self.assert_code("invalid_request", self.jobs.mutate, "submit", malformed)
        malformed.pop("shell")
        malformed["selection"] = {"expected_revision": True, "output_index": 0}
        self.assert_code("invalid_request", self.jobs.mutate, "submit", malformed)
        batch = {"schema": "matter-batch-submit/v1", "request_id": "bad-batch", "batch_id": "bad",
                 "session_id": "main", "items": [{"job_id": "duplicate", "action": self.spec()["action"]}] * 2}
        self.assert_code("job_exists", self.jobs.mutate, "batch_submit", batch)
        self.assert_code("batch_not_found", self.jobs.batch_show, "bad")
        self.assert_code("job_not_found", self.jobs.show, "duplicate")
        self.assert_code("request_not_found", self.sessions.request_status, "bad-batch")

    def test_query_pagination_is_read_only(self):
        for index in range(3):
            self.jobs.mutate("submit", self.spec(str(index)))
        database = self.store.root / DATABASE_NAME
        before = database.read_bytes()
        first = self.jobs.list_jobs(session_id="main", limit=2)
        self.assertEqual(first["next_offset"], 2)
        self.assertEqual(len(self.jobs.list_jobs(offset=2, limit=2)["jobs"]), 1)
        self.jobs.show("0")
        self.sessions.context("main")
        self.assertEqual(database.read_bytes(), before)
        self.assert_code("invalid_request", self.jobs.list_jobs, limit=True)

    def test_v1_migration_is_explicit_atomic_and_preserves_existing_state(self):
        old = ArtifactStore(self.root / "old")
        sessions = SessionService(old)
        with patch("matter_audio_core.session_db.DATABASE_VERSION", 1):
            # Write the actual historical schema; today's service writes v3 columns.
            with sessions.database.transaction(write=True, create=True) as connection:
                connection.execute("INSERT INTO sessions VALUES ('saved', 'Saved empty session', 1, 'old', NULL, NULL)")
                connection.execute("INSERT INTO revisions VALUES ('saved', 1, NULL, 'null', 'create', NULL, 'old')")
                receipt = canonical({"revision": {"revision": 1}}).decode()
                connection.execute("INSERT INTO mutations VALUES ('old-create', '{}', ?, 'old')", (receipt,))
        database = old.root / DATABASE_NAME
        before = database.read_bytes()
        self.assert_code("session_migration_required", sessions.show, "saved")
        self.assertEqual(before, database.read_bytes())
        broken = {**MIGRATIONS, 2: (*MIGRATIONS[2], "INVALID SQL")}
        with patch("matter_audio_core.session_db.MIGRATIONS", broken):
            self.assert_code("session_database_error", sessions.migrate)
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='jobs'").fetchone())
        finally:
            connection.close()
        self.assertEqual(sessions.migrate()["database_version"], DATABASE_VERSION)
        self.assertEqual(sessions.show("saved")["session"]["head_revision"], 1)
        self.assertEqual(sessions.request_status("old-create")["revision"]["revision"], 1)

    def test_retry_receipt_can_be_replayed_while_new_attempt_is_running(self):
        self.jobs.mutate("submit", self.spec(db=24))
        self.jobs.run("edit")
        receipt = self.mutate("retry")
        with job_lock(self.store, "edit"):
            self.assertEqual(self.mutate("retry"), receipt)


if __name__ == "__main__":
    unittest.main()
