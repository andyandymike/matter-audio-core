"""State and failure-path checks; all audio and feedback here are test fixtures."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

from matter_audio_core.actions import ActionService
from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.cli import run
from matter_audio_core.contracts import request
from matter_audio_core.errors import AudioError
from matter_audio_core.media import PCM, encode_wav, sample_bytes
from matter_audio_core.session_db import APPLICATION_ID, DATABASE_NAME, DATABASE_VERSION, MIGRATIONS
from matter_audio_core.sessions import SessionService


@contextlib.contextmanager
def open_database(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def create_request(session_id="bgm", request_id="create", asset_id=None):
    value = {"schema": "matter-session-create/v1", "request_id": request_id,
             "session_id": session_id, "name": "BGM 制作"}
    if asset_id:
        value["asset_id"] = asset_id
    return value


def select_request(asset_id=None, *, expected=1, request_id="select", session_id="bgm", restore=None):
    value = {"schema": "matter-session-select/v1", "request_id": request_id,
             "session_id": session_id, "expected_revision": expected}
    value.update({"from_revision": restore} if restore is not None else {"asset_id": asset_id})
    return value


def feedback_request(*, revision=1, request_id="feedback", source="agent_relay", text="测试反馈：尾音偏长"):
    return {"schema": "matter-feedback/v1", "request_id": request_id, "session_id": "bgm",
            "revision": revision, "text": text, "source": source,
            "listening_context": "Synthetic test fixture, not a real listening judgment."}


def branch_request():
    return {"schema": "matter-session-branch/v1", "request_id": "branch", "session_id": "alternative",
            "name": "Alternative", "from_session": "bgm", "from_revision": 2}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="matter 会话 # ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.wav"
        self.source.write_bytes(encode_wav(PCM(sample_bytes(array("h", [-2000, 2000] * 400)), 8000, 1)))
        self.store = ArtifactStore(self.root / "workspace")
        self.original = self.store.import_wav(self.source, "import-a")["outputs"][0]["asset_id"]
        self.sessions = SessionService(self.store)
        self.db = self.store.root / DATABASE_NAME

    def create(self):
        return self.sessions.mutate("create", create_request(asset_id=self.original))

    def candidate(self):
        return ActionService(self.store).execute(request("gain-b", "gain/v1", self.original,
                                                        {"db": -3}))["outputs"][0]["asset_id"]

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(AudioError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_existing_assets_are_adopted_without_changing_files_or_running_audio(self):
        before = {p.relative_to(self.store.root): p.read_bytes()
                  for p in self.store.root.rglob("*") if p.is_file()}
        created = self.create()
        self.assertEqual(created["revision"]["selected_asset"]["asset_id"], self.original)
        self.assertEqual(created["audio_model_calls"], 0)
        self.assertEqual(before, {p: (self.store.root / p).read_bytes() for p in before})
        with open_database(self.db) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], DATABASE_VERSION)
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], APPLICATION_ID)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_empty_session_can_be_created_and_selected_later(self):
        self.sessions.mutate("create", create_request())
        context = self.sessions.context("bgm")
        self.assertIsNone(context["current"]["selected_asset"])
        self.assertEqual((context["measurements"], context["playback"]), ([], []))
        self.assert_code("selection_required", self.sessions.mutate, "feedback", feedback_request())
        self.sessions.mutate("select", select_request(self.original))
        self.assertEqual(self.sessions.show("bgm")["current"]["selected_asset"]["asset_id"], self.original)

    def test_new_workspace_initialization_does_not_require_audio(self):
        root = self.root / "new"
        sessions = SessionService(ArtifactStore(root))
        sessions.mutate("create", create_request())
        self.assertTrue((root / DATABASE_NAME).is_file())
        self.assertEqual(sessions.show("bgm")["session"]["head_revision"], 1)

    def test_selection_restore_branch_and_process_reopen_preserve_history(self):
        self.create()
        candidate = self.candidate()
        # Publishing an audio action alone cannot change the current selection.
        self.assertEqual(self.sessions.show("bgm")["session"]["head_revision"], 1)
        self.sessions.mutate("select", select_request(candidate))
        self.sessions.mutate("select", select_request(expected=2, request_id="restore", restore=1))
        self.sessions.mutate("branch", branch_request())
        reopened = SessionService(ArtifactStore(self.store.root))
        state = reopened.show("bgm")
        self.assertEqual([r["revision"] for r in state["history"]], [3, 2, 1])
        self.assertEqual(state["current"]["parent_revision"], 2)
        self.assertEqual(state["current"]["restored_from_revision"], 1)
        self.assertEqual(state["current"]["selected_asset"]["asset_id"], self.original)
        branch = reopened.show("alternative")
        self.assertEqual(branch["current"]["selected_asset"]["asset_id"], candidate)
        self.assertEqual(branch["session"]["origin"], {"session_id": "bgm", "revision": 2})
        self.assertEqual(len(branch["history"]), 1)
        reopened.mutate("select", select_request(self.original, session_id="alternative", request_id="alt-select"))
        self.assertEqual(reopened.show("bgm")["session"]["head_revision"], 3)

    def test_stale_selection_does_not_overwrite_current_or_create_history(self):
        self.create()
        self.sessions.mutate("select", select_request(self.candidate()))
        self.assert_code("revision_conflict", self.sessions.mutate, "select",
                         select_request(self.original, request_id="stale"))
        self.assertEqual(self.sessions.show("bgm")["session"]["head_revision"], 2)
        self.assert_code("request_not_found", self.sessions.request_status, "stale")

    def test_feedback_is_verbatim_version_bound_and_attributed(self):
        self.create()
        self.sessions.mutate("select", select_request(self.candidate()))
        text = "  测试原话：不要把此内容当成试听结论。\n空格与换行保留。  "
        original_feedback = feedback_request(text=text)
        result = self.sessions.mutate("feedback", original_feedback)
        self.assertEqual(result, self.sessions.mutate("feedback", original_feedback))
        self.assertEqual(result["feedback"]["asset"]["asset_id"], self.original)
        self.assertEqual(result["feedback"]["text"], text)
        self.assertEqual(result["feedback"]["kind"], "user_observation")
        self.sessions.mutate("feedback", feedback_request(revision=2, request_id="agent-note", source="agent"))
        feedback = self.sessions.list_feedback("bgm")
        self.assertEqual([item["kind"] for item in feedback["feedback"]], ["agent_hypothesis", "user_observation"])
        self.assertEqual(self.sessions.show("bgm")["session"]["head_revision"], 2)
        self.assertEqual(len(self.sessions.list_feedback("bgm", revision=1)["feedback"]), 1)

    def test_idempotent_receipts_survive_later_selections_and_conflicting_reuse(self):
        create = create_request(asset_id=self.original)
        original = self.sessions.mutate("create", create)
        selection = select_request(self.candidate())
        selected = self.sessions.mutate("select", selection)
        self.assertEqual(original, self.sessions.mutate("create", create))
        self.assertEqual(selected, self.sessions.request_status("select"))
        self.sessions.mutate("select", select_request(expected=2, request_id="restore", restore=1))
        self.assertEqual(selected, self.sessions.mutate("select", selection))
        self.assertEqual(self.sessions.show("bgm")["session"]["head_revision"], 3)
        self.assert_code("request_conflict", self.sessions.mutate, "create", {**create, "name": "Changed"})
        self.assert_code("request_conflict", self.sessions.mutate, "feedback", feedback_request(request_id="select"))
        self.assert_code("session_exists", self.sessions.mutate, "create", {**create, "request_id": "another"})

    def test_branch_receipt_and_origin_feedback_are_not_copied_or_relabelled(self):
        self.create()
        self.sessions.mutate("select", select_request(self.candidate()))
        note = self.sessions.mutate("feedback", feedback_request(revision=2))
        branch = self.sessions.mutate("branch", branch_request())
        self.sessions.mutate("select", select_request(self.original, session_id="alternative", request_id="alt"))
        self.assertEqual(branch, self.sessions.mutate("branch", branch_request()))
        context = self.sessions.context("alternative")
        self.assertEqual(context["feedback"], [])
        inherited = context["origin_feedback"]["feedback"][0]
        self.assertEqual(inherited["session_id"], "bgm")
        self.assertEqual(inherited["feedback_id"], note["feedback"]["feedback_id"])
        self.assertEqual(inherited["revision"], 2)

    def test_context_combines_real_measurements_and_explicit_unavailable_features(self):
        self.create()
        before = self.sessions.context("bgm")
        self.sessions.mutate("select", select_request(self.candidate()))
        self.sessions.mutate("feedback", feedback_request(revision=2))
        after = self.sessions.context("bgm")
        self.assertAlmostEqual(after["measurements"][0]["levels"]["rms_dbfs"] -
                               before["measurements"][0]["levels"]["rms_dbfs"], -3, places=2)
        self.assertEqual(after["feedback"][0]["revision"], 2)
        self.assertEqual(after["constraints"]["availability"], "available")
        self.assertIsNone(after["constraints"]["policy"])
        self.assertEqual(after["jobs"]["availability"], "available")
        self.assertEqual(after["jobs"]["items"], [])
        self.assertEqual(after["audio_model_calls"], 0)
        self.assertTrue(Path(after["playback"][0]["path"]).is_file())

    def test_pagination_and_invalid_queries(self):
        self.create()
        for index in range(3):
            self.sessions.mutate("feedback", feedback_request(request_id=f"note-{index}"))
        first = self.sessions.list_feedback("bgm", limit=1)
        second = self.sessions.list_feedback("bgm", offset=first["next_offset"], limit=1)
        self.assertNotEqual(first["feedback"][0]["feedback_id"], second["feedback"][0]["feedback_id"])
        self.assertIsNone(self.sessions.list_feedback("bgm", offset=2, limit=1)["next_offset"])
        for index in range(2):
            self.sessions.mutate("create", create_request(f"other-{index}", f"new-{index}"))
        self.assertEqual(self.sessions.list_sessions(limit=1)["next_offset"], 1)
        self.assertEqual(len(self.sessions.context("bgm", feedback_limit=1)["feedback"]), 1)
        for kwargs in ({"offset": -1}, {"limit": 101}, {"limit": True}, {"offset": 0.0}):
            self.assert_code("invalid_request", self.sessions.show, "bgm", **kwargs)
        self.assert_code("invalid_request", self.sessions.list_feedback, "bgm", revision=False)
        self.assert_code("revision_not_found", self.sessions.list_feedback, "bgm", revision=99)
        self.assert_code("session_not_found", self.sessions.show, "missing")

    def test_strict_mutations_reject_unknown_fields_and_types_before_initialization(self):
        invalid = [
            ("create", {**create_request(), "unknown": True}),
            ("create", {**create_request(), "name": "  \n"}),
            ("create", {**create_request(), "session_id": "../escape"}),
            ("select", select_request(self.original, expected=True)),
            ("select", select_request(self.original, expected=1.0)),
            ("select", {**select_request(self.original), "from_revision": 1}),
            ("feedback", {**feedback_request(), "source": "model_listened"}),
            ("feedback", {**feedback_request(), "text": "x" * 4001}),
            ("feedback", {**feedback_request(), "revision": float("nan")}),
        ]
        for operation, value in invalid:
            with self.subTest(operation=operation, value=value):
                self.assert_code("invalid_request", self.sessions.mutate, operation, value)
        self.assertFalse(self.db.exists())

    def test_failed_initialization_and_migration_roll_back(self):
        # A failure after DDL must not leave a version marker or half a schema.
        broken = {1: (*MIGRATIONS[1], "INVALID SQL")}
        with patch("matter_audio_core.session_db.MIGRATIONS", broken):
            self.assert_code("session_database_error", self.create)
        with open_database(self.db) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master").fetchall(), [])
        self.create()
        self.assertEqual(self.sessions.show("bgm")["session"]["head_revision"], 1)

    def test_failure_after_selection_write_rolls_back_revision_head_and_receipt(self):
        self.create()
        selection = select_request(self.candidate())
        original_select = self.sessions._select

        def fail_after_write(*args):
            original_select(*args)
            raise RuntimeError("simulated interruption before commit")

        with patch.object(self.sessions, "_select", side_effect=fail_after_write):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                self.sessions.mutate("select", selection)
        reopened = SessionService(self.store)
        self.assertEqual(len(reopened.show("bgm")["history"]), 1)
        self.assert_code("request_not_found", reopened.request_status, "select")
        self.assertEqual(reopened.mutate("select", selection)["revision"]["revision"], 2)

    def test_read_queries_do_not_create_a_database_or_change_its_bytes(self):
        self.assert_code("session_database_missing", self.sessions.show, "bgm")
        self.assertFalse(self.db.exists())
        missing = self.root / "missing"
        self.assert_code("workspace_missing", SessionService(ArtifactStore(missing)).show, "bgm")
        self.assertFalse(missing.exists())
        self.create()
        before = self.db.read_bytes()
        self.sessions.context("bgm")
        self.sessions.list_sessions()
        self.sessions.list_feedback("bgm")
        self.assertEqual(self.db.read_bytes(), before)

    def test_unknown_future_or_unrelated_databases_are_not_modified(self):
        self.create()
        with open_database(self.db) as connection:
            connection.execute("PRAGMA user_version = 999")
        before = self.db.read_bytes()
        self.assert_code("session_schema_newer", self.sessions.show, "bgm")
        self.assert_code("session_schema_newer", self.sessions.mutate, "create", create_request("new", "new"))
        self.assertEqual(self.db.read_bytes(), before)
        with open_database(self.db) as connection:
            connection.execute("PRAGMA user_version = 1")
            connection.execute("PRAGMA application_id = 123")
        self.assert_code("session_database_unrecognized", self.sessions.show, "bgm")

    def test_unrelated_zero_version_database_is_not_adopted(self):
        with open_database(self.db) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.execute("INSERT INTO unrelated VALUES ('keep me')")
        before = self.db.read_bytes()
        self.assert_code("session_database_unrecognized", self.create)
        self.assertEqual(before, self.db.read_bytes())

    def test_corrupt_database_is_a_structured_cli_error(self):
        self.db.write_bytes(b"not a sqlite database")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = run(["--workspace", str(self.store.root), "session", "show", "bgm"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "session_database_error")

    def test_product_scope_is_checked_in_workspace_and_database(self):
        self.create()
        wrong = SessionService(ArtifactStore(self.store.root, product="another-product"))
        self.assert_code("workspace_mismatch", wrong.show, "bgm")
        with open_database(self.db) as connection:
            connection.execute("UPDATE metadata SET value = 'another-product' WHERE key = 'product'")
        self.assert_code("workspace_mismatch", self.sessions.show, "bgm")

    def test_non_audio_and_tampered_assets_cannot_be_selected_or_played(self):
        self.create()
        result = self.store.transact("opaque", {"fixture": True}, lambda publication: {
            "reference": publication.add(b"source receipt", {"codec": "opaque"}, role="source")})
        self.assert_code("unsupported_session_asset", self.sessions.mutate, "select",
                         select_request(result["outputs"][0]["asset_id"]))
        record, data = self.store.asset(self.original)
        (self.store.root / record["locator"]).write_bytes(data[:-1])
        self.assert_code("integrity_error", self.sessions.show, "bgm")
        self.assert_code("integrity_error", self.sessions.context, "bgm")

    def test_database_sidecar_symlinks_are_rejected(self):
        self.create()
        sidecar = self.db.with_name(self.db.name + "-journal")
        try:
            sidecar.symlink_to(self.source)
        except OSError:
            self.skipTest("This Windows environment cannot create symlinks")
        self.assert_code("unsafe_path", self.sessions.show, "bgm")

    def _concurrent(self, requests):
        barrier = threading.Barrier(len(requests))
        results = []

        def worker(value):
            try:
                barrier.wait(5)
                results.append(self.sessions.mutate("select", value))
            except Exception as exc:
                results.append(exc)

        threads = [threading.Thread(target=worker, args=(value,)) for value in requests]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
            self.assertFalse(thread.is_alive())
        return results

    def test_concurrent_identical_requests_commit_once(self):
        self.create()
        selection = select_request(self.candidate())
        results = self._concurrent([selection, copy.deepcopy(selection)])
        self.assertTrue(all(isinstance(value, dict) for value in results), results)
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.sessions.show("bgm")["history"]), 2)

    def test_concurrent_different_selections_have_one_winner(self):
        self.create()
        results = self._concurrent([select_request(self.original, request_id="choice-a"),
                                    select_request(self.candidate(), request_id="choice-b")])
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1, results)
        self.assertEqual([value.code for value in results if isinstance(value, AudioError)], ["revision_conflict"])
        self.assertEqual(self.sessions.show("bgm")["session"]["head_revision"], 2)

    def test_sqlite_recovers_uncommitted_spilled_writes_after_process_exit(self):
        self.create()
        script = """
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute('PRAGMA cache_size = 1')
connection.execute('BEGIN IMMEDIATE')
for number in range(100):
    connection.execute('INSERT INTO feedback (feedback_id, session_id, revision, source, text, listening_context, created_at) VALUES (?, ?, 1, ?, ?, ?, ?)',
                       (str(number), 'bgm', 'agent', 'x' * 4000, 'interruption fixture', 'fixture'))
os._exit(17)
"""
        completed = subprocess.run([sys.executable, "-c", script, str(self.db)], timeout=20)
        self.assertEqual(completed.returncode, 17)
        self.assertEqual(SessionService(self.store).list_feedback("bgm")["feedback"], [])
        self.assertEqual(self.sessions.show("bgm")["session"]["head_revision"], 1)


    def test_context_retrieves_older_feedback_for_a_restored_asset(self):
        self.create()
        note = self.sessions.mutate("feedback", feedback_request())
        self.sessions.mutate("select", select_request(self.candidate()))
        self.sessions.mutate("feedback", feedback_request(revision=2, request_id="recent-b"))
        self.sessions.mutate("select", select_request(expected=2, request_id="restore", restore=1))
        context = self.sessions.context("bgm", feedback_limit=1)
        self.assertEqual(context["feedback"][0]["revision"], 2)
        self.assertEqual(context["current_feedback"][0]["feedback_id"], note["feedback"]["feedback_id"])


if __name__ == "__main__":
    unittest.main()
