"""Comparison persistence and HTTP boundaries; no human listening is simulated."""

import copy
import http.client
import json
import tempfile
import threading
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

from matter_audio_core.actions import ActionService
from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.audition import AuditionService
from matter_audio_core.audition_server import create_server
from matter_audio_core.contracts import request
from matter_audio_core.delivery import ExportService
from matter_audio_core.errors import AudioError
from matter_audio_core.media import PCM, encode_wav, sample_bytes
from matter_audio_core.sessions import SessionService


class AuditionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = self.root / "fixture.wav"
        self.wav = encode_wav(PCM(sample_bytes(array("h", [-2000, 2000] * 400)), 8000, 1))
        source.write_bytes(self.wav)
        self.store = ArtifactStore(self.root / "workspace")
        self.a = self.store.import_wav(source, "import")["outputs"][0]["asset_id"]
        self.b = ActionService(self.store).execute(request("gain", "gain/v1", self.a, {"db": -3}))["outputs"][0]["asset_id"]
        self.sessions = SessionService(self.store)
        self.sessions.mutate("create", {"schema": "matter-session-create/v1", "request_id": "create",
            "session_id": "test", "name": "Synthetic fixture", "asset_id": self.a})
        self.spec = {"schema": "matter-audition-create/v1", "request_id": "compare", "audition_id": "compare",
            "session_id": "test", "expected_revision": 1, "name": "Test comparison", "reference_asset_id": self.a,
            "candidates": [{"label": "B", "asset_id": self.b}]}
        self.audition = AuditionService(self.store)
        self.audition.create(self.spec)

    def select(self, asset=None, expected=1, rid="select", restore=None):
        return {"schema": "matter-session-select/v1", "request_id": rid, "session_id": "test",
                "expected_revision": expected, **({"from_revision": restore} if restore else {"asset_id": asset or self.b})}

    def export(self, expected=1, rid="export"):
        return {"schema": "matter-export/v1", "request_id": rid, "session_id": "test", "expected_revision": expected}

    def code(self, code, function, *args):
        with self.assertRaises(AudioError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)

    def start_server(self):
        self.server = create_server(self.store, "compare")
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            self.server.shutdown()
            self.server.server_close()
            thread.join(3)
        self.addCleanup(cleanup)

    def http(self, path, body=None, *, token=True, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        supplied = {"X-Matter-Token": self.server.matter_token} if token else {}
        if body is not None:
            supplied["Content-Type"] = "application/json"
        supplied.update(headers or {})
        try:
            conn.request("POST" if body is not None else "GET", path,
                         json.dumps(body).encode() if body is not None else None, headers=supplied)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_comparison_survives_reopen_and_replay_but_rejects_id_rebinding(self):
        original = self.audition.show("compare")
        self.sessions.mutate("select", self.select())
        reopened = AuditionService(ArtifactStore(self.store.root))
        self.assertEqual(reopened.create(self.spec), original)
        changed = copy.deepcopy(self.spec)
        changed["name"] = "Other"
        self.code("request_conflict", reopened.create, changed)
        state = reopened.state("compare")
        self.assertEqual(reopened.list("test")["items"][0]["audition_id"], "compare")
        self.assertEqual(self.sessions.context("test")["auditions"], reopened.list("test"))
        self.assertEqual(state["session"]["current"]["selected_asset"]["asset_id"], self.b)
        self.assertEqual([x["asset_id"] for x in state["candidates"]], [self.a, self.b])
        self.assertTrue(all(1 <= len(x["waveform"]) <= 600 for x in state["candidates"]))

    def test_export_binds_revision_and_replays_after_head_changes(self):
        exports = ExportService(self.store)
        first = exports.create(self.export())
        self.assertEqual(Path(first["path"]).read_bytes(), self.wav)
        self.sessions.mutate("select", self.select())
        self.assertEqual(exports.create(self.export()), first)
        self.code("request_conflict", exports.create, self.export(2))
        self.code("revision_conflict", exports.create, self.export(1, "stale"))
        second = exports.create(self.export(2, "second"))
        self.assertEqual(Path(second["path"]).read_bytes(), self.store.asset(self.b)[1])
        self.assertEqual(Path(first["path"]).read_bytes(), self.wav)

    def test_export_checks_receipt_and_audio_integrity(self):
        exports = ExportService(self.store)
        first = exports.create(self.export())
        path = Path(first["path"])
        path.write_bytes(self.wav[:-2] + b"xx")
        self.code("integrity_error", exports.show, "export")

    def test_export_publication_failure_never_returns_partial_delivery(self):
        exports = ExportService(self.store)
        with patch.object(self.store, "_publish", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                exports.create(self.export())
        self.code("export_not_found", exports.show, "export")
        self.assertEqual(Path(exports.create(self.export())["path"]).read_bytes(), self.wav)

    def test_http_enforces_token_origin_host_and_asset_scope(self):
        self.start_server()
        self.assertEqual(self.http("/")[0], 200)
        self.assertEqual(self.http("/api/state", token=False)[0], 403)
        self.assertEqual(self.http("/api/state", headers={"X-Matter-Token": "é"})[0], 403)
        self.assertEqual(self.http("/api/state", headers={"Origin": "http://evil.invalid"})[0], 403)
        self.assertEqual(self.http("/api/state", headers={"Host": "evil.invalid"})[0], 403)
        status, headers, body = self.http("/api/state")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(json.loads(body)["session"]["session"]["head_revision"], 1)
        other = ActionService(self.store).execute(request("other", "gain/v1", self.a, {"db": -6}))["outputs"][0]["asset_id"]
        self.assertEqual(self.http("/api/audio/" + other)[0], 403)
        self.assertEqual(self.http("/api/select", self.select(other))[0], 403)
        self.assertEqual(self.http("/api/audio/../../fixture.wav")[0], 403)
        self.assertEqual(self.http("/api/select", {**self.select(), "session_id": "unrelated"})[0], 403)

    def test_audio_byte_ranges_and_invalid_ranges(self):
        self.start_server()
        path = "/api/audio/" + self.a
        for requested, expected in [("bytes=0-43", self.wav[:44]), ("bytes=-2", self.wav[-2:]),
                                    ("bytes=44-", self.wav[44:])]:
            status, headers, body = self.http(path, headers={"Range": requested})
            self.assertEqual((status, body), (206, expected))
            self.assertIn("Content-Range", headers)
        for requested in ("bytes=-0", "bytes=999999-", "bytes=9-2", "bytes=0-1,2-3", "bytes=-"):
            self.assertEqual(self.http(path, headers={"Range": requested})[0], 416)

    def test_select_feedback_restore_and_export_share_persistent_state(self):
        self.start_server()
        body = self.select()
        self.assertEqual(self.http("/api/select", body)[0], 200)
        self.assertEqual(self.http("/api/select", body)[0], 200)  # lost-response retry
        self.assertEqual(self.http("/api/select", self.select(self.a, rid="stale"))[0], 409)
        feedback = {"schema": "matter-feedback/v1", "request_id": "note", "session_id": "test", "revision": 2,
            "source": "agent", "text": "Automated fixture, no listening judgment.", "listening_context": "HTTP test"}
        self.assertEqual(self.http("/api/feedback", feedback)[0], 200)
        self.assertEqual(self.http("/api/feedback", feedback)[0], 200)
        self.assertEqual(len(self.sessions.list_feedback("test")["feedback"]), 1)
        self.assertEqual(self.http("/api/select", self.select(expected=2, rid="restore", restore=1))[0], 200)
        self.assertEqual(self.http("/api/export", self.export(3))[0], 200)
        self.assertEqual(self.http("/api/export/export")[2], self.wav)
        state = AuditionService(ArtifactStore(self.store.root)).state("compare")
        self.assertEqual(state["session"]["session"]["head_revision"], 3)
        self.assertEqual(state["session"]["current"]["selected_asset"]["asset_id"], self.a)


if __name__ == "__main__":
    unittest.main()
