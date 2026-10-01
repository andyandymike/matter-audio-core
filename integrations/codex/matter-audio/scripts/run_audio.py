"""Run a configured audio product with state scoped to the consuming project."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


def checked_path(path: Path) -> Path:
    """Keep the core's no-link boundary before canonicalizing caller paths."""
    path = path.absolute()
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError(f'Reparse points and symlinks are not supported: {candidate}')
    return path.resolve()


def project_root(value: Path | None, cwd: Path) -> Path:
    if value is not None:
        if not value.is_absolute() or not value.is_dir():
            raise ValueError('Project root must be an existing absolute directory')
        return checked_path(value)
    current = checked_path(cwd)
    for candidate in (current, *current.parents):
        if (candidate / '.git').exists():
            return candidate
    return current


def resolve_file_arguments(command: list[str], cwd: Path) -> list[str]:
    """Resolve shared-authoring CLI file arguments before entering the tool checkout."""
    # The shared CLI removes every standalone --json before parsing, including
    # between subcommands or an option and its value. Match that token stream.
    result = [token for token in command if token != '--json']

    def option_name(token: str, options: tuple[str, ...]) -> str | None:
        name = token.partition('=')[0]
        if name in options:
            return name
        matches = [option for option in options if name.startswith('--') and option.startswith(name)]
        return matches[0] if len(matches) == 1 else None

    # Only --json can precede a command without changing its interpretation.
    # Workspace belongs to the launcher so no forwarded option can bypass its
    # path checks. Help/version exit in argparse before any file operation.
    global_options = ('--json', '--workspace', '--help', '--version', '-h')
    while result and result[0].startswith('-'):
        name = option_name(result[0], global_options)
        if name == '--workspace':
            raise ValueError('Pass --workspace to the launcher before --, not inside the product command')
        if name is None or '=' in result[0]:
            raise ValueError(f'Unsupported or ambiguous leading product option: {result[0]}')
        if name in ('--help', '--version', '-h'):
            return [name, *result[1:]]
        result.pop(0)

    request_commands = {
        'library': ('create', 'search'), 'cue-set': ('create', 'export'),
        'action': ('resolve', 'execute'), 'session': ('create', 'select', 'branch'),
        'constraints': ('set',), 'feedback': ('add',), 'job': ('submit', 'cancel', 'retry'),
        'batch': ('submit', 'retry'), 'audition': ('create',), 'export': ('create',),
    }
    context = tuple(result[:2])
    if len(context) == 2 and context[1] in request_commands.get(context[0], ()):
        options = ('--request', '--help')
        if context == ('action', 'execute'):
            options += ('--expected-resolution-digest',)
    elif context == ('assets', 'import'):
        options = ('--request-id', '--help')
    elif context == ('audition', 'serve'):
        options = ('--ready-file', '--port', '--help')
    else:
        return result  # e.g. constraints show --r means --revision, not a file.

    def absolute(value: str) -> str:
        path = Path(value)
        return str(checked_path(path if path.is_absolute() else cwd / path))

    index = 2
    while index < len(result):
        token = result[index]
        if token == '--':
            break
        name = option_name(token, options)
        if name is not None:
            _, separator, value = token.partition('=')
            result[index] = name + separator + value
            if name in ('--request', '--ready-file'):
                if separator:
                    result[index] = name + '=' + absolute(value)
                else:
                    if index + 1 == len(result):
                        raise ValueError(f'{name} requires a file path')
                    value = result[index + 1]
                    # argparse treats another option as a missing value, but
                    # accepts negative-number filenames as ordinary values.
                    if value.startswith('-') and not re.fullmatch(r'-\d+|-\d*\.\d+', value):
                        raise ValueError(f'{name} requires a file path; use {name}=<path> for a filename starting with -')
                    result[index + 1] = absolute(value)
            if not separator and name != '--help':
                index += 2
                continue
        index += 1
    if result[:2] == ['assets', 'import']:
        index = 2
        while index < len(result):
            if result[index] == '--request-id':
                index += 2
            elif result[index].startswith('--request-id='):
                index += 1
            elif result[index] == '--':
                if index + 1 < len(result): result[index + 1] = absolute(result[index + 1])
                break
            elif result[index].startswith('-'):
                break  # Let the product reject an unknown option.
            else:
                result[index] = absolute(result[index])
                break
    return result


def resolve_plan(args, configuration, cwd: Path):
    executable, tool_root = Path(configuration['python']), Path(configuration['root'])
    if not executable.is_absolute() or not executable.is_file() or not tool_root.is_absolute() or not tool_root.is_dir():
        raise ValueError('Configuration must name an existing absolute interpreter and product checkout')
    consumer = project_root(args.project_root, cwd)
    if args.workspace is not None:
        workspace, source = args.workspace, 'explicit'
    elif args.use_configured_workspace:
        workspace, source = Path(configuration['workspace']), 'configured-explicit-opt-in'
    else:
        workspace, source = consumer / 'artifacts' / 'matter-audio' / args.product, 'project'
    if not workspace.is_absolute():
        raise ValueError('Workspace must be an absolute path')
    workspace = checked_path(workspace)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        command = ['capabilities', '--json']
    command = resolve_file_arguments(command, cwd)
    module = {'core': ['matter_audio_core'], 'score': ['score_matter', 'audio'], 'sonic': ['tools.authoring']}[args.product]
    argv = [str(executable), '-m', *module, '--workspace', str(workspace), *command]
    return {'schema': 'matter-launch-plan/v1', 'product': args.product,
            'project_root': str(consumer), 'workspace': str(workspace), 'workspace_source': source,
            'cwd': str(tool_root.resolve()), 'argv': argv}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'config.local.json')
    parser.add_argument('--product', choices=('core', 'sonic', 'score'), required=True)
    parser.add_argument('--project-root', type=Path, help='Consuming project, independent of the product checkout; defaults to the current Git root or cwd')
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument('--workspace', type=Path, help='Use an explicit existing or new audio workspace, including when resuming prior work')
    scope.add_argument('--use-configured-workspace', action='store_true', help='Explicitly opt into a legacy/shared workspace from machine configuration')
    parser.add_argument('--dry-run', action='store_true', help='Print the resolved command and workspace without starting a product or creating files')
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        configuration = json.loads(args.config.read_text(encoding='utf-8-sig'))[args.product]
        plan = resolve_plan(args, configuration, Path.cwd())
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False))
            return 0
        environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'}
        environment.pop('PYTHONPATH', None)
        completed = subprocess.run(plan['argv'], cwd=plan['cwd'], env=environment, timeout=args.timeout)
        return completed.returncode
    except subprocess.TimeoutExpired:
        error = {'code': 'host_timeout', 'message': 'Host timeout does not confirm backend cancellation. Query the same job with job show and request job cancel if needed; for direct actions use action show. Do not start another attempt blindly.'}
    except (OSError, ValueError, KeyError) as exc:
        error = {'code': 'host_configuration_error', 'message': str(exc)}
    print(json.dumps({'schema': 'matter-error/v1', 'status': 'failed', 'error': error}, ensure_ascii=False))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
