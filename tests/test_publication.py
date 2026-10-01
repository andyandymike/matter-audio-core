"""Readable publication budgets and concurrent first-use initialization."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from matter_audio_core.actions import ActionService, Operation, Registry
from matter_audio_core.artifacts import ArtifactStore, Publication
from matter_audio_core.contracts import MAX_JSON_BYTES, canonical, fingerprint, object_schema, request
from matter_audio_core.errors import AudioError
from matter_audio_core.execution import record_execution
from matter_audio_core.jobs import JobService
from matter_audio_core.media import PCM, encode_wav
from matter_audio_core.sessions import SessionService


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="matter-publication-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = ArtifactStore(self.root / "workspace")
        self.pcm = PCM(b"\x01\x00\x02\x00", 8000, 1)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(AudioError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_preflight_uses_exact_manifest_and_has_no_filesystem_side_effects(self):
        def produce(publication):
            publication.add(encode_wav(self.pcm), self.pcm.facts(),
                            provenance={"fixture": "complete-publication"})
            return {"findings": [], "limitations": ["Synthetic fixture"]}

        preview = Publication("0" * 32)
        extra = produce(preview)
        expected = self.store.validate_publication("exact", {"fixture": 1}, preview, extra)
        self.assertFalse(self.store.root.exists())
        result = self.store.transact("exact", {"fixture": 1}, produce)
        actual = Publication(result["group_id"])
        self.assertEqual(self.store.validate_publication("exact", {"fixture": 1}, actual, produce(actual)), result)
        self.assertEqual(len(canonical(expected)), len(canonical(result)))

    def test_exact_json_limit_is_readable_and_one_more_byte_is_rejected(self):
        preview = Publication("0" * 32)
        extra = {"notes": ""}
        size = len(canonical(self.store.validate_publication("boundary", {}, preview, extra)))
        extra["notes"] = "x" * (MAX_JSON_BYTES - size)
        expected = self.store.validate_publication("boundary", {}, preview, extra)
        self.assertEqual(len(canonical(expected)), MAX_JSON_BYTES)
        result = self.store.transact("boundary", {}, lambda publication: extra)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.store.show_request("boundary"), result)
        extra["notes"] += "x"
        self.assert_code("publication_too_large", self.store.validate_publication,
                         "boundary", {}, preview, extra)

    def test_oversized_output_becomes_readable_failure_and_discards_unpublished_assets(self):
        calls = []

        def produce(publication):
            calls.append(1)
            publication.add(encode_wav(self.pcm), self.pcm.facts())
            return {"notes": "x" * MAX_JSON_BYTES}

        result = self.store.transact("large", {}, produce)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "publication_too_large")
        self.assertEqual(result["outputs"], [])
        self.assertEqual(self.store.transact("large", {}, produce), result)
        self.assertEqual(calls, [1])
        self.assertEqual(self.store.list_assets()["assets"], [])
        group = self.store.root / "objects" / result["group_id"]
        self.assertEqual({p.name for p in group.iterdir()}, {"manifest.json", "manifest.sha256"})

    def test_oversized_binding_is_rejected_without_claim_or_producer(self):
        binding = {"notes": "x" * MAX_JSON_BYTES}
        self.assert_code("publication_too_large", self.store.transact, "binding", binding,
                         lambda publication: self.fail("Producer must not run"))
        self.assertEqual(list((self.store.root / "requests").iterdir()), [])
        self.assertEqual(list((self.store.root / "objects").iterdir()), [])
        self.assertEqual(self.store.transact("binding", {}, lambda p: {})["status"], "succeeded")

    def test_legacy_readable_success_replays_even_without_new_failure_headroom(self):
        publication = Publication("a" * 32)
        binding = {"notes": ""}
        result = self.store.validate_publication("legacy", binding, publication, {})
        binding["notes"] = "x" * (MAX_JSON_BYTES - len(canonical(result)))
        result = self.store.validate_publication("legacy", binding, publication, {})
        self.store._workspace(create=True)
        self.store._publish(self.store._request_path("legacy"), {"claim.json": canonical({
            "request_id": "legacy", "binding_digest": fingerprint(binding), "group_id": publication.group_id})})
        self.store._publish(self.store.root / "objects" / publication.group_id, {
            "manifest.json": canonical(result), "manifest.sha256": fingerprint(result)["hex"].encode("ascii")})
        self.assertEqual(self.store.transact("legacy", binding, lambda p: self.fail("Legacy result must replay")), result)

    def test_capacity_failure_preserves_known_and_uncertain_execution_evidence(self):
        for started in (True, None):
            request_id = "known" if started else "uncertain"

            def produce(publication):
                record_execution({"call_id": "fixture", "audio_model": "synthetic-evidence-only",
                                  "backend_started": started})
                return {"notes": "x" * MAX_JSON_BYTES}

            result = self.store.transact(request_id, {}, produce)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["audio_model_calls"], 1 if started else None)
            self.assertEqual(result["execution"], self.store.execution_evidence(request_id)["execution"])
            self.assertEqual(self.store.show_request(request_id), result)

    def test_journal_budget_stops_before_recording_unreadable_evidence(self):
        def produce(publication):
            for index in range(3):
                record_execution({"call_id": str(index), "audio_model": "synthetic-evidence-only",
                                  "backend_started": True, "notes": "x" * 400000})
            self.fail("Third event must exceed the complete receipt budget")

        result = self.store.transact("journal", {}, produce)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "publication_too_large")
        self.assertEqual(result["audio_model_calls"], 2)
        self.assertEqual(len(result["execution"]["events"]), 2)
        self.assertEqual(self.store.show_request("journal"), result)

    def test_oversized_error_is_also_replaced_with_readable_failure(self):
        def produce(publication):
            raise AudioError("adapter_failure", "x" * MAX_JSON_BYTES)

        result = self.store.transact("error", {}, produce)
        self.assertEqual(result["error"]["code"], "publication_too_large")
        self.assertEqual(self.store.show_request("error"), result)

    def managed(self):
        source = self.root / "source.wav"
        source.write_bytes(encode_wav(self.pcm))
        asset = self.store.import_wav(source, "import")["outputs"][0]["asset_id"]
        SessionService(self.store).mutate("create", {"schema": "matter-session-create/v1",
            "request_id": "session", "session_id": "main", "name": "Synthetic", "asset_id": asset})
        registry = Registry([Operation("fixture.metadata/v1", object_schema({"notes": {"type": "string"}}),
            lambda p, pcm: p, lambda p, pcm: (None, {"kind": "measurement"}))])
        jobs = JobService(self.store, registry)
        spec = {"schema": "matter-job-submit/v1", "request_id": "submit", "session_id": "main", "job_id": "large",
                "action": {"operation": "fixture.metadata/v1", "inputs": [asset], "parameters": {"notes": "x" * 300000}}}
        return jobs, spec

    def test_managed_capacity_failure_recovers_after_publication_and_can_retry(self):
        jobs, spec = self.managed()
        jobs.mutate("submit", spec)
        with patch.object(jobs, "_finish", side_effect=RuntimeError("interrupted before registration")):
            with self.assertRaisesRegex(RuntimeError, "registration"):
                jobs.run("large")
        self.assertEqual(jobs.show("large")["status"], "running")
        recovered = jobs.recover("large")
        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(recovered["error"]["code"], "publication_too_large")
        self.assertEqual(jobs.recover("large"), recovered)
        jobs.mutate("retry", {"schema": "matter-job-retry/v1", "request_id": "retry",
                             "job_id": "large", "expected_attempt": 1})
        retried = jobs.run("large")
        self.assertEqual(retried["status"], "failed")
        self.assertEqual(retried["job"]["attempt"], 2)
        self.assertEqual(len(self.store.list_assets()["assets"]), 1)
        self.assertEqual(SessionService(self.store).show("main")["session"]["head_revision"], 1)

    def test_unreadable_job_resolution_rolls_back_submission_and_batch(self):
        jobs, spec = self.managed()
        spec["action"]["parameters"]["notes"] = "x" * 600000
        self.assert_code("resolution_too_large", jobs.mutate, "submit", spec)
        self.assert_code("job_not_found", jobs.show, "large")
        self.assert_code("request_not_found", jobs.sessions.request_status, "submit")
        good = {**spec["action"], "parameters": {"notes": "small"}}
        batch = {"schema": "matter-batch-submit/v1", "request_id": "batch", "session_id": "main", "batch_id": "batch",
                 "items": [{"job_id": "small", "action": good}, {"job_id": "large", "action": spec["action"]}]}
        self.assert_code("resolution_too_large", jobs.mutate, "batch_submit", batch)
        self.assert_code("job_not_found", jobs.show, "small")
        self.assert_code("batch_not_found", jobs.batch_show, "batch")
        self.assertEqual(len(list((self.store.root / "requests").iterdir())), 1)


class WorkspaceInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="matter-initialize-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.wav"
        self.source.write_bytes(encode_wav(PCM(b"\x01\x00", 8000, 1)))

    def contenders(self, workspace, products, *, mode="import"):
        signals = self.root / (workspace.name + "-signals")
        signals.mkdir()
        processes = []
        try:
            for index, product in enumerate(products):
                process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).with_name("workspace_worker.py")),
                    str(workspace), str(self.source), str(signals), "import-" + str(index), product, mode],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                processes.append(process)
            deadline = time.monotonic() + 20
            while len(list(signals.glob("*.ready"))) != len(products):
                self.assertFalse(any(p.poll() is not None for p in processes), "Contender exited before barrier")
                self.assertLess(time.monotonic(), deadline, "Contenders did not reach initialization barrier")
                time.sleep(.01)
            (signals / "go").touch()
            results = []
            for process in processes:
                output, error = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 41 if mode == "interrupt" else 0, error.decode())
                results.append(json.loads(output) if output else None)
            return results
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=10)

    def test_independent_processes_initialize_missing_and_empty_workspaces(self):
        for exists in (False, True):
            workspace = self.root / ("empty" if exists else "missing")
            if exists:
                workspace.mkdir()
            results = self.contenders(workspace, ["matter-audio"] * 4, mode="slow" if exists else "import")
            self.assertTrue(all(r["status"] == "succeeded" for r in results))
            self.assertEqual(len({r["asset_id"] for r in results}), 4)
            self.assertEqual(len(ArtifactStore(workspace).list_assets()["assets"]), 4)
            self.assertEqual(set(p.name for p in workspace.iterdir()), {"workspace.json", "objects", "requests", ".staging"})
            self.assertEqual({p.name for p in self.root.iterdir()} - {"source.wav"},
                             {"missing", "missing-signals", *({"empty", "empty-signals"} if exists else set())})

    def test_competing_products_cannot_take_over_the_winning_workspace(self):
        workspace = self.root / "products"
        results = self.contenders(workspace, ["one", "two"])
        self.assertEqual(sum(r.get("status") == "succeeded" for r in results), 1)
        self.assertEqual([r["error"]["code"] for r in results if "error" in r], ["workspace_mismatch"])
        self.assertEqual({p.name for p in self.root.iterdir()}, {"source.wav", "products", "products-signals"})

    def test_nonempty_foreign_directory_is_never_initialized(self):
        workspace = self.root / "foreign"
        workspace.mkdir()
        (workspace / "user.txt").write_text("User-owned content")
        results = self.contenders(workspace, ["matter-audio"] * 2)
        self.assertTrue(all(r["error"]["code"] == "workspace_unrecognized" for r in results))
        self.assertEqual([p.name for p in workspace.iterdir()], ["user.txt"])
        self.assertEqual((workspace / "user.txt").read_text(), "User-owned content")

    def test_interrupted_marker_fails_closed_without_reclaiming_the_directory(self):
        workspace = self.root / "interrupted"
        self.contenders(workspace, ["matter-audio"], mode="interrupt")
        marker = workspace / "workspace.json"
        before = marker.read_bytes()
        with self.assertRaises(AudioError) as caught:
            ArtifactStore(workspace).import_wav(self.source, "restart")
        self.assertEqual(caught.exception.code, "invalid_json")
        self.assertEqual(marker.read_bytes(), before)
        self.assertEqual([p.name for p in workspace.iterdir()], ["workspace.json"])


if __name__ == "__main__":
    unittest.main()
