import copy
import tempfile
import unittest
from array import array
from pathlib import Path

from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.contracts import canonical
from matter_audio_core.errors import AudioError
from matter_audio_core.media import PCM, encode_wav, sample_bytes
from matter_audio_core.sessions import SessionService


class RevisionApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = ArtifactStore(self.root / "workspace")
        self.assets = []
        for index, amplitude in enumerate((100, 200)):
            path = self.root / f"source-{index}.wav"
            path.write_bytes(encode_wav(PCM(sample_bytes(array("h", [amplitude] * 16)), 8000, 1)))
            self.assets.append(self.store.import_wav(path, f"import-{index}")["outputs"][0])
        self.sessions = SessionService(self.store)

    def create(self, session_id="main", *, empty=False):
        request = {"schema": "matter-session-create/v1", "request_id": f"create-{session_id}",
                   "session_id": session_id, "name": session_id}
        if not empty:
            request["asset_id"] = self.assets[0]["asset_id"]
        return self.sessions.mutate("create", request)["revision"]

    def lock(self):
        return self.sessions.mutate("constraints", {
            "schema": "matter-constraints-set/v1", "request_id": "lock", "session_id": "main",
            "expected_revision": 1, "regions": [{"start_frame": 0, "end_frame": 4}],
        })["revision"]

    def test_historical_query_survives_head_change_and_unrelated_head_corruption(self):
        original = self.create()
        locked = self.lock()
        self.sessions.mutate("constraints", {
            "schema": "matter-constraints-set/v1", "request_id": "unlock", "session_id": "main",
            "expected_revision": 2, "regions": [],
        })
        self.sessions.mutate("select", {
            "schema": "matter-session-select/v1", "request_id": "select-b", "session_id": "main",
            "expected_revision": 3, "asset_id": self.assets[1]["asset_id"],
        })
        before = self.sessions.show("main")
        self.assertEqual(self.sessions.revision("main", 1), original)
        self.assertEqual(self.sessions.revision("main", 2), locked)
        self.assertEqual(self.sessions.show("main"), before)
        # The requested snapshot must not depend on an unrelated newer asset.
        (self.store.root / self.assets[1]["locator"]).write_bytes(b"corrupt current asset")
        reopened = SessionService(ArtifactStore(self.store.root))
        self.assertEqual(reopened.revision("main", 2), locked)

    def test_empty_snapshot_and_returned_values_do_not_mutate_saved_history(self):
        empty = self.create("empty", empty=True)
        self.assertEqual(self.sessions.revision("empty", 1), empty)
        original = self.create()
        result = self.sessions.revision("main", 1)
        result["selected_asset"]["digest"]["hex"] = "0" * 64
        self.assertEqual(self.sessions.revision("main", 1), original)

    def test_invalid_and_missing_revision_queries_are_explicit(self):
        self.create()
        for session_id, number, expected in (("main", True, "invalid_request"),
                                             ("main", 0, "invalid_request"),
                                             ("main", 99, "revision_not_found"),
                                             ("missing", 1, "session_not_found")):
            with self.subTest(session_id=session_id, revision=number):
                with self.assertRaises(AudioError) as raised:
                    self.sessions.revision(session_id, number)
                self.assertEqual(raised.exception.code, expected)

    def test_requested_asset_and_saved_protection_are_verified(self):
        self.create()
        locked = self.lock()
        broken = copy.deepcopy(locked["constraints"])
        broken["regions"][0]["pcm_digest"]["hex"] = "0" * 64
        with self.sessions.database.transaction(write=True) as connection:
            connection.execute("UPDATE revisions SET constraints_json = ? WHERE session_id = ? AND revision = ?",
                               (canonical(broken).decode(), "main", 2))
        with self.assertRaises(AudioError) as raised:
            self.sessions.revision("main", 2)
        self.assertEqual(raised.exception.code, "constraint_violation")
        (self.store.root / self.assets[0]["locator"]).write_bytes(b"corrupt historical asset")
        with self.assertRaises(AudioError):
            self.sessions.revision("main", 1)


if __name__ == "__main__":
    unittest.main()
