"""Reusable product CLI. All machine responses are one JSON object on stdout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence

from . import __version__
from .actions import ActionService, Registry
from .artifacts import ArtifactStore
from .contracts import read_json
from .errors import AudioError
from .session_contracts import capabilities as session_capabilities
from .job_contracts import capabilities as job_capabilities


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise AudioError("invalid_arguments", message)


def build_parser(product: str, *, allow_import: bool = True) -> Parser:
    parser = Parser(prog=product, description="Local audio authoring and persistent sessions")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--workspace", type=Path, help="Explicit product workspace for assets and actions")
    parser.add_argument("--json", action="store_true", help="JSON is also the default output")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities")
    assets = commands.add_parser("assets").add_subparsers(dest="asset_command", required=True)
    if allow_import:
        importing = assets.add_parser("import")
        importing.add_argument("path", type=Path)
        importing.add_argument("--request-id", required=True)
    showing = assets.add_parser("show")
    showing.add_argument("asset_id")
    listing = assets.add_parser("list")
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--limit", type=int, default=50)
    inspecting = commands.add_parser("inspect")
    inspecting.add_argument("asset_id")
    inspecting.add_argument("--window-frames", type=int)
    inspecting.add_argument("--offset", type=int, default=0)
    inspecting.add_argument("--limit", type=int, default=128)
    action = commands.add_parser("action").add_subparsers(dest="action_command", required=True)
    for name in ("resolve", "execute"):
        operation = action.add_parser(name)
        operation.add_argument("--request", type=Path, required=True)
        if name == "execute":
            operation.add_argument("--expected-resolution-digest")
    action.add_parser("show").add_argument("request_id")
    sessions = commands.add_parser("session").add_subparsers(dest="session_command", required=True)
    for name in ("create", "select", "branch"):
        sessions.add_parser(name).add_argument("--request", type=Path, required=True)
    sessions.add_parser("request").add_argument("request_id")
    sessions.add_parser("migrate")
    for name in ("show", "list"):
        querying = sessions.add_parser(name)
        if name == "show":
            querying.add_argument("session_id")
        querying.add_argument("--offset", type=int, default=0)
        querying.add_argument("--limit", type=int, default=50)
    feedback = commands.add_parser("feedback").add_subparsers(dest="feedback_command", required=True)
    feedback.add_parser("add").add_argument("--request", type=Path, required=True)
    feedback_list = feedback.add_parser("list")
    feedback_list.add_argument("session_id")
    feedback_list.add_argument("--revision", type=int)
    feedback_list.add_argument("--offset", type=int, default=0)
    feedback_list.add_argument("--limit", type=int, default=50)
    context = commands.add_parser("context").add_subparsers(dest="context_command", required=True)
    context_show = context.add_parser("show")
    context_show.add_argument("session_id")
    context_show.add_argument("--history-limit", type=int, default=10)
    context_show.add_argument("--feedback-limit", type=int, default=20)
    jobs = commands.add_parser("job").add_subparsers(dest="job_command", required=True)
    for name in ("submit", "cancel", "retry"):
        jobs.add_parser(name).add_argument("--request", type=Path, required=True)
    for name in ("run", "recover", "show"):
        querying = jobs.add_parser(name)
        querying.add_argument("job_id")
        if name == "show":
            querying.add_argument("--offset", type=int, default=0)
            querying.add_argument("--limit", type=int, default=50)
    job_list = jobs.add_parser("list")
    job_list.add_argument("--session", dest="session_id")
    job_list.add_argument("--offset", type=int, default=0)
    job_list.add_argument("--limit", type=int, default=50)
    batch = commands.add_parser("batch").add_subparsers(dest="batch_command", required=True)
    for name in ("submit", "retry"):
        batch.add_parser(name).add_argument("--request", type=Path, required=True)
    for name in ("run", "recover", "show"):
        batch.add_parser(name).add_argument("batch_id")
    return parser


def session_command(args, store, registry):
    # Audio-only commands and capability discovery do not open a database.
    from .sessions import SessionService
    service = SessionService(store, registry)
    if args.command == "session":
        if args.session_command == "migrate":
            return service.migrate()
        if args.session_command in ("create", "select", "branch"):
            return service.mutate(args.session_command, read_json(args.request))
        if args.session_command == "request":
            return service.request_status(args.request_id)
        if args.session_command == "show":
            return service.show(args.session_id, offset=args.offset, limit=args.limit)
        return service.list_sessions(offset=args.offset, limit=args.limit)
    if args.command == "feedback":
        if args.feedback_command == "add":
            return service.mutate("feedback", read_json(args.request))
        return service.list_feedback(args.session_id, revision=args.revision, offset=args.offset, limit=args.limit)
    return service.context(args.session_id, history_limit=args.history_limit, feedback_limit=args.feedback_limit)


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


def job_command(args, store, registry):
    from .jobs import JobService
    service = JobService(store, registry)
    if args.command == "batch":
        if args.batch_command in ("submit", "retry"):
            return service.mutate("batch_" + args.batch_command, read_json(args.request))
        return getattr(service, "batch_" + args.batch_command)(args.batch_id)
    if args.job_command in ("submit", "cancel", "retry"):
        return service.mutate(args.job_command, read_json(args.request))
    if args.job_command == "show":
        return service.show(args.job_id, offset=args.offset, limit=args.limit)
    if args.job_command == "list":
        return service.list_jobs(session_id=args.session_id, offset=args.offset, limit=args.limit)
    return getattr(service, args.job_command)(args.job_id)


def run(argv: Sequence[str] | None = None, *, product: str = "matter-audio",
        registry: Registry | None = None, allow_import: bool = True,
        extend_parser: Callable | None = None, handle_extra: Callable | None = None,
        prepare_workspace: Callable | None = None, capability_extra: Callable | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Permit --json after any subcommand while retaining argparse's normal help.
    arguments = [arg for arg in arguments if arg != "--json"]
    try:
        parser = build_parser(product, allow_import=allow_import)
        if extend_parser:
            extend_parser(parser)
        args = parser.parse_args(arguments)
        registry = registry or Registry()
        if args.command == "capabilities":
            result = {"schema": "matter-capabilities/v1", "product": product,
                      "core_version": __version__, "operations": registry.capabilities(),
                      "sessions": session_capabilities(),
                      "jobs": job_capabilities(),
                      "transport": "cli-json/v1", "audio_model_calls": 0,
                      "limitations": ["Fades, region locks and model editing are not implemented."]}
            if capability_extra:
                result["product_capabilities"] = capability_extra()
        else:
            if args.workspace is None:
                raise AudioError("workspace_required", "Pass --workspace before the command")
            if prepare_workspace:
                prepare_workspace(args.workspace)
            store = ArtifactStore(args.workspace, product=product)
            service = ActionService(store, registry)
            if args.command == "assets":
                if args.asset_command == "import":
                    result = store.import_wav(args.path, args.request_id)
                elif args.asset_command == "show":
                    result = {"asset": store.asset(args.asset_id)[0]}
                else:
                    if not 0 <= args.offset or not 1 <= args.limit <= 100:
                        raise AudioError("invalid_arguments", "Asset offset >= 0; limit 1..100")
                    result = store.list_assets(offset=args.offset, limit=args.limit)
            elif args.command == "inspect":
                params = {"offset": args.offset, "limit": args.limit}
                if args.window_frames is not None:
                    params["window_frames"] = args.window_frames
                result = service.inspect_asset(args.asset_id, params)
            elif args.command == "action":
                if args.action_command == "show":
                    result = store.show_request(args.request_id)
                elif args.action_command == "resolve":
                    result = service.resolve(read_json(args.request))
                else:
                    result = service.execute(read_json(args.request),
                                             expected_resolution_digest=args.expected_resolution_digest)
            elif args.command in ("session", "feedback", "context"):
                result = session_command(args, store, registry)
            elif args.command in ("job", "batch"):
                result = job_command(args, store, registry)
            elif handle_extra:
                result = handle_extra(args, store)
            else:
                raise AudioError("unsupported_command", args.command)
            if "outputs" in result:
                result = {**result, "playback": store.playback_refs(result)}
        emit(result)
        return 2 if result.get("status") in ("failed", "interrupted", "cancelled", "partial_failure") else 0
    except AudioError as exc:
        emit({"schema": "matter-error/v1", "status": "failed", "error": exc.document()})
        return 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        emit({"schema": "matter-error/v1", "status": "failed",
              "error": {"code": "io_or_integrity_error", "message": str(exc), "details": {}}})
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)
