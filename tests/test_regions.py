"""Exact locks, fade arithmetic, continuous edits and historical job compatibility."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from array import array
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from matter_audio_core.actions import ActionService, Registry
from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.contracts import canonical, request, validate
from matter_audio_core.errors import AudioError
from matter_audio_core.fades import FADE_SCHEMA, fade, resolve_fade
from matter_audio_core.jobs import JobService
from matter_audio_core.media import PCM, decode_wav, encode_wav, sample_bytes
from matter_audio_core.session_db import DATABASE_NAME, DATABASE_VERSION, MIGRATIONS
from matter_audio_core.sessions import SessionService


def pcm(values, channels=1):
    return PCM(sample_bytes(array("h", values)), 8000, channels)


class ErrorChecks:
    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(AudioError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)


class FadeTests(ErrorChecks, unittest.TestCase):
    def render(self, source, **parameters):
        validate(parameters, FADE_SCHEMA)
        return fade(resolve_fade(parameters, source), source)[0]

    def test_stereo_endpoints_and_signed_half_rounding(self):
        source = pcm([3, -3] * 5, 2)
        self.assertEqual(list(self.render(source, fade_in_frames=3).samples()),
                         [0, 0, 2, -2, 3, -3, 3, -3, 3, -3])
        self.assertEqual(list(self.render(source, fade_out_frames=3).samples()),
                         [3, -3, 3, -3, 3, -3, 2, -2, 0, 0])

    def test_both_edges_copy_the_untouched_middle_exactly(self):
        source = pcm([-32768, 32767, -12345, 23456, 12345, -23456] * 6)
        output = self.render(source, fade_in_frames=5, fade_out_frames=7)
        self.assertEqual(output.payload[10:-14], source.payload[10:-14])
        self.assertEqual((output.frames, output.sample_rate, output.channels), (36, 8000, 1))
        self.assertEqual((output.samples()[0], output.samples()[-1]), (0, 0))

    def test_zero_one_and_two_frame_fades_have_explicit_endpoints(self):
        source = pcm([32767, -32768, 13, -13])
        self.assertEqual(self.render(source, fade_out_frames=0).payload, source.payload)
        self.assertEqual(list(self.render(source, fade_in_frames=1, fade_out_frames=1).samples()),
                         [0, -32768, 13, 0])
        self.assertEqual(list(self.render(source, fade_in_frames=2, fade_out_frames=2).samples()),
                         [0, -32768, 13, 0])

    def test_seconds_round_half_up_and_overlap_or_mixed_units_are_rejected(self):
        source = pcm([2000] * 8)
        resolved = resolve_fade({"fade_out_seconds": 0.0003125}, source)
        self.assertEqual(resolved["fade_out_frames"], 3)  # 2.5 frames
        self.assert_code("fade_overlap", self.render, source, fade_in_frames=5, fade_out_frames=4)
        for parameters in ({}, {"fade_in_frames": True}, {"fade_out_frames": -1},
                           {"fade_in_frames": 1, "fade_out_seconds": 0.001},
                           {"fade_out_frames": 2, "curve": "equal_power"}):
            self.assert_code("invalid_request", validate, parameters, FADE_SCHEMA)


class RegionTests(ErrorChecks, unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="matter protected # ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.wav"
        self.source.write_bytes(encode_wav(pcm([3000, -9000] * 800, 2)))
        self.store = ArtifactStore(self.root / "workspace")
        self.asset = self.store.import_wav(self.source, "import")["outputs"][0]["asset_id"]
        self.sessions = SessionService(self.store)
        self.sessions.mutate("create", {"schema": "matter-session-create/v1", "request_id": "create",
                                       "session_id": "main", "name": "Test", "asset_id": self.asset})
        self.actions = ActionService(self.store)
        self.jobs = JobService(self.store)

    def lock(self, ranges=None, expected=1, request_id="lock"):
        return self.sessions.mutate("constraints", {
            "schema": "matter-constraints-set/v1", "request_id": request_id, "session_id": "main",
            "expected_revision": expected,
            "regions": [{"start_frame": 0, "end_frame": 100}] if ranges is None else ranges})

    def action(self, operation, parameters, asset=None, revision=2, request_id="edit"):
        value = request(request_id, operation, asset or self.asset, parameters)
        if revision is not None:
            value["protection"] = {"session_id": "main", "revision": revision}
        return value

    def select(self, asset=None, expected=2, request_id="select", restore=None):
        value = {"schema": "matter-session-select/v1", "request_id": request_id, "session_id": "main",
                 "expected_revision": expected}
        value.update({"from_revision": restore} if restore is not None else {"asset_id": asset})
        return self.sessions.mutate("select", value)

    def submit(self, operation="fade/v1", parameters=None, job_id="edit", selection=None):
        value = {"schema": "matter-job-submit/v1", "request_id": "submit-" + job_id,
                 "job_id": job_id, "session_id": "main",
                 "action": {"operation": operation, "inputs": [self.asset],
                            "parameters": parameters or {"fade_out_frames": 100}}}
        if selection is not None:
            value["selection"] = {"expected_revision": selection, "output_index": 0}
        return self.jobs.mutate("submit", value)

    def test_two_edits_restore_branch_and_reopen_keep_anchor_and_pcm(self):
        original = self.source.read_bytes()
        policy = self.lock()["revision"]["constraints"]
        trimmed = self.actions.execute(self.action("trim/v1", {"start_frame": 0, "end_frame": 600}))
        second = trimmed["outputs"][0]["asset_id"]
        self.select(second)
        faded = self.actions.execute(self.action("fade/v1", {"fade_out_frames": 200},
                                                second, 3, "fade"))
        third = faded["outputs"][0]["asset_id"]
        self.select(third, 3, "select-fade")
        for result in (trimmed, faded):
            self.assertEqual(result["findings"][0]["protection"]["status"], "verified")
            data = decode_wav(self.store.asset(result["outputs"][0]["asset_id"])[1])
            self.assertEqual(data.payload[:400], decode_wav(original).payload[:400])
        self.select(expected=4, request_id="restore", restore=3)
        self.sessions.mutate("branch", {"schema": "matter-session-branch/v1", "request_id": "branch",
            "session_id": "alternative", "name": "Alt", "from_session": "main", "from_revision": 4})
        reopened = SessionService(ArtifactStore(self.store.root))
        self.assertEqual(reopened.show("main")["current"]["selected_asset"]["asset_id"], second)
        self.assertEqual(reopened.show("alternative")["current"]["selected_asset"]["asset_id"], third)
        for session in ("main", "alternative"):
            self.assertEqual(reopened.constraints(session)["constraints"], policy)
            self.assertEqual(reopened.context(session)["constraints"]["mapped_regions"][0]["end_frame"], 100)
        self.assertEqual(original, self.source.read_bytes())

    def test_inner_region_projects_after_two_leading_trims(self):
        self.lock([{"start_frame": 200, "end_frame": 300}, {"start_frame": 400, "end_frame": 500}])
        second = self.actions.execute(self.action("trim/v1", {"start_frame": 100, "end_frame": 700}))["outputs"][0]["asset_id"]
        self.select(second)
        third = self.actions.execute(self.action("trim/v1", {"start_frame": 50, "end_frame": 500},
                                                second, 3, "trim-again"))["outputs"][0]["asset_id"]
        self.select(third, 3, "select-again")
        regions = self.sessions.constraints("main")["mapped_regions"]
        self.assertEqual([(r["start_frame"], r["end_frame"]) for r in regions], [(50, 150), (250, 350)])
        self.assertEqual([r["anchor_start_frame"] for r in regions], [200, 400])

    def test_overlap_deletion_and_different_input_are_rejected_at_resolve(self):
        self.lock()
        for op, params in (("gain/v1", {"db": -3}), ("trim/v1", {"start_frame": 1, "end_frame": 700}),
                           ("fade/v1", {"fade_in_frames": 3})):
            self.assert_code("constraint_violation", self.actions.resolve, self.action(op, params))
        other = self.store.import_wav(self.source, "different-import")["outputs"][0]["asset_id"]
        self.assert_code("constraint_input_mismatch", self.actions.resolve,
                         self.action("fade/v1", {"fade_out_frames": 3}, other))
        self.assert_code("constraint_mapping_unavailable", self.select, other)

    def test_unity_fade_endpoint_may_touch_lock_and_inspection_is_allowed(self):
        self.lock([{"start_frame": 0, "end_frame": 700}])
        allowed = self.actions.execute(self.action("fade/v1", {"fade_out_frames": 101}))
        self.assertEqual(allowed["status"], "succeeded")
        self.assert_code("constraint_violation", self.actions.resolve,
                         self.action("fade/v1", {"fade_out_frames": 102}, request_id="bad"))
        inspected = self.actions.execute(self.action("inspect/v1", {}, request_id="inspect"))
        self.assertEqual(inspected["outputs"], [])
        self.assertEqual(inspected["findings"][0]["protection"]["status"], "verified")
        self.assertEqual(self.actions.execute(self.action("gain/v1", {"db": 0}, request_id="unity"))["status"], "succeeded")

    def test_select_rejects_unprotected_edit_that_changed_locked_samples(self):
        self.lock()
        changed = self.actions.execute(self.action("gain/v1", {"db": -3}, revision=None))["outputs"][0]["asset_id"]
        self.assert_code("constraint_violation", self.select, changed)
        self.assertEqual(self.sessions.show("main")["session"]["head_revision"], 2)

    def test_constraint_mutations_are_idempotent_guarded_and_explicitly_clearable(self):
        first = self.lock()
        self.assertEqual(self.lock(), first)
        self.assert_code("request_conflict", self.lock, [], 1)
        self.assert_code("revision_conflict", self.lock, [], 1, "stale")
        self.lock([], 2, "clear")
        cleared = self.sessions.constraints("main")
        self.assertEqual(cleared["mapped_regions"], [])
        self.assertIsNone(cleared["constraints"]["anchor_asset"])
        self.assertNotEqual(first["revision"]["constraints"]["constraint_id"], cleared["constraints"]["constraint_id"])
        self.select(expected=3, request_id="restore-lock", restore=2)
        self.assertEqual(self.sessions.constraints("main")["constraints"], first["revision"]["constraints"])
        self.select(expected=4, request_id="restore-unlocked", restore=1)
        self.assertIsNone(self.sessions.constraints("main")["constraints"])

    def test_region_units_bounds_order_and_count_are_validated(self):
        for index, ranges in enumerate(([{"start_frame": 10, "end_frame": 10}],
                                       [{"start_frame": 0, "end_frame": 801}],
                                       [{"start_frame": 20, "end_frame": 30}, {"start_frame": 10, "end_frame": 20}],
                                       [{"start_seconds": 0, "end_seconds": 0.00001}])):
            self.assert_code("invalid_region", self.lock, ranges, 1, "bad-" + str(index))
        self.assert_code("invalid_request", self.lock, [{"start_frame": 0, "end_frame": 1}] * 17)
        self.lock([{"start_seconds": 0.0003125, "end_seconds": 0.01}])
        self.assertEqual(self.sessions.constraints("main")["mapped_regions"][0]["start_frame"], 3)

    def test_constraint_changes_invalidate_resolutions_and_queued_jobs(self):
        self.lock()
        action = self.action("fade/v1", {"fade_out_frames": 100})
        resolution = self.actions.resolve(action)
        self.submit()
        self.lock([], 2, "clear")
        self.assert_code("constraint_conflict", self.actions.execute, action,
                         expected_resolution_digest=resolution["digest"]["hex"])
        result = self.jobs.run("edit")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "constraint_conflict")
        self.assertEqual(len(self.store.list_assets()["assets"]), 1)
        self.assert_code("constraint_conflict", self.jobs.mutate, "retry", {
            "schema": "matter-job-retry/v1", "request_id": "retry", "job_id": "edit", "expected_attempt": 1})

    def test_new_lock_invalidates_previously_unlocked_job(self):
        self.submit("gain/v1", {"db": -3})
        self.lock()
        self.assertEqual(self.jobs.run("edit")["error"]["code"], "constraint_conflict")

    def test_constraint_change_during_execution_prevents_audio_publication(self):
        self.lock()
        base = self.actions.registry.get("fade/v1")
        def execute(parameters, source):
            output = base.execute(parameters, source)
            self.lock([], 2, "clear-during-run")
            return output
        service = ActionService(self.store, Registry([replace(base, execute=execute)]))
        result = service.execute(self.action("fade/v1", {"fade_out_frames": 100}))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "constraint_conflict")
        self.assertEqual(result["outputs"], [])

    def test_undeclared_mapping_and_false_executor_claims_are_not_trusted(self):
        self.lock()
        base = self.actions.registry.get("fade/v1")
        action = self.action("fade/v1", {"fade_out_frames": 100})
        unavailable = ActionService(self.store, Registry([replace(base, mapping=None)]))
        self.assert_code("constraint_mapping_unavailable", unavailable.resolve, action)
        def corrupt(parameters, source):
            output, observation = base.execute(parameters, source)
            return PCM(b"\0\0" + output.payload[2:], output.sample_rate, output.channels), observation
        liar = ActionService(self.store, Registry([replace(base, execute=corrupt)]))
        result = liar.execute(action)
        self.assertEqual(result["error"]["code"], "constraint_violation")
        self.assertEqual(result["outputs"], [])

    def test_late_job_keeps_candidate_but_cannot_replace_newer_selection(self):
        policy = self.lock()["revision"]["constraints"]
        self.submit(selection=2)
        second = self.actions.execute(self.action("trim/v1", {"start_frame": 0, "end_frame": 700}))["outputs"][0]["asset_id"]
        self.select(second)
        result = self.jobs.run("edit")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["job"]["selection"]["reason"], "revision_conflict")
        self.assertEqual(self.sessions.constraints("main")["constraints"], policy)
        self.assertEqual(self.sessions.show("main")["current"]["selected_asset"]["asset_id"], second)

    def test_recovery_of_published_protected_job_selects_once(self):
        self.lock()
        self.submit(selection=2)
        with patch.object(self.jobs, "_finish", side_effect=RuntimeError("simulated registration crash")):
            with self.assertRaises(RuntimeError):
                self.jobs.run("edit")
        recovered = JobService(self.store).recover("edit")
        self.assertEqual(recovered["status"], "succeeded")
        self.assertEqual(recovered["job"]["selection"], {"status": "selected", "revision": 3})
        self.assertEqual(JobService(self.store).recover("edit"), recovered)
        self.assertEqual(self.sessions.show("main")["session"]["head_revision"], 3)

    def legacy_workspace(self):
        old = ArtifactStore(self.root / "legacy")
        asset = old.import_wav(self.source, "import")["outputs"][0]["asset_id"]
        sessions = SessionService(old)
        action = request("legacy-action", "gain/v1", asset, {"db": 24})
        resolution = ActionService(old).resolve(action)
        spec = {"job_id": "old-job", "action": {key: action[key] for key in ("operation", "inputs", "parameters")}}
        with patch("matter_audio_core.session_db.DATABASE_VERSION", 2):
            with sessions.database.transaction(write=True, create=True) as connection:
                snapshot = canonical(sessions._asset(asset)).decode()
                connection.execute("INSERT INTO sessions VALUES ('main', 'Legacy', 1, 'old', NULL, NULL)")
                connection.execute("INSERT INTO revisions VALUES ('main', 1, NULL, ?, 'create', NULL, 'old')", (snapshot,))
                connection.execute("INSERT INTO feedback VALUES (1, 'f_old', 'main', 1, 'agent', 'Fixture', '', 'old')")
                connection.execute("INSERT INTO jobs VALUES ('old-job', 'main', 1, ?, 'queued', 1, ?, 'old', 'old')",
                                   (canonical(spec).decode(), canonical({"status": "not_requested"}).decode()))
                JobService._insert_attempt(connection, "old-job", 1, resolution)
        return old, sessions, resolution

    def test_v2_migration_rolls_back_and_preserves_frozen_job_retry(self):
        old, sessions, resolution = self.legacy_workspace()
        database = old.root / DATABASE_NAME
        before = database.read_bytes()
        self.assert_code("session_migration_required", sessions.show, "main")
        self.assertEqual(database.read_bytes(), before)
        with patch("matter_audio_core.session_db.MIGRATIONS", {**MIGRATIONS, 3: (*MIGRATIONS[3], "INVALID SQL")}):
            self.assert_code("session_database_error", sessions.migrate)
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertNotIn("constraints_json", [row[1] for row in connection.execute("PRAGMA table_info(revisions)")])
        self.assertEqual(sessions.migrate()["database_version"], DATABASE_VERSION)
        self.assertIsNone(sessions.show("main")["current"]["constraints"])
        self.assertEqual(sessions.list_feedback("main")["feedback"][0]["text"], "Fixture")
        jobs = JobService(old)
        self.assertEqual(jobs.show("old-job")["resolution"], resolution)
        self.assertEqual(jobs.run("old-job")["error"]["code"], "clipping_rejected")
        jobs.mutate("retry", {"schema": "matter-job-retry/v1", "request_id": "retry", "job_id": "old-job", "expected_attempt": 1})
        self.assertEqual(jobs._comparable(jobs.show("old-job")["resolution"]), jobs._comparable(resolution))
        self.assertEqual(jobs.run("old-job")["error"]["code"], "clipping_rejected")

    def test_legacy_job_cannot_bypass_a_new_lock(self):
        old, sessions, _ = self.legacy_workspace()
        sessions.migrate()
        sessions.mutate("constraints", {"schema": "matter-constraints-set/v1", "request_id": "lock",
            "session_id": "main", "expected_revision": 1, "regions": [{"start_frame": 0, "end_frame": 100}]})
        jobs = JobService(old)
        result = jobs.run("old-job")
        self.assertEqual(result["error"]["code"], "constraint_conflict")
        self.assertEqual(len(old.list_assets()["assets"]), 1)
        self.assert_code("constraint_conflict", jobs.mutate, "retry", {
            "schema": "matter-job-retry/v1", "request_id": "retry", "job_id": "old-job", "expected_attempt": 1})


if __name__ == "__main__":
    unittest.main()
