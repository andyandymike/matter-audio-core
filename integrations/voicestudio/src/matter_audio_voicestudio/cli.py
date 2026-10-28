"""Explicit preview/execute interface for an independently deployed service."""

from pathlib import Path
import sys

from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.cli import Parser, emit
from matter_audio_core.contracts import read_json
from matter_audio_core.diagnostics import backend_report, build_report, check
from matter_audio_core.errors import AudioError

from . import __version__, client
from .service import LIMITATIONS, REQUEST_SCHEMA, TranscriptionService, export_result
from . import editing

PRODUCTS = {"core": "matter-audio", "score": "score-matter", "sonic": "sonic-matter"}


def doctor(path):
    if path is None:
        checks = [check("config", "configuration", "error", "configuration_missing",
                        "No transcription service was configured.", remedy="Pass --config with a local service configuration file.")]
        configured = False
    else:
        try:
            config = client.load_config(path)
        except AudioError:
            checks = [check("config", "configuration", "error", "configuration_invalid",
                            "The service configuration could not be validated.", remedy="Compare its fields with config.example.json.")]
            configured = False
        else:
            configured = True
            checks = [check("config", "configuration", "ok", "configuration_valid", "Service configuration is syntactically valid; the server was not contacted.")]
            try:
                client.credential(config)
            except AudioError:
                checks.append(check("authentication", "configuration", "error", "authentication_unavailable",
                                    "The configured environment credential is missing or invalid.", remedy="Set the named environment variable; never put the secret in the config file."))
            else:
                checks.append(check("authentication", "configuration", "ok", "authentication_configured",
                                    "Local authentication prerequisites passed; server acceptance is unverified."))
            checks.append(check("device", "device", "unknown", "remote_device_unobserved", "The remote model and execution device are not probed."))
    return build_report("matter-voicestudio", [backend_report("voicestudio-transcription", configured=configured, checks=checks)])


def main(argv=None):
    try:
        parser = Parser(prog="matter-voicestudio", description=__doc__)
        parser.add_argument("--version", action="version", version=__version__)
        parser.add_argument("--config", type=Path)
        parser.add_argument("--workspace", type=Path)
        parser.add_argument("--product", choices=PRODUCTS, default="core")
        commands = parser.add_subparsers(dest="command", required=True)
        commands.add_parser("capabilities")
        commands.add_parser("doctor")
        transcribing = commands.add_parser("transcribe").add_subparsers(dest="action", required=True)
        for name in ("resolve", "execute"):
            command = transcribing.add_parser(name)
            command.add_argument("--request", type=Path, required=True)
            if name == "execute":
                command.add_argument("--expected-resolution-digest", required=True)
        transcribing.add_parser("show").add_argument("request_id")
        inspecting = transcribing.add_parser("inspect", help="Read indexed word or segment timing without contacting the service.")
        inspecting.add_argument("request_id")
        inspecting.add_argument("--unit", choices=("word", "segment"), default="word")
        inspecting.add_argument("--offset", type=int, default=0)
        inspecting.add_argument("--limit", type=int, default=50)
        exporting = transcribing.add_parser("export")
        exporting.add_argument("request_id")
        exporting.add_argument("--format", choices=("json", "srt", "vtt"), required=True)
        exporting.add_argument("--output", type=Path, required=True)
        edits = commands.add_parser("edit", help="Plan and execute offline transcript-guided PCM edits.").add_subparsers(dest="action", required=True)
        edits.add_parser("plan").add_argument("--request", type=Path, required=True)
        edits.add_parser("show").add_argument("asset_id")
        executing = edits.add_parser("execute")
        executing.add_argument("asset_id")
        executing.add_argument("--expected-plan-digest", required=True)
        args = parser.parse_args([item for item in (sys.argv[1:] if argv is None else argv) if item != "--json"])
        if args.command == "capabilities":
            result = {"schema": "matter-voicestudio-capabilities/v1", "adapter_version": __version__,
                      "transport": "cli-json/v1", "request_schema": REQUEST_SCHEMA,
                      "commands": ["doctor", "transcribe resolve", "transcribe execute", "transcribe show", "transcribe export",
                                   "transcribe inspect", "edit plan", "edit show", "edit execute"],
                      "editing": editing.capabilities(),
                      "formats": ["json", "srt", "vtt"], "audio_model_calls": 0, "network_requests": 0,
                      "limitations": LIMITATIONS}
        elif args.command == "doctor":
            result = doctor(args.config)
        else:
            if args.workspace is None:
                raise AudioError("workspace_required", "Pass the existing audio workspace with --workspace.")
            store = ArtifactStore(args.workspace, product=PRODUCTS[args.product])
            if args.command == "edit":
                if args.action == "plan":
                    result = editing.plan(store, read_json(args.request))
                elif args.action == "show":
                    result = editing.show(store, args.asset_id)
                else:
                    result = editing.execute(store, args.asset_id, args.expected_plan_digest)
            elif args.action == "inspect":
                result = editing.inspect_transcript(store, args.request_id, args.unit, args.offset, args.limit)
            elif args.action == "show":
                result = store.show_request(args.request_id)
                if result.get("binding", {}).get("operation") != "voicestudio.transcribe/v1":
                    raise AudioError("transcription_unavailable", "Request is not a transcription.")
            elif args.action == "export":
                result = export_result(store, args.request_id, args.format, args.output)
            else:
                if args.config is None:
                    raise AudioError("configuration_required", "Pass --config to select the external transcription service.")
                service = TranscriptionService(store, client.load_config(args.config))
                request = read_json(args.request)
                result = (service.resolve(request) if args.action == "resolve" else
                          service.execute(request, args.expected_resolution_digest))
        emit(result)
        return 2 if result.get("status") == "failed" else 0
    except AudioError as exc:
        emit({"schema": "matter-error/v1", "status": "failed", "error": exc.document()})
    except (OSError, ValueError, TypeError, KeyError):
        emit({"schema": "matter-error/v1", "status": "failed", "error": {
            "code": "adapter_io_error", "message": "A local input, export target or adapter value could not be processed."}})
    return 2
