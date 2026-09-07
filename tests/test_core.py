from __future__ import annotations

import contextlib
import io
import json
import struct
import tempfile
import threading
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

from matter_audio_core.actions import ActionService, Operation, Registry
from matter_audio_core.artifacts import ArtifactStore, publish_directory
from matter_audio_core.cli import run
from matter_audio_core.contracts import canonical, digest, read_json, request
from matter_audio_core.errors import AudioError
from matter_audio_core.media import PCM, Q24, decode_wav, encode_wav, gain, gain_multiplier, inspect, sample_bytes, trim


def wav(values, rate=8000, channels=1):
    return encode_wav(PCM(sample_bytes(array("h", values)), rate, channels))


class MediaTests(unittest.TestCase):
    def test_signed_half_rounding_and_unity(self):
        pcm = decode_wav(wav([-32768, -3, -1, 0, 1, 3, 32767]))
        result, clipped = gain(pcm, Q24 // 2)
        self.assertEqual(list(result.samples()), [-16384, -2, -1, 0, 1, 2, 16384])
        self.assertEqual(clipped, 0)
        self.assertEqual(gain(pcm, Q24)[0], pcm)
        self.assertEqual(gain_multiplier(0), Q24)
        self.assertEqual(gain_multiplier(-6.020599913279624), Q24 // 2)

    def test_clipping_is_explicit(self):
        pcm = decode_wav(wav([-20000, 20000]))
        with self.assertRaisesRegex(AudioError, "overflow"):
            gain(pcm, Q24 * 2)
        output, count = gain(pcm, Q24 * 2, "saturate")
        self.assertEqual(list(output.samples()), [-32768, 32767])
        self.assertEqual(count, 2)

    def test_trim_uses_frames_not_interleaved_samples(self):
        pcm = decode_wav(wav([1, -1, 2, -2, 3, -3], channels=2))
        self.assertEqual(list(trim(pcm, 1, 3).samples()), [2, -2, 3, -3])
        for start, end in [(1, 1), (-1, 1), (0, 4)]:
            with self.assertRaises(AudioError):
                trim(pcm, start, end)

    def test_corrupt_riff_and_data_are_rejected(self):
        source = wav([1, 2, 3])
        for corrupted in [source[:-1], source + b"x", b"RF64" + source[4:]]:
            with self.assertRaises(AudioError):
                decode_wav(corrupted)
        broken = bytearray(source)
        struct.pack_into("<I", broken, 40, 1000)
        with self.assertRaises(AudioError):
            decode_wav(bytes(broken))

    def test_stereo_levels_and_silence_pagination(self):
        pcm = decode_wav(wav([0, 1000, 0, -1000, 0, 1000], channels=2))
        finding = inspect(pcm, window_frames=1, offset=1, limit=1)
        self.assertEqual(finding["channel_levels"][0]["rms_dbfs"], None)
        self.assertEqual(finding["channel_levels"][1]["rms_pcm16"], 1000)
        self.assertEqual(finding["windows"][0]["start_frame"], 1)
        self.assertEqual(finding["next_offset"], 2)
        json.dumps(finding, allow_nan=False)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.input = self.root / "source.wav"
        self.original = wav([-32768, -3, -1, 0, 1, 3, 32767])
        self.input.write_bytes(self.original)
        self.store = ArtifactStore(self.root / "workspace")
        self.imported = self.store.import_wav(self.input, "import-1")
        self.asset_id = self.imported["outputs"][0]["asset_id"]
        self.service = ActionService(self.store)

    def test_import_is_snapshot_and_repeated_request_has_no_new_object(self):
        self.assertEqual(self.store.import_wav(self.input, "import-1"), self.imported)
        self.assertEqual(len(list((self.store.root / "objects").iterdir())), 1)
        self.input.write_bytes(wav([6, 5, 4]))
        self.assertEqual(self.store.asset(self.asset_id)[1], self.original)
        with self.assertRaisesRegex(AudioError, "different"):
            self.store.import_wav(self.input, "import-1")

    def test_gain_lineage_and_source_identity(self):
        req = request("gain-1", "gain/v1", self.asset_id, {"db": -3})
        result = self.service.execute(req)
        self.assertEqual(result, self.service.execute(req))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["audio_model_calls"], 0)
        self.assertEqual(result["outputs"][0]["parents"][0]["asset_id"], self.asset_id)
        self.assertEqual(self.input.read_bytes(), self.original)
        self.assertEqual(self.store.asset(self.asset_id)[1], self.original)

    def test_request_conflict_and_resolution_preview(self):
        req = request("same", "gain/v1", self.asset_id, {"db": -3})
        resolution = self.service.resolve(req)
        self.assertFalse(self.store._request_path("same").exists())
        with self.assertRaisesRegex(AudioError, "changed"):
            self.service.execute(req, expected_resolution_digest="0" * 64)
        self.service.execute(req, expected_resolution_digest=resolution["digest"]["hex"])
        req["parameters"]["db"] = -6
        with self.assertRaisesRegex(AudioError, "different"):
            self.service.execute(req)

    def test_strict_numeric_types_unknown_fields_and_operations(self):
        for parameters in [{"db": True}, {"db": -3, "magic": True}, {"db": float("nan")}]:
            with self.assertRaises(AudioError):
                self.service.resolve(request("invalid", "gain/v1", self.asset_id, parameters))
        for start in [False, 0.0]:
            with self.assertRaises(AudioError):
                self.service.resolve(request("invalid", "trim/v1", self.asset_id, {"start_frame": start, "end_frame": 2}))
        with self.assertRaises(AudioError):
            self.service.resolve(request("invalid", "model-magic/v1", self.asset_id, {}))

    def test_seconds_resolution_rounds_half_up(self):
        req = request("time", "trim/v1", self.asset_id, {"start_seconds": 0.0000625, "end_seconds": 0.0008125})
        resolved = self.service.resolve(req)["effective_parameters"]
        self.assertEqual((resolved["start_frame"], resolved["end_frame"]), (1, 7))
        output = self.service.execute(req)["outputs"][0]
        self.assertEqual(output["media"]["frame_count"], 6)

    def test_failed_action_is_queryable_and_has_no_audio(self):
        req = request("loud", "gain/v1", self.asset_id, {"db": 6})
        failed = self.service.execute(req)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["outputs"], [])
        self.assertEqual(failed["error"]["code"], "clipping_rejected")
        self.assertEqual(self.service.execute(req), failed)

    def test_analysis_can_have_zero_outputs(self):
        result = self.service.execute(request("inspection", "inspect/v1", self.asset_id, {"limit": 1}))
        self.assertEqual(result["outputs"], [])
        self.assertEqual(result["findings"][0]["kind"], "measurement")

    def test_asset_tampering_and_extra_files_rejected(self):
        record, data = self.store.asset(self.asset_id)
        path = self.store.root / record["locator"]
        path.write_bytes(wav([1, 2]))
        with self.assertRaises(AudioError):
            self.store.asset(self.asset_id)
        path.write_bytes(data)
        (path.parent / "partial.wav").write_bytes(data)
        with self.assertRaisesRegex(AudioError, "inventory"):
            self.store.asset(self.asset_id)

    def test_incomplete_publication_is_pending_and_not_reexecuted(self):
        calls = []
        original_publish = self.store._publish

        def failing_publish(target, files):
            if target.parent.name == "objects":
                raise OSError("simulated crash before publication")
            return original_publish(target, files)

        def producer(publication):
            calls.append(1)
            return {"findings": []}

        with patch.object(self.store, "_publish", side_effect=failing_publish):
            with self.assertRaises(OSError):
                self.store.transact("crash", {"fixture": 1}, producer)
        with self.assertRaisesRegex(AudioError, "recovery"):
            self.store.transact("crash", {"fixture": 1}, producer)
        self.assertEqual(calls, [1])
        self.assertEqual(len(self.store.list_assets()["assets"]), 1)

    def test_asset_rechecked_after_group_validation(self):
        load_group = self.store._load_group

        def replace_after_verification(group_id):
            result = load_group(group_id)
            path = self.store.root / result["outputs"][0]["locator"]
            path.write_bytes(wav([100, 200, 300]))
            return result

        with patch.object(self.store, "_load_group", side_effect=replace_after_verification):
            with self.assertRaisesRegex(AudioError, "changed after"):
                self.store.asset(self.asset_id)

    def test_concurrent_request_has_one_producer(self):
        entered, finish = threading.Event(), threading.Event()
        calls, results = [], []

        def producer(publication):
            calls.append(1)
            entered.set()
            if not finish.wait(5):
                raise RuntimeError("test timeout")
            return {"findings": []}

        thread = threading.Thread(target=lambda: results.append(self.store.transact("concurrent", {"x": 1}, producer)))
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaisesRegex(AudioError, "recovery"):
                self.store.transact("concurrent", {"x": 1}, producer)
        finally:
            finish.set()
            thread.join(5)
        self.assertEqual(calls, [1])
        self.assertEqual(self.store.transact("concurrent", {"x": 1}, producer), results[0])

    def test_no_replace_including_empty_target(self):
        for populated in [False, True]:
            with tempfile.TemporaryDirectory(dir=self.root) as folder:
                source, target = Path(folder) / "source", Path(folder) / "target"
                source.mkdir()
                target.mkdir()
                (source / "payload").write_text("new")
                if populated:
                    (target / "payload").write_text("old")
                with self.assertRaises(OSError):
                    publish_directory(source, target)
                self.assertTrue(source.exists())
                if populated:
                    self.assertEqual((target / "payload").read_text(), "old")

    def test_workspace_product_separation(self):
        with self.assertRaisesRegex(AudioError, "different product"):
            ArtifactStore(self.store.root, product="another-product").asset(self.asset_id)


class CliTests(unittest.TestCase):
    def capture(self, arguments):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = run(arguments)
        self.assertEqual(err.getvalue(), "")
        return status, json.loads(out.getvalue())

    def test_capabilities_are_read_only_and_do_not_import_models(self):
        import sys
        before = set(sys.modules)
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "unused"
            code, value = self.capture(["--workspace", str(workspace), "capabilities", "--json"])
            self.assertEqual(code, 0)
            self.assertFalse(workspace.exists())
            self.assertEqual({op["operation"] for op in value["operations"]},
                             {"inspect/v1", "gain/v1", "trim/v1", "fade/v1", "mix/v1", "splice/v1"})
        self.assertFalse({"torch", "numpy", "miniaudio", "score_matter"} & (set(sys.modules) - before))

    def test_invalid_cli_is_machine_readable(self):
        code, value = self.capture(["unsupported", "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(value["error"]["code"], "invalid_arguments")

    def test_duplicate_json_and_nonfinite_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            for text in ['{"x":1,"x":2}', '{"x":NaN}']:
                path.write_text(text)
                with self.assertRaises(AudioError):
                    read_json(path)


if __name__ == "__main__":
    unittest.main()
