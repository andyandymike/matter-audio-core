"""Numeric layering fixtures and exact write-boundary tests."""

import tempfile
import unittest
from array import array
from pathlib import Path

from matter_audio_core.actions import ActionService
from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.composition import mix, resolve_mix, resolve_splice, splice, observed_changes
from matter_audio_core.contracts import request
from matter_audio_core.errors import AudioError
from matter_audio_core.media import PCM, decode_wav, encode_wav, sample_bytes
from matter_audio_core.sessions import SessionService


def pcm(values, channels=1, rate=8000):
    return PCM(sample_bytes(array("h", values)), rate, channels)


class CompositionTests(unittest.TestCase):
    def test_mix_rounds_the_sum_once_and_preserves_unwritten_frames(self):
        inputs = [pcm([100] * 8), pcm([10, -10, 20, -20])]
        parameters = resolve_mix({"layers": [{"input_index": 1, "source_start_frame": 0,
            "source_end_frame": 4, "offset_frame": 2}]}, inputs)
        output, report = mix(parameters, inputs)
        self.assertEqual(list(output.samples()), [100, 100, 110, 90, 120, 80, 100, 100])
        self.assertEqual(report["observed_changes"]["outside_changed_sample_count"], 0)
        self.assertEqual(report["observed_changes"]["changed_frame_count"], 4)

    def test_gain_and_fade_combination_uses_one_q48_rounding(self):
        inputs = [pcm([0] * 6), pcm([1, 1, -1, -1, 1, 1])]
        parameters = resolve_mix({"layers": [{"input_index": 1, "source_start_frame": 0,
            "source_end_frame": 6, "offset_frame": 0, "fade_in_frames": 3, "fade_out_frames": 3}]}, inputs)
        self.assertEqual(list(mix(parameters, inputs)[0].samples()), [0, 1, -1, -1, 1, 0])
        parameters["layers"][0]["gain_q24"] = 1 << 23
        self.assertEqual(list(mix(parameters, inputs)[0].samples()), [0, 0, -1, -1, 0, 0])

    def test_overflow_rejects_by_default_and_counts_saturation(self):
        inputs = [pcm([30000, -30000]), pcm([30000, -30000])]
        parameters = resolve_mix({"layers": [{"input_index": 1, "source_start_frame": 0,
            "source_end_frame": 2, "offset_frame": 0}]}, inputs)
        with self.assertRaises(AudioError) as caught:
            mix(parameters, inputs)
        self.assertEqual(caught.exception.code, "clipping_rejected")
        parameters["clip"] = "saturate"
        output, report = mix(parameters, inputs)
        self.assertEqual(list(output.samples()), [32767, -32768])
        self.assertEqual(report["overflow_sample_count"], 2)

    def test_splice_transition_endpoints_and_stereo_channels(self):
        inputs = [pcm([100, -100] * 10, 2), pcm([1000, -1000] * 10, 2)]
        parameters = resolve_splice({"start_frame": 2, "end_frame": 8, "transition_frames": 3}, inputs)
        output, report = splice(parameters, inputs)
        self.assertEqual(list(output.samples())[::2], [100, 100, 100, 550, 1000, 1000, 550, 100, 100, 100])
        self.assertEqual(list(output.samples())[1::2], [-100, -100, -100, -550, -1000, -1000, -550, -100, -100, -100])
        self.assertEqual(report["observed_changes"]["outside_changed_sample_count"], 0)
        self.assertEqual(report["transition"], [{"start_frame": 2, "end_frame": 5}, {"start_frame": 5, "end_frame": 8}])

    def test_replacement_window_can_come_from_short_clip(self):
        inputs = [pcm([1] * 8), pcm([10, 20])]
        output, _ = splice(resolve_splice({"start_frame": 3, "end_frame": 5, "replacement_start_frame": 0}, inputs), inputs)
        self.assertEqual(list(output.samples()), [1, 1, 1, 10, 20, 1, 1, 1])

    def test_invalid_ranges_formats_and_missing_layers_fail_before_execution(self):
        for inputs, parameters in [([pcm([0] * 8), pcm([0] * 8, rate=16000)], {"start_frame": 1, "end_frame": 4}),
                                   ([pcm([0] * 8), pcm([0] * 8)], {"start_frame": 2, "end_frame": 4, "transition_frames": 2}),
                                   ([pcm([0] * 8), pcm([0])], {"start_frame": 2, "end_frame": 4})]:
            with self.assertRaises(AudioError):
                resolve_splice(parameters, inputs)
        with self.assertRaises(AudioError):
            resolve_mix({"layers": []}, [pcm([0] * 8), pcm([0] * 8)])

    def test_report_counts_changes_outside_allowed_write_and_bounds_range_inventory(self):
        report = observed_changes(pcm([0] * 200), pcm([0, 1] * 100), [{"start_frame": 50, "end_frame": 100}])
        self.assertEqual((report["changed_sample_count"], report["outside_changed_sample_count"]), (100, 75))
        self.assertEqual((len(report["changed_ranges"]), report["changed_range_count"], report["ranges_truncated"]), (64, 100, True))

    def test_protected_session_multilayer_lineage_and_continuous_splice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, assets = ArtifactStore(root / "workspace"), []
            for index, value in enumerate(([100] * 12, [10] * 4, [20] * 4)):
                path = root / f"{index}.wav"
                path.write_bytes(encode_wav(pcm(value)))
                assets.append(store.import_wav(path, f"import-{index}")["outputs"][0]["asset_id"])
            sessions, actions = SessionService(store), ActionService(store)
            sessions.mutate("create", {"schema": "matter-session-create/v1", "request_id": "create",
                "session_id": "mix", "name": "Synthetic layering", "asset_id": assets[0]})
            sessions.mutate("constraints", {"schema": "matter-constraints-set/v1", "request_id": "lock",
                "session_id": "mix", "expected_revision": 1, "regions": [{"start_frame": 0, "end_frame": 4}]})
            action = {**request("mix", "mix/v1", assets[0], {"layers": [
                {"input_index": 1, "source_start_frame": 0, "source_end_frame": 4, "offset_frame": 4},
                {"input_index": 2, "source_start_frame": 0, "source_end_frame": 4, "offset_frame": 8}]}),
                "inputs": assets, "protection": {"session_id": "mix", "revision": 2}}
            result = actions.execute(action)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual([p["role"] for p in result["outputs"][0]["parents"]], ["source", "layer", "layer"])
            selected = result["outputs"][0]["asset_id"]
            sessions.mutate("select", {"schema": "matter-session-select/v1", "request_id": "select", "session_id": "mix",
                "expected_revision": 2, "asset_id": selected})
            followup = {**request("splice", "splice/v1", selected, {"start_frame": 8, "end_frame": 12, "replacement_start_frame": 0}),
                "inputs": [selected, assets[1]], "protection": {"session_id": "mix", "revision": 3}}
            revised = actions.execute(followup)
            self.assertEqual(revised["findings"][0]["protection"]["status"], "verified")
            output = decode_wav(store.asset(revised["outputs"][0]["asset_id"])[1])
            self.assertEqual(list(output.samples()), [100] * 4 + [110] * 4 + [10] * 4)
            action["request_id"] = "bad"
            action["parameters"]["layers"][0]["offset_frame"] = 0
            with self.assertRaises(AudioError) as caught:
                actions.resolve(action)
            self.assertEqual(caught.exception.code, "constraint_violation")


if __name__ == "__main__":
    unittest.main()
