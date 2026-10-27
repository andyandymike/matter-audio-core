import json
import math
import subprocess
import sys
import tempfile
import unittest
from array import array
from pathlib import Path

from matter_audio_core.actions import ActionService, Registry
from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.contracts import MAX_JSON_BYTES, canonical, request
from matter_audio_core.errors import AudioError
from matter_audio_core.execution import checkpoints
from matter_audio_core.media import PCM, encode_wav, sample_bytes
from matter_audio_core.quality import PROFILE, quality_operation, resolve, scan
from matter_audio_core.sessions import SessionService


def pcm(values, channels=1, rate=8000):
    return PCM(sample_bytes(array("h", values)), rate, channels)


def warnings(report, kind):
    return [item for item in report["warnings"] if item["kind"] == kind]


class QualityTests(unittest.TestCase):
    def test_fresh_cli_immediate_and_persisted_reports_keep_source_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "signal.wav"
            original = encode_wav(pcm([0, 32767, 1000, -32768, 0]))
            source.write_bytes(original)
            store = ArtifactStore(root / "workspace")
            asset = store.import_wav(source, "import")["outputs"][0]
            def call(*arguments):
                result = subprocess.run([sys.executable, "-m", "matter_audio_core",
                    "--workspace", str(store.root), *arguments, "--json"],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return json.loads(result.stdout)
            before = sorted(p.name for p in (store.root / "objects").iterdir())
            response = call("quality", asset["asset_id"], "--profile", "music")
            self.assertEqual(response["audio_model_calls"], 0)
            self.assertEqual(response["report"]["warning_counts"]["clipping_risk"], 2)
            self.assertEqual(sorted(p.name for p in (store.root / "objects").iterdir()), before)
            body = request("quality-cli", "quality/v1", asset["asset_id"], {"profile": "music"})
            path = root / "request.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            executed = call("action", "execute", "--request", str(path))
            self.assertEqual(executed["status"], "succeeded")
            self.assertEqual(executed["outputs"], [])
            self.assertEqual(call("action", "show", "quality-cli"), executed)
            self.assertEqual(store.asset(asset["asset_id"])[1], original)

    def test_opposite_phase_stereo_and_one_live_channel_are_not_silence(self):
        for pair in ((1000, -1000), (0, 1000)):
            source = pcm(list(pair) * 16000, channels=2)
            report = scan(source, {"profile": "speech"})
            self.assertEqual(report["warning_counts"]["sustained_silence"], 0)
            self.assertFalse(report["truncated"])
            self.assertEqual(report["scanned_frames"], 16000)

    def test_clipping_risk_uses_exact_contiguous_frames_and_both_channels(self):
        source = pcm([100, 100, 32767, 0, -32768, 32760, 100, 100, 0, -32767], channels=2)
        report = scan(source, {"window_frames": 2})
        risks = warnings(report, "clipping_risk")
        self.assertEqual([(r["start_frame"], r["end_frame"]) for r in risks], [(1, 3), (4, 5)])
        self.assertEqual(risks[0]["evidence"], {"near_full_scale_sample_count": 3,
            "peak_pcm16": 32768, "channel_indices": [0, 1]})
        self.assertEqual(risks[1]["evidence"]["channel_indices"], [1])
        self.assertTrue(risks[1]["touches_scan_boundary"])

    def test_short_final_window_uses_actual_frame_count_for_level_change(self):
        report = scan(pcm([1000] * 4 + [8000] * 2), {"window_frames": 4})
        change, = warnings(report, "relative_level_jump")
        self.assertEqual((change["start_frame"], change["end_frame"]), (0, 6))
        self.assertEqual(change["evidence"]["boundary_frame"], 4)
        self.assertEqual(change["evidence"]["current_end_frame"], 6)
        self.assertEqual(change["evidence"]["current_rms_pcm16"], 8000)
        self.assertAlmostEqual(change["evidence"]["change_db"], 20 * math.log10(8))
        self.assertEqual(report["windows_scanned"], 2)

    def test_volume_drop_is_reported_and_silent_onsets_are_not_ratios(self):
        report = scan(pcm([8000] * 4 + [1000] * 4 + [0] * 4), {"window_frames": 4})
        change, = warnings(report, "relative_level_jump")
        self.assertLess(change["evidence"]["change_db"], 0)
        self.assertEqual(report["level_comparisons"], 1)
        self.assertEqual(warnings(scan(pcm([0] * 4 + [20000] * 4), {"window_frames": 4}),
                                  "relative_level_jump"), [])

    def test_silence_is_frame_exact_across_windows(self):
        source = pcm([100] * 3 + [32, -32] * 2000 + [100] * 7)
        report = scan(source, {"profile": "speech", "window_frames": 111})
        silent, = warnings(report, "sustained_silence")
        self.assertEqual((silent["start_frame"], silent["end_frame"]), (3, 4003))
        self.assertFalse(silent["touches_scan_boundary"])
        self.assertEqual(warnings(scan(pcm([33] * 4000), {"profile": "speech"}), "sustained_silence"), [])

    def test_profile_thresholds_and_music_rest_are_explicit(self):
        source = pcm([0] * 4800)
        self.assertEqual(len(warnings(scan(source, {"profile": "speech"}), "sustained_silence")), 1)
        for profile in ("generic", "music"):
            self.assertEqual(warnings(scan(source, {"profile": profile}), "sustained_silence"), [])
        rest, = warnings(scan(pcm([0] * 16000), {"profile": "music"}), "sustained_silence")
        self.assertEqual(rest["severity"], "note")
        self.assertIn("intentional musical rest", rest["message"])
        levels = pcm([1000] * 4 + [4000] * 4)
        for profile, count in (("speech", 1), ("generic", 1), ("music", 0)):
            report = scan(levels, {"profile": profile, "window_frames": 4})
            self.assertEqual(len(warnings(report, "relative_level_jump")), count)

    def test_range_and_frame_budget_limit_observation(self):
        source = pcm([32767] * 3 + [1000] * 4 + [32767] * 4)
        report = scan(source, {"start_frame": 3, "end_frame": 11, "max_scan_frames": 4})
        self.assertEqual(report["requested_range"], {"start_frame": 3, "end_frame": 11})
        self.assertEqual(report["scanned_range"], {"start_frame": 3, "end_frame": 7})
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["unscanned_frames"], 4)
        self.assertEqual(report["reasons"], ["scan_frame_budget"])
        self.assertTrue(report["truncated"])

    def test_warning_limit_counts_omissions_without_stopping_scan(self):
        report = scan(pcm([32767, 1000] * 10), {"max_warnings": 2, "window_frames": 20})
        self.assertEqual(report["scanned_frames"], 20)
        self.assertEqual(report["unscanned_frames"], 0)
        self.assertEqual(report["warning_counts"]["clipping_risk"], 10)
        self.assertEqual(len(report["warnings"]), 2)
        self.assertEqual(report["warnings_omitted"], 8)
        self.assertEqual(report["reasons"], ["warning_limit"])
        both = scan(pcm([32767, 1000] * 10), {"max_warnings": 1, "max_scan_frames": 6})
        self.assertEqual(both["reasons"], ["scan_frame_budget", "warning_limit"])

    def test_budget_at_event_boundary_reports_only_observed_prefix(self):
        report = scan(pcm([0] * 20000), {"profile": "speech", "start_frame": 100,
                                       "max_scan_frames": 4000})
        silent, = warnings(report, "sustained_silence")
        self.assertEqual((silent["start_frame"], silent["end_frame"]), (100, 4100))
        self.assertTrue(silent["touches_scan_boundary"])
        self.assertTrue(report["truncated"])

    def test_single_frame_and_report_are_finite_and_input_is_unchanged(self):
        for source in (pcm([0]), pcm([-32768]), pcm([1, -1], channels=2)):
            before = source.payload
            report = scan(source, {})
            self.assertEqual(report["windows_scanned"], 1)
            self.assertEqual(report["level_comparisons"], 0)
            self.assertEqual(source.payload, before)
            self.assertTrue(report["advisory"])
            self.assertEqual(report["profile"], PROFILE)
            canonical(report)
            json.dumps(report, allow_nan=False)

    def test_invalid_parameters_are_rejected_by_public_scan(self):
        invalid = [{"profile": "podcast"}, {"profile": None}, {"unknown": 1}, {"window_frames": 0},
            {"window_frames": 192001}, {"start_frame": True}, {"start_frame": 1.0},
            {"end_frame": float("nan")}, {"max_scan_frames": False}, {"max_scan_frames": 33554433},
            {"max_warnings": 0}, {"max_warnings": 129}, {"max_warnings": 1.0}]
        for parameters in invalid:
            with self.subTest(parameters=parameters), self.assertRaises(AudioError) as caught:
                scan(pcm([0] * 10), parameters)
            self.assertEqual(caught.exception.code, "invalid_request")
        for parameters in ({"start_frame": 10}, {"start_frame": 5, "end_frame": 5}, {"end_frame": 11}):
            with self.subTest(parameters=parameters), self.assertRaises(AudioError) as caught:
                scan(pcm([0] * 10), parameters)
            self.assertEqual(caught.exception.code, "invalid_range")

    def test_resolution_does_not_scan_and_defaults_replay_exactly(self):
        source = pcm([1000] * 400 + [10000] * 400)
        def forbid_checkpoint(force):
            self.fail("Resolution must not perform a diagnostic scan")
        with checkpoints(forbid_checkpoint):
            resolved = resolve({}, source)
        self.assertEqual(resolved["window_frames"], 400)
        self.assertEqual(scan(source, {}), scan(source, resolved))

    def test_cancellation_checks_inside_a_large_window(self):
        source = pcm([1000] * 12000)
        calls = []
        def cancel(force):
            calls.append(force)
            if len(calls) == 3:
                raise AudioError("job_cancelled", "Synthetic scan cancellation")
        with checkpoints(cancel), self.assertRaises(AudioError) as caught:
            scan(source, {"window_frames": 12000})
        self.assertEqual(caught.exception.code, "job_cancelled")
        self.assertEqual(calls, [True, False, False])
        self.assertEqual(scan(source, {})["scanned_frames"], 12000)

    def test_maximum_warning_report_publishes_and_reopens(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            source.write_bytes(encode_wav(pcm(([1000] * 4 + [16000] * 4) * 100)))
            store = ArtifactStore(root / "workspace")
            asset = store.import_wav(source, "import")["outputs"][0]["asset_id"]
            action = request("bounded", "quality/v1", asset, {"window_frames": 4, "max_warnings": 128})
            result = ActionService(store, Registry([quality_operation()])).execute(action)
            report, = result["findings"]
            self.assertEqual(len(report["warnings"]), 128)
            self.assertEqual(report["warnings_omitted"], 71)
            self.assertLess(len(canonical(result)), MAX_JSON_BYTES)
            self.assertEqual(ArtifactStore(store.root).show_request("bounded"), result)

    def test_operation_has_source_bound_findings_no_audio_and_replays(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.wav"
            original = encode_wav(pcm([32767, 1000, -32768, 1000]))
            source.write_bytes(original)
            store = ArtifactStore(root / "workspace")
            asset = store.import_wav(source, "import")["outputs"][0]["asset_id"]
            registry = Registry([quality_operation()])
            sessions = SessionService(store, registry)
            sessions.mutate("create", {"schema": "matter-session-create/v1", "request_id": "create",
                "session_id": "test", "name": "test", "asset_id": asset})
            sessions.mutate("constraints", {"schema": "matter-constraints-set/v1", "request_id": "lock",
                "session_id": "test", "expected_revision": 1,
                "regions": [{"start_frame": 0, "end_frame": 4}]})
            action = request("quality", "quality/v1", asset, {})
            action["protection"] = {"session_id": "test", "revision": 2}
            session_before = sessions.show("test")
            service = ActionService(store, registry)
            result = service.execute(action)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["outputs"], [])
            self.assertEqual(result["audio_model_calls"], 0)
            finding, = result["findings"]
            self.assertEqual(finding["source_asset_id"], asset)
            self.assertEqual(finding["warning_counts"]["clipping_risk"], 2)
            self.assertEqual(service.execute(action), result)
            self.assertEqual(sessions.show("test"), session_before)
            self.assertEqual(store.asset(asset)[1], original)
            self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
