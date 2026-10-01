"""Launch one local ScoreMatter BGM candidate with project-local output and records."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from run_audio import project_root


# Probe only the configured product's parser, argument validation and file metadata.
# No model import, model execution, output-directory creation or record write occurs.
PREFLIGHT = r'''
import inspect, json, sys
from pathlib import Path
from score_matter import authoring
from score_matter.cli import _build_parser
if "record_root" not in inspect.signature(authoring.generate_sa3_wav).parameters:
    raise RuntimeError("Configured ScoreMatter needs generate --record-root support")
args = _build_parser().parse_args(json.loads(sys.argv[1]))
settings = authoring.SA3GenerationSettings(
    seconds=args.seconds, seed=args.seed, steps=args.steps, threads=args.threads,
    cfg=args.cfg, apg=args.apg, negative_prompt=args.negative_prompt,
    play=False, timeout_seconds=args.timeout_seconds)
runtime = authoring.resolve_sa3_runtime(args.runtime_root)
command = authoring.build_sa3_command(runtime=runtime, prompt=args.prompt,
    output=args.out, settings=settings, seed=args.seed if args.seed is not None else 0)
print(json.dumps({"status":"ready_for_one_local_model_call", "model_calls":0,
    "product_module":str(Path(authoring.__file__).resolve()),
    "runtime_root":str(runtime.root), "runtime_python":str(runtime.python),
    "runtime_script":str(runtime.script), "record_root":str(args.record_root),
    "component_check":"exists_and_nonzero_size_only",
    "runtime_command_preview":command,
    "seed_preview_only":args.seed is None,
    "defaults": {"seconds":args.seconds,"steps":args.steps,"threads":args.threads,"cfg":args.cfg}},
    ensure_ascii=False))
'''


def check_directory_path(path: Path) -> None:
    """Reject known file/dangling-link ancestors without creating a directory."""
    for candidate in (path, *path.parents):
        if candidate.is_symlink() and not candidate.exists():
            raise ValueError(f'Directory path contains a dangling link: {candidate}')
        if candidate.exists():
            if not candidate.is_dir():
                raise ValueError(f'Directory path contains a non-directory: {candidate}')
            return


def resolve_plan(args, configuration: dict, cwd: Path) -> dict:
    executable = Path(configuration['python'])
    tool_root = Path(configuration['root'])
    for path, valid in ((executable, executable.is_file()), (tool_root, tool_root.is_dir())):
        if not path.is_absolute() or not valid:
            raise ValueError('Configuration must name an existing absolute interpreter and product checkout')
    consumer = project_root(args.project_root, cwd)
    configured_runtime = args.runtime_root or configuration.get('sa3_runtime_root')
    if configured_runtime is None:
        raise ValueError('Set score.sa3_runtime_root in machine config or pass --runtime-root')
    runtime = Path(configured_runtime)
    if not runtime.is_absolute() or not runtime.is_dir():
        raise ValueError('SA3 runtime root must be an existing absolute directory')
    if not args.prompt and not args.check:
        raise ValueError('--prompt is required for generation or --dry-run')
    prompt = args.prompt if args.prompt is not None else 'Configuration preflight only'
    if args.out is not None:
        output = args.out if args.out.is_absolute() else cwd / args.out
    else:
        run = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + secrets.token_hex(3)
        output = consumer / 'artifacts/matter-audio/score/generation' / run / 'candidate.wav'
    if output.exists() or output.is_symlink():
        raise ValueError(f'Output already exists; use the existing result or choose a new version: {output}')
    check_directory_path(output.parent)
    output = output.resolve()
    if output.suffix.lower() != '.wav':
        raise ValueError('BGM output must end in .wav')
    record_root = args.record_root or output.parent / 'records'
    if not record_root.is_absolute():
        record_root = cwd / record_root
    check_directory_path(record_root)
    record_root = record_root.resolve()
    if record_root == output or output in record_root.parents:
        raise ValueError('Record directory cannot be the planned WAV or a child of that file')
    command = ['generate', '--prompt', prompt, '--out', str(output),
               '--record-root', str(record_root), '--runtime-root', str(runtime.resolve())]
    for name in ('seconds', 'seed', 'steps', 'threads', 'cfg', 'apg', 'negative_prompt'):
        value = getattr(args, name)
        if value is not None:
            command.extend(['--' + name.replace('_', '-'), str(value)])
    if not 1 <= args.timeout_seconds <= 86400:
        raise ValueError('--timeout-seconds must be between 1 and 86400')
    command.extend(['--timeout-seconds', str(args.timeout_seconds)])
    return {'schema':'matter-bgm-launch-plan/v1', 'project_root':str(consumer),
            'output':str(output), 'record_root':str(record_root), 'runtime_root':str(runtime.resolve()),
            'cwd':str(tool_root.resolve()), 'argv':[str(executable), '-m', 'score_matter', *command],
            'model_calls':0, 'runtime_checked':False}


def environment() -> dict:
    result = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONUTF8':'1',
              'HF_HUB_OFFLINE':'1', 'HF_DATASETS_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1',
              'HF_HUB_DISABLE_TELEMETRY':'1', 'HF_HUB_DISABLE_IMPLICIT_TOKEN':'1', 'DO_NOT_TRACK':'1'}
    result.pop('PYTHONPATH', None)
    return result


def check_backend(plan: dict, env: dict) -> dict:
    result = subprocess.run([plan['argv'][0], '-c', PREFLIGHT, json.dumps(plan['argv'][3:])],
                            cwd=plan['cwd'], env=env, capture_output=True, text=True,
                            encoding='utf-8', timeout=30)
    if result.returncode:
        raise ValueError('ScoreMatter preflight failed; no model was launched: ' +
                         (result.stderr or result.stdout).strip()[-5000:])
    evidence = json.loads(result.stdout)
    module = Path(evidence['product_module']).resolve()
    if not module.is_relative_to(Path(plan['cwd']).resolve()):
        raise ValueError('Configured interpreter imports ScoreMatter outside its configured checkout')
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'config.local.json')
    parser.add_argument('--project-root', type=Path)
    parser.add_argument('--runtime-root', type=Path, help='Override the configured absolute SA3 TFLite runtime')
    parser.add_argument('--out', type=Path, help='New WAV path; relative paths resolve against caller cwd')
    parser.add_argument('--record-root', type=Path, help='Optional record directory; defaults beside the new WAV')
    parser.add_argument('--prompt')
    for name in ('seconds', 'seed', 'steps', 'threads'):
        parser.add_argument('--' + name, type=int)
    for name in ('cfg', 'apg'):
        parser.add_argument('--' + name, type=float)
    parser.add_argument('--negative-prompt')
    parser.add_argument('--timeout-seconds', type=int, default=600, help='Backend generation timeout; never retries')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Print paths and argv without starting a child or creating files')
    mode.add_argument('--check', action='store_true', help='Validate the real product, arguments and local runtime file metadata without generation')
    args = parser.parse_args()
    executing = False
    plan = None
    try:
        configuration = json.loads(args.config.read_text(encoding='utf-8-sig'))['score']
        plan = resolve_plan(args, configuration, Path.cwd())
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False))
            return 0
        env = environment()
        evidence = check_backend(plan, env)
        if args.check:
            print(json.dumps({**plan, 'runtime_checked':True, 'preflight':evidence}, ensure_ascii=False))
            return 0
        # Give ScoreMatter time to enforce its own model-process timeout and publish evidence.
        print(json.dumps({'schema':'matter-bgm-launch/v1', 'output':plan['output'],
                          'record_root':plan['record_root'], 'automatic_retries':0}, ensure_ascii=False),
              file=sys.stderr, flush=True)
        executing = True
        completed = subprocess.run(plan['argv'], cwd=plan['cwd'], env=env,
                                   timeout=args.timeout_seconds + 120)
        return completed.returncode
    except subprocess.TimeoutExpired:
        error = ({'code':'bgm_host_timeout', 'message':'Outcome is uncertain. Inspect the exact output, records and running process before any retry. This direct generation route has no shared action/job ID.'}
                 if executing else {'code':'bgm_preflight_timeout', 'message':'Product preflight timed out; no generation was launched.'})
    except (OSError, ValueError, KeyError) as exc:
        error = {'code':'bgm_configuration_error', 'message':str(exc)}
    context = {key:plan[key] for key in ('output', 'record_root')} if plan else {}
    print(json.dumps({'schema':'matter-error/v1', 'status':'failed', 'error':error, **context}, ensure_ascii=False))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
