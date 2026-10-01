"""Regression checks for consuming-project and file-path isolation in the optional skill."""
import argparse
from array import array
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from matter_audio_core.cli import build_parser
from matter_audio_core.media import PCM, decode_wav, encode_wav, sample_bytes

SOURCE = Path(__file__).resolve().parents[1] / 'integrations/codex/matter-audio/scripts/run_audio.py'
spec = importlib.util.spec_from_file_location('matter_skill_launcher', SOURCE)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class SkillLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'consumer'
        self.nested = self.project / 'nested'
        self.nested.mkdir(parents=True)
        (self.project / '.git').write_text('gitdir: elsewhere',encoding='utf-8')
        self.tool = self.root / 'tool'; self.tool.mkdir()
        self.config = {'python':sys.executable,'root':str(self.tool),'workspace':str(self.root/'legacy')}

    def plan(self, **kwargs):
        values = dict(product='core',workspace=None,project_root=None,use_configured_workspace=False,command=[])
        values.update(kwargs)
        return launcher.resolve_plan(argparse.Namespace(**values),self.config,self.nested)

    def prepare_real_core(self):
        # Run the current core in a separate tool checkout, without depending on
        # an editable install or allowing the launcher to inherit PYTHONPATH.
        shutil.copytree(SOURCE.parents[4] / 'src/matter_audio_core', self.tool / 'matter_audio_core',
                        ignore=shutil.ignore_patterns('__pycache__'))
        config = self.root / 'config.json'
        config.write_text(json.dumps({'core': self.config}), encoding='utf-8')
        return config

    def invoke(self, config, command):
        result = subprocess.run([sys.executable, str(SOURCE), '--config', str(config),
                                 '--product', 'core', '--', *command], cwd=self.nested,
                                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def link_directory(self, link, target):
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.addCleanup(link.rmdir)  # Remove only the junction, never its target.
        else:
            link.symlink_to(target, target_is_directory=True)
            self.addCleanup(link.unlink)

    def test_nested_worktree_marker_keeps_state_in_consumer(self):
        result = self.plan()
        self.assertEqual(Path(result['workspace']),self.project/'artifacts/matter-audio/core')
        self.assertEqual(Path(result['cwd']),self.tool)
        self.assertFalse((self.project/'artifacts').exists())

    def test_explicit_workspace_and_legacy_opt_in(self):
        selected = self.root/'selected'
        self.assertEqual(Path(self.plan(workspace=selected)['workspace']),selected)
        self.assertEqual(Path(self.plan(use_configured_workspace=True)['workspace']),self.root/'legacy')
        self.assertNotEqual(self.plan()['workspace'],self.plan(use_configured_workspace=True)['workspace'])

    def test_explicit_project_and_product_are_independent(self):
        other=self.root/'second'; other.mkdir()
        result=self.plan(project_root=other,product='sonic')
        self.assertEqual(Path(result['workspace']),other/'artifacts/matter-audio/sonic')
        self.assertIn('tools.authoring',result['argv'])

    def test_import_uses_caller_file_even_if_tool_has_same_name(self):
        (self.nested/'input.wav').write_bytes(b'consumer')
        (self.tool/'input.wav').write_bytes(b'wrong-tool-file')
        result=self.plan(command=['assets','import','input.wav','--request-id','import-one'])
        path=Path(result['argv'][result['argv'].index('import')+1])
        self.assertEqual(path.read_bytes(),b'consumer')

    def test_import_path_can_follow_options(self):
        command=['--json','assets','import','--request-id','input.wav','input.wav']
        result=self.plan(command=command)['argv']
        self.assertEqual(result[-2],'input.wav')
        self.assertEqual(Path(result[-1]),self.nested/'input.wav')

    def test_json_in_every_position_imports_real_caller_pcm(self):
        config = self.prepare_real_core()
        for directory, value in ((self.nested, 100), (self.tool, 200)):
            (directory / 'input.wav').write_bytes(encode_wav(PCM(sample_bytes(array('h', [value])), 8000, 1)))
        for position in range(6):
            with self.subTest(position=position):
                command = ['assets', 'import', 'input.wav', '--request-id', f'import-{position}']
                command.insert(position, '--json')
                result = self.invoke(config, command)
                output = Path(result['playback'][0]['path'])
                self.assertEqual(list(decode_wav(output.read_bytes()).samples()), [100])
                self.assertEqual(Path(result['outputs'][0]['provenance']['path']), self.nested / 'input.wav')

    def test_repeated_json_and_separator_preserve_option_values_and_caller_file(self):
        config = self.prepare_real_core()
        for directory, value in ((self.nested, 100), (self.tool, 200)):
            (directory / '--request').write_bytes(encode_wav(PCM(sample_bytes(array('h', [value])), 8000, 1)))
        result = self.invoke(config, ['--json', 'assets', '--json', 'import', '--request-id',
                                     '--json', 'separator-import', '--', '--request', '--json'])
        self.assertEqual(result['request_id'], 'separator-import')
        self.assertEqual(list(decode_wav(Path(result['playback'][0]['path']).read_bytes()).samples()), [100])

    def test_import_option_abbreviations_and_global_json_use_real_caller_pcm(self):
        config = self.prepare_real_core()
        for directory, value in ((self.nested, 100), (self.tool, 200)):
            (directory / 'input.wav').write_bytes(encode_wav(PCM(sample_bytes(array('h', [value])), 8000, 1)))
        for index, flag in enumerate(('--request-i', '--r', '--request', '--r=', '--request-i=')):
            with self.subTest(flag=flag):
                request_id = f'abbreviated-import-{index}'
                option = [flag + request_id] if flag.endswith('=') else [flag, request_id]
                result = self.invoke(config, ['--j', 'assets', 'import', *option, 'input.wav'])
                self.assertEqual(result['request_id'], request_id)
                self.assertEqual(list(decode_wav(Path(result['playback'][0]['path']).read_bytes()).samples()), [100])

    def test_request_abbreviations_read_real_caller_json(self):
        config = self.prepare_real_core()
        (self.nested / 'input.wav').write_bytes(encode_wav(PCM(sample_bytes(array('h', [100])), 8000, 1)))
        imported = self.invoke(config, ['assets', 'import', 'input.wav', '--request-id', 'source'])
        asset_id = imported['outputs'][0]['asset_id']
        for index, (flag, filename) in enumerate((('--requ', 'request.json'), ('--r', 'request.json'),
                                                ('--requ=', 'request.json'), ('--r=', 'request.json'),
                                                ('--r=', '-request.json'))):
            with self.subTest(flag=flag, filename=filename):
                for directory, name, db in ((self.nested, 'caller', 0), (self.tool, 'tool', -6)):
                    body = {'schema': 'matter-action/v1', 'request_id': f'{name}-{index}',
                            'operation': 'gain/v1', 'inputs': [asset_id], 'parameters': {'db': db}}
                    (directory / filename).write_text(json.dumps(body), encoding='utf-8')
                option = [flag + filename] if flag.endswith('=') else [flag, filename]
                result = self.invoke(config, ['action', 'execute', *option])
                self.assertEqual(result['request_id'], f'caller-{index}')
                self.assertEqual(list(decode_wav(Path(result['playback'][0]['path']).read_bytes()).samples()), [100])

    def test_file_option_does_not_consume_another_option_as_a_path(self):
        for command in (['action', 'execute', '--r', '--help'],
                        ['action', 'execute', '--request', '--expected-resolution-digest', 'digest'],
                        ['audition', 'serve', 'one', '--ready-f', '--port', '0']):
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValueError, 'requires a file path'):
                    self.plan(command=command)
        argv = self.plan(command=['action', 'execute', '--r', '-1'])['argv'][3:]
        self.assertEqual(build_parser('core').parse_args(argv).request, self.nested / '-1')

    def test_file_abbreviations_match_each_shared_command_context(self):
        commands = {
            'library': ('create', 'search'), 'cue-set': ('create', 'export'),
            'action': ('resolve', 'execute'), 'session': ('create', 'select', 'branch'),
            'constraints': ('set',), 'feedback': ('add',), 'job': ('submit', 'cancel', 'retry'),
            'batch': ('submit', 'retry'), 'audition': ('create',), 'export': ('create',),
        }
        for command, subcommands in commands.items():
            for subcommand in subcommands:
                with self.subTest(command=command, subcommand=subcommand):
                    argv = self.plan(command=[command, subcommand, '--r=request.json'])['argv'][3:]
                    parsed = build_parser('core').parse_args(argv)
                    self.assertEqual(parsed.request, self.nested / 'request.json')
        for flag in ('--r', '--ready', '--ready-f', '--r=', '--ready-f='):
            with self.subTest(flag=flag):
                option = [flag + 'ready.json'] if flag.endswith('=') else [flag, 'ready.json']
                argv = self.plan(command=['audition', 'serve', 'one', *option])['argv'][3:]
                self.assertEqual(build_parser('core').parse_args(argv).ready_file, self.nested / 'ready.json')

    def test_revision_abbreviations_remain_non_file_options(self):
        for command in (['constraints', 'show', 'one', '--r', '1'],
                        ['feedback', 'list', 'one', '--r=1']):
            with self.subTest(command=command):
                argv = self.plan(command=command)['argv'][3:]
                self.assertEqual(argv[2:], command)
                self.assertEqual(build_parser('core').parse_args(argv).revision, 1)

    def test_product_ingestion_files_resolve_from_caller_with_json_and_abbreviations(self):
        cases = (
            ('sonic', ['recordings', 'list', '--m=recordings.json'],
             ['recordings', 'list', '--manifest=' + str(self.nested / 'recordings.json')]),
            ('sonic', ['recordings', '--json', 'import', 'paper-01', '--m', 'recordings.json', '--r', 'registration'],
             ['recordings', 'import', 'paper-01', '--manifest', str(self.nested / 'recordings.json'), '--request-id', 'registration']),
            ('score', ['candidate', 'register', '--a', 'candidate.wav', '--g=record.json', '--i', 'intent.json', '--r=registration'],
             ['candidate', 'register', '--audio', str(self.nested / 'candidate.wav'), '--generation-record=' + str(self.nested / 'record.json'), '--intent', str(self.nested / 'intent.json'), '--request-id=registration']),
        )
        for product, command, expected in cases:
            with self.subTest(product=product, command=command):
                argv = self.plan(product=product, command=command)['argv']
                self.assertEqual(argv[argv.index('--workspace') + 2:], expected)

    def test_product_ingestion_files_reject_links_and_missing_values(self):
        shared = self.root / 'source-files'
        shared.mkdir()
        self.link_directory(self.nested / 'linked', shared)
        for context, option in ((['recordings', 'list'], '--manifest'),
                                (['recordings', 'import', 'one'], '--manifest'),
                                (['candidate', 'register'], '--audio'),
                                (['candidate', 'register'], '--generation-record'),
                                (['candidate', 'register'], '--intent')):
            with self.subTest(context=context, option=option):
                with self.assertRaisesRegex(ValueError, 'Reparse points and symlinks'):
                    self.plan(command=[*context, option + '=linked/file'])
                with self.assertRaisesRegex(ValueError, 'requires a file path'):
                    self.plan(command=[*context, option, '--help'])
                with self.assertRaisesRegex(ValueError, 'requires a file path'):
                    self.plan(command=[*context, option])

    def test_music_requests_use_caller_paths_and_asset_ids_remain_identifiers(self):
        for command in ('annotate', 'plan'):
            for option in (['--request', 'music.json'], ['--r=music.json']):
                with self.subTest(command=command, option=option):
                    argv = self.plan(product='score', command=['music', '--json', command, *option])['argv']
                    expected = ['--request', str(self.nested / 'music.json')] if len(option) == 2 else [
                        '--request=' + str(self.nested / 'music.json')]
                    self.assertEqual(argv[argv.index('--workspace') + 2:], ['music', command, *expected])
        for command in ('show', 'execute'):
            forwarded = ['music', command, 'a_' + 'a' * 32 + '_0']
            argv = self.plan(product='score', command=forwarded)['argv']
            self.assertEqual(argv[argv.index('--workspace') + 2:], forwarded)

    def test_music_requests_cannot_hide_linked_paths_or_missing_values(self):
        source = self.root / 'music-files'
        source.mkdir()
        self.link_directory(self.nested / 'linked', source)
        for command in ('annotate', 'plan'):
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValueError, 'Reparse points and symlinks'):
                    self.plan(command=['music', command, '--r=linked/music.json'])
                with self.assertRaisesRegex(ValueError, 'requires a file path'):
                    self.plan(command=['music', command, '--request', '--help'])

    def test_forwarded_workspace_and_unknown_leading_options_fail_explicitly(self):
        for option in (['--workspace', str(self.root / 'bypass')],
                       ['--w=' + str(self.root / 'bypass')]):
            with self.subTest(option=option):
                with self.assertRaisesRegex(ValueError, 'Pass --workspace to the launcher before --'):
                    self.plan(command=['--j', *option, 'assets', 'import', 'input.wav'])
        with self.assertRaisesRegex(ValueError, 'Unsupported or ambiguous leading product option'):
            self.plan(command=['--unknown', 'assets', 'import', 'input.wav'])
        for flag, canonical in (('--help', '--help'), ('-h', '-h'), ('--version', '--version'),
                                ('--h', '--help'), ('--v', '--version')):
            with self.subTest(flag=flag):
                self.assertEqual(self.plan(command=[flag])['argv'][-1], canonical)

    def test_workspace_links_are_rejected_for_default_explicit_and_configured_paths(self):
        shared = self.root / 'shared-artifacts'
        shared.mkdir()
        other = self.root / 'other-consumer'
        other.mkdir()
        for consumer in (self.project, other):
            self.link_directory(consumer / 'artifacts', shared)
            workspace = consumer / 'artifacts/matter-audio/core'
            self.config['workspace'] = str(workspace)
            for options in ({'project_root': consumer}, {'workspace': workspace},
                            {'use_configured_workspace': True}):
                with self.subTest(consumer=consumer, options=options):
                    with self.assertRaisesRegex(ValueError, 'Reparse points and symlinks'):
                        self.plan(**options)
        self.assertEqual(list(shared.iterdir()), [])

    def test_project_root_and_caller_links_are_rejected_before_resolution(self):
        alias = self.root / 'project-alias'
        self.link_directory(alias, self.project)
        with self.assertRaisesRegex(ValueError, 'Reparse points and symlinks'):
            self.plan(project_root=alias)
        with self.assertRaisesRegex(ValueError, 'Reparse points and symlinks'):
            launcher.project_root(None, alias / 'nested')

    def test_managed_file_arguments_cannot_hide_linked_ancestors(self):
        shared = self.root / 'shared-files'
        shared.mkdir()
        self.link_directory(self.nested / 'linked', shared)
        for command in (['assets', 'import', 'linked/input.wav', '--request-id', 'one'],
                        ['action', 'execute', '--request=linked/request.json'],
                        ['audition', 'serve', 'one', '--ready-file', 'linked/ready.json']):
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValueError, 'Reparse points and symlinks'):
                    self.plan(command=command)
        self.assertEqual(list(shared.iterdir()), [])

    def test_request_and_ready_file_resolve_before_cwd_change(self):
        request=self.plan(command=['action','execute','--request=request.json'])['argv']
        self.assertEqual(request[-1],'--request='+str(self.nested/'request.json'))
        ready=self.plan(command=['audition','serve','id','--ready-file','ready.json'])['argv']
        self.assertEqual(Path(ready[-1]),self.nested/'ready.json')

    def test_relative_workspace_is_rejected(self):
        with self.assertRaises(ValueError): self.plan(workspace=Path('relative'))

    def test_non_git_directory_stays_explicit_cwd(self):
        other=self.root/'nonrepo';other.mkdir()
        self.assertEqual(launcher.project_root(None,other),other)


if __name__ == '__main__':
    unittest.main()
