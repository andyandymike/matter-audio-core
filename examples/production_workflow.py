"""Cue variants, a loop, two scene revisions and local search via fresh CLIs."""

import json
import math
import subprocess
import sys
import uuid
from array import array
from pathlib import Path

from matter_audio_core.media import PCM, decode_wav, encode_wav, sample_bytes


def main():
    root = Path('.local/production-demo') / uuid.uuid4().hex[:12]
    root.mkdir(parents=True)
    workspace, calls = root / 'workspace', 0

    def call(*args):
        nonlocal calls
        completed = subprocess.run([sys.executable, '-m', 'matter_audio_core', '--workspace', str(workspace), *args, '--json'],
                                   capture_output=True, encoding='utf-8', check=True)
        calls += 1
        return json.loads(completed.stdout)

    def write(command, body):
        path = root / f'request-{calls}.json'
        path.write_text(json.dumps(body), encoding='utf-8')
        return call(*command, '--request', str(path))

    def action(identifier, operation, inputs, parameters):
        body = {'schema': 'matter-action/v1', 'request_id': identifier, 'operation': operation,
                'inputs': inputs, 'parameters': parameters}
        result = write(['action', 'execute'], body)
        assert result['status'] == 'succeeded' and result['audio_model_calls'] == 0
        assert write(['action', 'execute'], body) == result
        return result['outputs'][0]

    assets = []
    for i, (frames, frequency, amplitude) in enumerate([(8000, 123, 2000), (800, 320, 4000), (800, 640, 1200)]):
        path = root / f'fixture-{i}.wav'
        path.write_bytes(encode_wav(PCM(sample_bytes(array('h', [round(amplitude * math.sin(2 * math.pi * frequency * n / 8000))
                                                              for n in range(frames)])), 8000, 1)))
        assets.append(call('assets', 'import', str(path), '--request-id', f'import-{i}')['outputs'][0]['asset_id'])
    loop = action('loop', 'loop/v1', [assets[0]], {'start_frame': 0, 'end_frame': 8000, 'crossfade_frames': 800})
    variants = [action(f'level-{i}', 'normalize/v1', [asset], {'target_rms_dbfs': -24, 'max_boost_db': 24})
                for i, asset in enumerate(assets[1:])]
    for variant in variants:
        measured = call('analyze', variant['asset_id'])['analysis']
        assert abs(measured['rms_dbfs'] + 24) < .01
    spec = {'duration_frames': 21600, 'tracks': [{'name': 'music', 'db': -9, 'fade_in_frames': 800, 'fade_out_frames': 800}, {'name': 'sfx'}],
        'events': [{'event_id': 'bed', 'input_index': 0, 'track': 'music', 'source_start_frame': 0, 'source_end_frame': 7200, 'offset_frame': 0, 'repeat': 3},
                   {'event_id': 'switch', 'input_index': 1, 'track': 'sfx', 'source_start_frame': 0, 'source_end_frame': 800, 'offset_frame': 1000, 'repeat': 2, 'interval_frames': 8000},
                   {'event_id': 'confirm', 'input_index': 2, 'track': 'sfx', 'source_start_frame': 0, 'source_end_frame': 800, 'offset_frame': 15000}]}
    inputs = [loop['asset_id'], *[variant['asset_id'] for variant in variants]]
    scene_a = action('scene-a', 'scene/v1', inputs, spec)
    spec['tracks'][1]['db'] = -6
    scene_b = action('scene-b', 'scene/v1', inputs, spec)
    before, after = [decode_wav((workspace / value['locator']).read_bytes()).samples() for value in (scene_a, scene_b)]
    writes = [(1000, 1800), (9000, 9800), (15000, 15800)]
    assert any(a != b for a, b in zip(before, after))
    assert all(a == b for frame, (a, b) in enumerate(zip(before, after)) if not any(start <= frame < end for start, end in writes))
    cue_set = {'schema': 'matter-cue-set/v1', 'set_id': 'demo-v1', 'name': 'Synthetic UI scene', 'cues': [
        {'key': 'ambience', 'name': 'Ambience loop', 'selected_variant': 'main', 'variants': [
            {'key': 'main', 'asset_id': loop['asset_id'], 'loop': {'begin_frame': 0, 'end_frame': 7200}}]},
        {'key': 'switch', 'name': 'Switch variations', 'selected_variant': 'b', 'variants': [
            {'key': key, 'asset_id': value['asset_id']} for key, value in zip(('a', 'b'), variants)]},
        {'key': 'scene', 'name': 'Scene render', 'selected_variant': 'soft', 'variants': [{'key': 'soft', 'asset_id': scene_b['asset_id']}]}]}
    created = write(['cue-set', 'create'], cue_set)
    assert call('cue-set', 'show', 'demo-v1') == created
    delivered = write(['cue-set', 'export'], {'schema': 'matter-cue-export/v1', 'request_id': 'delivery', 'set_id': 'demo-v1', 'variants': 'all'})
    assert call('cue-set', 'export-show', 'delivery') == delivered
    assert len(delivered['files']) == 4
    for entry in delivered['body']['entries']:
        record = call('assets', 'show', entry['asset']['asset_id'])['asset']
        assert (Path(delivered['directory']) / entry['filename']).read_bytes() == (workspace / record['locator']).read_bytes()
    index = write(['library', 'create'], {'schema': 'matter-library/v1', 'library_id': 'demo', 'name': 'Synthetic library', 'entries': [
        {'asset_id': value['asset_id'], 'name': name, 'tags': tags} for value, name, tags in
        [(loop, 'Ambience', ['music', 'loop']), (variants[0], 'Switch A', ['ui', 'switch']), (variants[1], 'Switch B', ['ui', 'switch']), (scene_b, 'Scene', ['mix'])]]})
    assert call('library', 'show', 'demo') == index
    results = write(['library', 'search'], {'schema': 'matter-library-search/v1', 'library_id': 'demo', 'tags': ['ui'], 'similar_to': variants[1]['asset_id']})
    assert results['total_matches'] == 2 and results['items'][0]['asset_id'] == variants[1]['asset_id']
    report = {'status': 'passed', 'workspace': str(workspace.resolve()), 'cli_calls': calls, 'audio_model_calls': 0,
        'export_directory': delivered['directory'], 'independent_track_pcm': 'verified', 'human_listening': 'not_performed'}
    (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
