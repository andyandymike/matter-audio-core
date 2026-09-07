import copy
import tempfile
import unittest
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.cue_sets import CueSetService
from matter_audio_core.errors import AudioError
from matter_audio_core.library import LibraryService
from matter_audio_core.media import PCM, encode_wav, sample_bytes
from matter_audio_core.sessions import SessionService


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = ArtifactStore(self.root / 'workspace')
        self.ids = []
        for i, values in enumerate(([1000, -1000] * 10, [100, -100] * 20, [0] * 30)):
            path = self.root / f'{i}.wav'
            path.write_bytes(encode_wav(PCM(sample_bytes(array('h', values)), 8000, 1)))
            self.ids.append(self.store.import_wav(path, f'import-{i}')['outputs'][0]['asset_id'])
        self.cues, self.library = CueSetService(self.store), LibraryService(self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def spec(self):
        return {'schema': 'matter-cue-set/v1', 'set_id': 'ui-v1', 'name': 'UI paper', 'cues': [
            {'key': 'switch', 'name': '切换', 'selected_variant': 'soft', 'variants': [
                {'key': 'normal', 'asset_id': self.ids[0]}, {'key': 'soft', 'asset_id': self.ids[1]}]}]}

    def index(self):
        return {'schema': 'matter-library/v1', 'library_id': 'paper', 'name': 'Paper', 'entries': [
            {'asset_id': self.ids[0], 'name': '纸张 Clear', 'tags': ['UI', 'paper']},
            {'asset_id': self.ids[1], 'name': '纸张 Quiet', 'tags': ['UI', 'paper']},
            {'asset_id': self.ids[2], 'name': 'Silence', 'tags': ['fixture']} ]}

    def test_sets_and_exports_reopen_with_exact_selected_bytes(self):
        first = self.cues.create(self.spec())
        self.assertEqual(self.cues.create(self.spec()), first)
        export = {'schema': 'matter-cue-export/v1', 'request_id': 'deliver', 'set_id': 'ui-v1', 'variants': 'selected'}
        receipt = self.cues.export(export)
        self.assertEqual(receipt, CueSetService(self.store).export_show('deliver'))
        path = Path(receipt['directory']) / 'switch__soft.wav'
        self.assertEqual(path.read_bytes(), self.store.asset(self.ids[1])[1])
        changed = self.spec()
        changed['cues'][0]['selected_variant'] = 'normal'
        with self.assertRaises(AudioError):
            self.cues.create(changed)
        self.assertEqual(self.cues.export(export), receipt)

    def test_new_set_revision_preserves_prior_selection_and_exports_all(self):
        original = self.cues.create(self.spec())
        changed = self.spec()
        changed.update(set_id='ui-v2', supersedes='ui-v1')
        changed['cues'][0]['selected_variant'] = 'normal'
        self.cues.create(changed)
        self.assertEqual(self.cues.show('ui-v1'), original)
        result = self.cues.export({'schema': 'matter-cue-export/v1', 'request_id': 'all', 'set_id': 'ui-v2', 'variants': 'all'})
        self.assertEqual(set(result['files']), {'switch__normal.wav', 'switch__soft.wav'})
        self.assertEqual(len(self.cues.documents.list()['items']), 2)

    def test_saved_selection_and_loop_ranges_are_bound(self):
        sessions = SessionService(self.store)
        sessions.mutate('create', {'schema': 'matter-session-create/v1', 'request_id': 'session',
            'session_id': 'sfx', 'name': 'SFX', 'asset_id': self.ids[1]})
        spec = self.spec()
        variant = spec['cues'][0]['variants'][1]
        variant['selection'] = {'session_id': 'sfx', 'revision': 1}
        variant['loop'] = {'begin_frame': 0, 'end_frame': 40}
        self.cues.create(spec)
        other = copy.deepcopy(spec)
        other['set_id'] = 'bad-selection'
        other['cues'][0]['variants'][1]['asset_id'] = self.ids[0]
        other['cues'][0]['variants'][1].pop('loop')
        with self.assertRaises(AudioError):
            self.cues.create(other)
        other = copy.deepcopy(spec)
        other['set_id'] = 'bad-loop'
        other['cues'][0]['variants'][1]['loop']['end_frame'] = 41
        with self.assertRaises(AudioError):
            self.cues.create(other)

    def test_conflicts_paths_duplicate_keys_and_partial_publication(self):
        spec = self.spec()
        spec['cues'][0]['key'] = '../unsafe'
        with self.assertRaises(AudioError):
            self.cues.create(spec)
        spec = self.spec()
        spec['cues'].append(copy.deepcopy(spec['cues'][0]))
        with self.assertRaises(AudioError):
            self.cues.create(spec)
        with patch.object(self.store, '_publish', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.cues.create(self.spec())
        self.assertEqual(self.cues.documents.list()['items'], [])

    def test_concurrent_identical_creation_publishes_one_complete_document(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.cues.create(self.spec()), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.cues.documents.list()['items']), 1)

    def test_export_tampering_is_detected(self):
        self.cues.create(self.spec())
        result = self.cues.export({'schema': 'matter-cue-export/v1', 'request_id': 'deliver', 'set_id': 'ui-v1', 'variants': 'all'})
        (Path(result['directory']) / 'switch__normal.wav').write_bytes(b'changed')
        with self.assertRaises(AudioError):
            self.cues.export_show('deliver')

    def test_metadata_filtering_is_local_and_silence_is_not_fake_dbfs(self):
        self.library.create(self.index())
        result = self.library.search({'schema': 'matter-library-search/v1', 'library_id': 'paper', 'text': '纸张 quiet', 'tags': ['ui']})
        self.assertEqual([item['asset_id'] for item in result['items']], [self.ids[1]])
        result = self.library.search({'schema': 'matter-library-search/v1', 'library_id': 'paper', 'min_rms_dbfs': -120})
        self.assertEqual(result['total_matches'], 2)

    def test_numeric_distance_pagination_and_index_replay(self):
        created = self.library.create(self.index())
        self.assertEqual(LibraryService(self.store).create(self.index()), created)
        result = self.library.search({'schema': 'matter-library-search/v1', 'library_id': 'paper', 'similar_to': self.ids[0], 'limit': 1})
        self.assertEqual(result['items'][0]['asset_id'], self.ids[0])
        self.assertEqual(result['items'][0]['distance'], 0)
        self.assertEqual(result['next_offset'], 1)
        with self.assertRaises(AudioError):
            self.library.search({'schema': 'matter-library-search/v1', 'library_id': 'paper', 'min_seconds': 4, 'max_seconds': 1})


if __name__ == '__main__':
    unittest.main()
