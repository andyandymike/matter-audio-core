import math
import tempfile
import unittest
from array import array
from pathlib import Path

from matter_audio_core.actions import ActionService
from matter_audio_core.analysis import describe
from matter_audio_core.arrangement import (LOOP_SCHEMA, SCENE_SCHEMA, make_loop, normalize,
    render_scene, resolve_loop, resolve_normalize, resolve_scene)
from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.contracts import request, validate
from matter_audio_core.errors import AudioError
from matter_audio_core.media import PCM, decode_wav, encode_wav, sample_bytes
from matter_audio_core.sessions import SessionService


def pcm(values, channels=1, rate=8000):
    return PCM(sample_bytes(array('h', values)), rate, channels)


def event(**changes):
    return {'event_id': 'click', 'input_index': 0, 'track': 'sfx', 'source_start_frame': 0,
            'source_end_frame': 2, 'offset_frame': 1, **changes}


class ArrangementTests(unittest.TestCase):
    def test_silence_and_single_frame_analysis_are_finite(self):
        silent = describe(pcm([0] * 10))
        self.assertIsNone(silent['rms_dbfs'])
        self.assertIsNone(silent['spectral_centroid_hz'])
        self.assertEqual(silent['active_frame_fraction'], 0)
        self.assertEqual(silent['seam']['max_jump_pcm16'], 0)
        self.assertEqual(describe(pcm([-32768]))['peak_pcm16'], 32768)

    def test_fft_retains_opposite_phase_stereo_power(self):
        values = [round(10000 * math.sin(2 * math.pi * 440 * i / 8192)) for i in range(4096)]
        audio = pcm([value for sample in values for value in (sample, -sample)], channels=2, rate=8192)
        findings = describe(audio)
        self.assertAlmostEqual(findings['spectral_centroid_hz'], 440, delta=2)
        self.assertAlmostEqual(findings['stereo_correlation'], -1)
        self.assertAlmostEqual(findings['zero_crossing_rate'], 880 / 8192, delta=.002)

    def test_normalization_reaches_rms_and_preserves_polarity(self):
        source = pcm([1000, -1000] * 8)
        parameters = resolve_normalize({'target_rms_dbfs': -20}, source)
        output, report = normalize(parameters, source)
        self.assertAlmostEqual(report['analysis']['rms_dbfs'], -20, delta=.01)
        self.assertEqual(output.samples()[0], -output.samples()[1])
        self.assertFalse(parameters['limited'])

    def test_peak_and_boost_limits_are_explicit(self):
        source = pcm([30000] + [0] * 99)
        parameters = resolve_normalize({'target_rms_dbfs': -6}, source)
        output, report = normalize(parameters, source)
        self.assertTrue(parameters['limited'])
        self.assertLessEqual(report['analysis']['peak_dbfs'], -1)
        self.assertLess(report['analysis']['rms_dbfs'], -6)
        silent = pcm([0] * 4)
        self.assertEqual(normalize(resolve_normalize({'target_rms_dbfs': -20}, silent), silent)[0], silent)

    def test_loop_rotation_blends_only_the_tail_and_reports_period(self):
        source = pcm([10, 20, 30, 40, 50, 60, 70, 80])
        plan = resolve_loop({'start_frame': 0, 'end_frame': 8, 'crossfade_frames': 2}, source)
        output, report = make_loop(plan, source)
        self.assertEqual(list(output.samples()), [30, 40, 50, 60, 70, 20])
        self.assertEqual(report['loop']['end_frame'], 6)
        self.assertEqual(report['output_analysis']['seam']['max_jump_pcm16'], 10)
        self.assertEqual(report['source_seam']['max_jump_pcm16'], 70)

    def test_zero_overlap_is_an_exact_stereo_slice(self):
        source = pcm([1, -1, 2, -2, 3, -3, 4, -4], channels=2)
        output, _ = make_loop(resolve_loop({'start_frame': 1, 'end_frame': 4, 'crossfade_frames': 0}, source), source)
        self.assertEqual(output.payload, source.payload[4:])

    def test_equal_power_overlap_rejects_clipping(self):
        source = pcm([32767] * 12)
        plan = resolve_loop({'start_frame': 0, 'end_frame': 12, 'crossfade_frames': 4, 'curve': 'equal_power'}, source)
        with self.assertRaisesRegex(AudioError, 'overflow'):
            make_loop(plan, source)
        _, report = make_loop({**plan, 'clip': 'saturate'}, source)
        self.assertEqual(report['overflow_sample_count'], 2)

    def test_loop_invalid_ranges_and_strict_json(self):
        for fade in (1, 5):
            with self.assertRaises(AudioError):
                resolve_loop({'start_frame': 0, 'end_frame': 8, 'crossfade_frames': fade}, pcm([1] * 8))
        with self.assertRaises(AudioError):
            validate({'start_frame': True, 'end_frame': 8, 'crossfade_frames': 2}, LOOP_SCHEMA)

    def test_scene_repeats_on_an_explicit_silent_timeline(self):
        inputs = [pcm([2, -2])]
        plan = resolve_scene({'duration_frames': 7, 'tracks': [{'name': 'sfx'}],
            'events': [event(repeat=2, interval_frames=3)]}, inputs)
        output, report = render_scene(plan, inputs)
        self.assertEqual(list(output.samples()), [0, 2, -2, 0, 2, -2, 0])
        self.assertEqual(report['event_count'], 2)

    def test_editing_one_track_leaves_other_intervals_exact(self):
        inputs = [pcm([100] * 8), pcm([10] * 2)]
        spec = {'duration_frames': 8, 'tracks': [{'name': 'music'}, {'name': 'sfx'}],
            'events': [event(event_id='bed', track='music', offset_frame=0, source_end_frame=8),
                       event(input_index=1, offset_frame=3)]}
        first = render_scene(resolve_scene(spec, inputs), inputs)[0]
        spec['tracks'][1]['db'] = -6
        second = render_scene(resolve_scene(spec, inputs), inputs)[0]
        self.assertEqual(first.payload[:6], second.payload[:6])
        self.assertEqual(first.payload[10:], second.payload[10:])
        self.assertEqual(list(first.samples())[3:5], [110, 110])
        self.assertEqual(list(second.samples())[3:5], [105, 105])

    def test_scene_fades_and_sum_round_once(self):
        inputs = [pcm([1] * 3), pcm([1] * 3)]
        spec = {'duration_frames': 3, 'tracks': [{'name': 'sfx', 'fade_in_frames': 3}],
            'events': [event(offset_frame=0, source_end_frame=3), event(event_id='second', input_index=1, offset_frame=0, source_end_frame=3)]}
        output, _ = render_scene(resolve_scene(spec, inputs), inputs)
        self.assertEqual(list(output.samples()), [0, 1, 2])

    def test_scene_clipping_and_bounds(self):
        inputs = [pcm([30000, -30000])]
        spec = {'duration_frames': 2, 'tracks': [{'name': 'sfx'}],
                'events': [event(offset_frame=0), event(event_id='overlap', offset_frame=0)]}
        with self.assertRaises(AudioError):
            render_scene(resolve_scene(spec, inputs), inputs)
        spec['duration_frames'] = 1
        with self.assertRaises(AudioError):
            resolve_scene(spec, inputs)
        with self.assertRaises(AudioError):
            validate({**spec, 'master_db': float('nan')}, SCENE_SCHEMA)

    def test_loop_locks_map_interior_and_reject_removed_or_written_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wav = root / 'source.wav'
            wav.write_bytes(encode_wav(pcm([10, 20, 30, 40, 50, 60, 70, 80])))
            store = ArtifactStore(root / 'workspace')
            asset = store.import_wav(wav, 'source')['outputs'][0]['asset_id']
            sessions = SessionService(store)
            for i, (start, end) in enumerate([(2, 5), (0, 1), (6, 7)]):
                sid = 'session-' + str(i)
                sessions.mutate('create', {'schema': 'matter-session-create/v1', 'request_id': sid,
                    'session_id': sid, 'name': sid, 'asset_id': asset})
                sessions.mutate('constraints', {'schema': 'matter-constraints-set/v1', 'request_id': 'lock-' + sid,
                    'session_id': sid, 'expected_revision': 1, 'regions': [{'start_frame': start, 'end_frame': end}]})
                action = request('loop-' + sid, 'loop/v1', asset, {'start_frame': 0, 'end_frame': 8, 'crossfade_frames': 2})
                action['protection'] = {'session_id': sid, 'revision': 2}
                if i == 0:
                    result = ActionService(store).execute(action)
                    self.assertEqual(result['status'], 'succeeded')
                    output = decode_wav(store.asset(result['outputs'][0]['asset_id'])[1])
                    self.assertEqual(list(output.samples())[:3], [30, 40, 50])
                else:
                    with self.assertRaises(AudioError):
                        ActionService(store).resolve(action)


if __name__ == '__main__':
    unittest.main()
