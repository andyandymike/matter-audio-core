"""Five synthetic transcript-editing tasks, driven by fresh CLI processes.

This is an engineering baseline, not an LLM-agent or listening evaluation.
The case catalog separates public task instructions from evaluator-only oracles.
A future agent can replace the scripted task driver and submit the same trace
shape; success must still be judged from independently inspected workspace
artifacts, never from an agent's self-reported completion or quality claims.
"""

from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.contracts import canonical
from matter_audio_core.delivery import ExportService
from matter_audio_core.errors import AudioError
from matter_audio_core.media import PCM, decode_wav, encode_wav, sample_bytes
from matter_audio_core.sessions import SessionService
from matter_audio_voicestudio import client, editing
from matter_audio_voicestudio.service import TranscriptionService


RATE = 8000
FRAMES = 16000
CASE_SCHEMA = "matter-editing-eval-cases/v1"
TRACE_SCHEMA = "matter-editing-eval-trajectory/v1"
REPORT_SCHEMA = "matter-editing-eval-report/v1"
REJECTION_CHECKS = ("expected_rejection", "no_publication", "session_unchanged", "source_unchanged", "no_feedback")
SUCCESS_CHECKS = ("verified_plan", "exact_pcm", "protected_pcm", "source_unchanged", "quality_coverage",
                  "explicit_selection", "exact_export", "no_feedback", "zero_model_receipts")


def case_catalog():
    """Stable task IDs and separate, evaluator-owned scoring oracles."""
    return {"schema": CASE_SCHEMA, "fixture": "synthetic-pcm-and-transcript/v1", "cases": [
        {"id": "extract_sentence", "task": "Extract the first complete sentence, retain its locked audio, inspect signal locations, then explicitly select and export the result.",
         "selection": {"unit": "segment", "index": 1}, "edit_kind": "extract", "lock": [2000, 4000],
         "oracle": {"outcome": "success", "source_range": [0, 8000], "output_frames": 8000}},
        {"id": "replace_third_echo", "task": "Replace the third occurrence of echo with the supplied replacement from frame 500, preserving the locked prefix and all audio outside that word. Inspect, explicitly select and export it.",
         "selection": {"unit": "word", "text": "echo", "occurrence": 3}, "edit_kind": "replace", "lock": [0, 4000],
         "oracle": {"outcome": "success", "source_range": [8000, 10000], "replacement_start_frame": 500,
                    "output_frames": FRAMES}},
        {"id": "reject_ambiguous_echo", "task": "Extract echo; no occurrence is specified. Refuse an ambiguous edit without changing the workspace selection.",
         "selection": {"unit": "word", "text": "echo"}, "edit_kind": "extract", "lock": [0, 4000],
         "oracle": {"outcome": "reject", "error_code": "selection_ambiguous"}},
        {"id": "reject_missing_timestamps", "task": "Extract the first word from a transcript with text but no timestamps. Do not invent edit boundaries.",
         "selection": {"unit": "word", "index": 1}, "edit_kind": "extract", "lock": [0, 4000],
         "missing_timings": True, "oracle": {"outcome": "reject", "error_code": "selection_unavailable"}},
        {"id": "reject_protected_word", "task": "Replace the third echo, which is locked. Reject the edit without publishing audio or changing the lock or selection.",
         "selection": {"unit": "word", "text": "echo", "occurrence": 3}, "edit_kind": "replace", "lock": [8000, 10000],
         "oracle": {"outcome": "reject", "error_code": "constraint_violation"}},
    ], "agent_submission_contract": {
        "trajectory_schema": TRACE_SCHEMA,
        "public_task_fields": ["id", "task"],
        "private_evaluator_fields": ["selection", "edit_kind", "lock", "oracle", "missing_timings"],
        "events": ["sequence", "phase", "module", "arguments", "return_code", "elapsed_seconds", "response"],
        "required_success_checks": list(SUCCESS_CHECKS), "required_rejection_checks": list(REJECTION_CHECKS),
        "grading": "All required checks must pass; evidence is recomputed from saved artifacts and recorded CLI results. Do not accept submitted pass flags as evidence.",
        "agent_execution": "prepare-only supplies briefs and exact IDs; grade-existing verifies saved results without doing tasks",
    }}


def grade_checks(outcome, checks):
    """Missing evidence is unknown, never a falsely successful task."""
    required = SUCCESS_CHECKS if outcome == "success" else REJECTION_CHECKS if outcome == "reject" else ()
    if not required:
        raise ValueError("Unknown evaluation outcome")
    failed = [name for name in required if checks.get(name) is False]
    unknown = [name for name in required if checks.get(name) is not True and checks.get(name) is not False]
    return {"completed": False if failed else None if unknown else True,
            "failed_checks": failed, "unknown_checks": unknown,
            "checks": {name: checks.get(name) for name in required}}


def _payload(replacement=False):
    # Deterministic nonconstant signals make wrong range/channel/identity choices
    # observable. They are test data, not speech or listening-quality fixtures.
    length = 3000 if replacement else FRAMES
    values = array("h", [((index * (43 if replacement else 31)) % 5001) - 2500 for index in range(length)])
    return sample_bytes(values)


def _transcript(missing=False):
    result = {"text": "echo keep echo tail. echo end.", "language": "en", "model": "synthetic-stub"}
    if not missing:
        result["words"] = [{"word": word, "start": start, "end": end} for word, start, end in (
            ("echo", 0, .25), ("keep", .25, .5), ("echo", .5, .75),
            ("tail", .75, 1), ("echo", 1, 1.25), ("end", 1.25, 2))]
        result["segments"] = [{"text": "echo keep echo tail.", "start": 0, "end": 1},
                              {"text": "echo end.", "start": 1, "end": 2}]
    return result


def _setup(root, case):
    store = ArtifactStore(root / "workspace")
    paths, assets = {}, {}
    for name, replacement in (("source", False), ("replacement", True)):
        path = root / (name + ".wav")
        path.write_bytes(encode_wav(PCM(_payload(replacement), RATE, 1)))
        paths[name] = path
        assets[name] = store.import_wav(path, "import-" + name)["outputs"][0]
    config_path = root / "stub-config.json"
    config_path.write_bytes(canonical({"schema": "matter-voicestudio-config/v1", "base_url": "http://127.0.0.1:9",
                                      "model": "synthetic-stub", "service_revision": "fixture-1"}))
    service = TranscriptionService(store, client.load_config(config_path))
    request = {"schema": "matter-transcription-request/v1", "request_id": "transcript",
               "asset_id": assets["source"]["asset_id"], "timestamps": "word"}
    resolution = service.resolve(request)
    with patch.object(client, "transcribe", return_value=_transcript(case.get("missing_timings", False))) as stub, \
            patch("socket.create_connection", side_effect=AssertionError("Fixture must not contact a server")):
        transcript = service.execute(request, resolution["digest"]["hex"])
    if transcript["status"] != "succeeded" or stub.call_count != 1:
        raise RuntimeError("Synthetic transcript fixture was not published")
    sessions = SessionService(store)
    sessions.mutate("create", {"schema": "matter-session-create/v1", "request_id": "create", "session_id": "editing",
                               "name": "Synthetic evaluation", "asset_id": assets["source"]["asset_id"]})
    start, end = case["lock"]
    sessions.mutate("constraints", {"schema": "matter-constraints-set/v1", "request_id": "lock", "session_id": "editing",
                                    "expected_revision": 1, "regions": [{"start_frame": start, "end_frame": end}]})
    return store, paths, assets, transcript, sessions.show("editing")


class Runner:
    def __init__(self, root, workspace):
        self.root, self.workspace, self.events = root, workspace, []

    def call(self, module, *arguments, phase="task"):
        command = [sys.executable, "-m", module, "--workspace", str(self.workspace), *map(str, arguments), "--json"]
        started = time.perf_counter()
        try:
            completed = subprocess.run(command, cwd=self.root, env=os.environ.copy(), capture_output=True,
                                       encoding="utf-8", timeout=90)
        except subprocess.TimeoutExpired:
            return_code = None
            response = {"status": "failed", "error": {"code": "evaluation_cli_timeout"}}
        else:
            return_code = completed.returncode
            try:
                response = json.loads(completed.stdout)
            except ValueError:
                response = {"invalid_json": True, "stdout": completed.stdout[:4096], "stderr": completed.stderr[:4096]}
        elapsed = time.perf_counter() - started
        event = {"sequence": len(self.events), "phase": phase, "module": module,
                 "arguments": list(map(str, arguments)), "return_code": return_code,
                 "elapsed_seconds": elapsed, "response": response}
        self.events.append(event)
        return event

    def request(self, module, command, document, *, phase="task"):
        path = self.root / f"request-{len(self.events):03}.json"
        path.write_bytes(canonical(document))
        return self.call(module, *command, "--request", path, phase=phase)


def _ok(event):
    if event["return_code"] != 0 or event["response"].get("status") == "failed" or event["response"].get("invalid_json"):
        raise RuntimeError(f"CLI step {event['sequence']} failed: {event['response']}")
    return event["response"]


def _expected_pcm(case):
    start, end = case["oracle"]["source_range"]
    source = _payload()
    if case["edit_kind"] == "extract":
        return source[2 * start:2 * end]
    replacement = _payload(True)
    offset = case["oracle"]["replacement_start_frame"]
    return source[:2 * start] + replacement[2 * offset:2 * (offset + end - start)] + source[2 * end:]



def prepare(output_dir, case_ids=None):
    """Create independent fixtures and public briefs, but do none of the tasks."""
    catalog = case_catalog()
    cases = [case for case in catalog["cases"] if case_ids is None or case["id"] in case_ids]
    if not cases or (case_ids is not None and set(case_ids) != {case["id"] for case in cases}):
        raise ValueError("Unknown or empty case selection")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=False)
    tasks = []
    for case in cases:
        directory = root / case["id"]
        directory.mkdir()
        started = time.perf_counter()
        store, paths, assets, transcript, session = _setup(directory, case)
        state = {"assets": assets, "transcript_asset_id": transcript["transcript_asset_id"],
                 "original_session": session, "setup_seconds": time.perf_counter() - started,
                 "request_directories": sorted(p.name for p in (store.root / "requests").iterdir()),
                 "object_directories": sorted(p.name for p in (store.root / "objects").iterdir())}
        (directory / "evaluator-state.json").write_bytes(canonical(state))
        tasks.append({"case_id": case["id"], "task": case["task"], "workspace": str(store.root),
            "product": "core", "source_asset_id": assets["source"]["asset_id"],
            "replacement_asset_id": assets["replacement"]["asset_id"],
            "transcription_request_id": "transcript", "transcript_asset_id": transcript["transcript_asset_id"],
            "session_id": "editing", "expected_revision": 2, "protection": {"session_id": "editing", "revision": 2},
            "request_ids": {"edit_plan": "edit-plan", "quality": "quality", "select": "select", "export": "export"},
            "instructions": ["Inspect the saved transcript using fresh CLI commands; no service configuration is needed.",
                "Bind the supplied protection reference when planning. Do not unlock, replace the source, or write feedback.",
                "For a successful edit, show the saved plan and execute its exact digest. Inspect signal locations via Core action execute quality/v1 with profile speech and request_id quality, saving its receipt.",
                "Select using request_id select and expected_revision 2; export through Core export create with request_id export and expected_revision 3.",
                "For an unsafe or ambiguous task, stop after the structured rejection. Do not invent a different request or choose an occurrence."]})
    manifest = {"schema": "matter-editing-eval-prepared/v1", "fixture": catalog["fixture"],
                "case_ids": [case["id"] for case in cases]}
    (root / "prepared.json").write_bytes(canonical(manifest))
    (root / "tasks.json").write_bytes(canonical({"schema": "matter-editing-eval-tasks/v1", "tasks": tasks}))
    (root / "cases.evaluator.json").write_bytes(canonical(catalog))
    return {"schema": manifest["schema"], "status": "prepared", "task_file": str(root / "tasks.json"),
            "tasks": len(tasks), "audio_model_calls": 0, "network_requests": 0,
            "note": "Give agents tasks.json, not evaluator-only case definitions. No editing task has run."}


def _state(root):
    return json.loads((root / "evaluator-state.json").read_text(encoding="utf-8"))


def _scripted_task(root, case):
    """Only the task driver executes commands. The grader below never does."""
    state = _state(root)
    runner = Runner(root, root / "workspace")
    assets = state["assets"]
    edit = {"kind": case["edit_kind"]}
    if edit["kind"] == "replace":
        edit.update(replacement_asset_id=assets["replacement"]["asset_id"], replacement_start_frame=500, transition_frames=0)
    request = {"schema": "matter-transcript-edit/v1", "request_id": "edit-plan", "transcription_request_id": "transcript",
               "transcript_asset_id": state["transcript_asset_id"], "source_asset_id": assets["source"]["asset_id"],
               "selection": case["selection"], "edit": edit, "protection": {"session_id": "editing", "revision": 2}}
    observations, failure = {}, None
    started = time.perf_counter()
    try:
        inspected = _ok(runner.call("matter_audio_voicestudio", "transcribe", "inspect", "transcript",
                                    "--unit", case["selection"]["unit"], "--offset", 0, "--limit", 50))
        expected_items = 0 if case.get("missing_timings") else 6 if case["selection"]["unit"] == "word" else 2
        observations["inspect"] = inspected["total_items"] == expected_items and len(inspected["items"]) == expected_items
        event = runner.request("matter_audio_voicestudio", ("edit", "plan"), request)
        if case["oracle"]["outcome"] == "reject":
            observations["expected_rejection"] = (event["return_code"] == 2 and
                event["response"].get("error", {}).get("code") == case["oracle"]["error_code"])
        else:
            planned = _ok(event)
            plan_id = planned["plan"]["asset_id"]
            shown = _ok(runner.call("matter_audio_voicestudio", "edit", "show", plan_id))
            observations["show"] = shown["document"] == planned["document"] and shown["plan_digest"] == planned["plan_digest"]
            result = _ok(runner.call("matter_audio_voicestudio", "edit", "execute", plan_id,
                                     "--expected-plan-digest", planned["plan_digest"]))
            replay = _ok(runner.call("matter_audio_voicestudio", "edit", "execute", plan_id,
                                     "--expected-plan-digest", planned["plan_digest"]))
            observations["replay"] = replay == result
            selected = _ok(runner.call("matter_audio_core", "session", "show", "editing", phase="verification"))
            observations["no_implicit_selection"] = selected == state["original_session"]
            output_id = result["outputs"][0]["asset_id"]
            _ok(runner.request("matter_audio_core", ("action", "execute"), {"schema": "matter-action/v1",
                "request_id": "quality", "operation": "quality/v1", "inputs": [output_id], "parameters": {"profile": "speech"}}))
            _ok(runner.request("matter_audio_core", ("session", "select"), {"schema": "matter-session-select/v1",
                "request_id": "select", "session_id": "editing", "expected_revision": 2, "asset_id": output_id}))
            _ok(runner.request("matter_audio_core", ("export", "create"), {"schema": "matter-export/v1",
                "request_id": "export", "session_id": "editing", "expected_revision": 3}))
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        failure = str(exc)
    trajectory = {"schema": TRACE_SCHEMA, "case_id": case["id"], "driver": "scripted_baseline",
                  "workspace": str(runner.workspace), "events": runner.events}
    (root / "trajectory.json").write_bytes(canonical(trajectory))
    return {"events": runner.events, "observations": observations, "failure": failure,
            "elapsed_seconds": time.perf_counter() - started}


def grade_case(root, case, trusted_run=None):
    """Read completed artifacts only; never plan, execute, select or fix a task.

    trusted_run is only passed directly by this process's scripted runner. Files
    submitted by an external agent are not trusted timing/rejection evidence.
    """
    if not (root / "workspace" / "workspace.json").is_file():
        raise ValueError("Expected an already prepared workspace")
    store = ArtifactStore(root / "workspace")
    state = _state(root)
    sessions = SessionService(store)
    checks, evidence, failure = {}, {}, None
    try:
        checks["source_unchanged"] = all(store.asset(asset["asset_id"])[1] == (root / (name + ".wav")).read_bytes() ==
            encode_wav(PCM(_payload(name == "replacement"), RATE, 1)) for name, asset in state["assets"].items())
        checks["no_feedback"] = not sessions.list_feedback("editing")["feedback"]
        current = sessions.show("editing")
        if case["oracle"]["outcome"] == "reject":
            checks["expected_rejection"] = None if trusted_run is None else trusted_run["observations"].get("expected_rejection", False)
            checks["no_publication"] = (state["request_directories"] == sorted(p.name for p in (store.root / "requests").iterdir()) and
                state["object_directories"] == sorted(p.name for p in (store.root / "objects").iterdir()))
            checks["session_unchanged"] = current == state["original_session"]
        else:
            # A fresh prepare-only workspace is an incomplete task, not a reason
            # for the evaluator to perform the missing steps on the agent's behalf.
            checks.update({name: False for name in SUCCESS_CHECKS if name not in checks})
            saved = store.show_request("edit-plan")
            shown = editing.show(store, saved["plan"]["asset_id"])
            document = shown["document"]
            request = document["binding"]["request"]
            start, end = case["oracle"]["source_range"]
            checks["verified_plan"] = (shown["asset"] == saved["plan"] and document == saved["document"] and
                request["source_asset_id"] == state["assets"]["source"]["asset_id"] and
                request["transcript_asset_id"] == state["transcript_asset_id"] and
                request["edit"]["kind"] == case["edit_kind"] and
                request.get("protection") == {"session_id": "editing", "revision": 2} and
                document["binding"]["selection"]["range"] == {"start_frame": start, "end_frame": end})
            executed = store.show_request(document["core_request"]["request_id"])
            if executed["status"] != "succeeded" or executed["binding"] != {"resolution": document["core_resolution"]}:
                raise ValueError("No successful action bound to the saved plan")
            output = executed["outputs"][0]
            _, raw = store.asset(output["asset_id"])
            pcm = decode_wav(raw)
            checks["exact_pcm"] = (pcm.payload == _expected_pcm(case) and pcm.frames == case["oracle"]["output_frames"] and
                                     pcm.sample_rate == RATE and pcm.channels == 1)
            left, right = case["lock"]
            shift = start if case["edit_kind"] == "extract" else 0
            checks["protected_pcm"] = (pcm.payload[2 * (left - shift):2 * (right - shift)] == _payload()[2 * left:2 * right] and
                sessions.revision("editing", 2) == state["original_session"]["current"] and
                current["current"]["constraints"] == state["original_session"]["current"]["constraints"])
            quality = store.show_request("quality")
            finding = quality["findings"][0]
            checks["quality_coverage"] = (quality["status"] == "succeeded" and quality["resolution"]["request"]["operation"] == "quality/v1" and
                quality["resolution"]["request"]["inputs"] == [output["asset_id"]] and finding["advisory"] is True and
                finding["scanned_frames"] == pcm.frames and finding["unscanned_frames"] == 0 and finding["content_profile"] == "speech")
            selection = sessions.request_status("select")
            checks["explicit_selection"] = (selection["operation"] == "select" and selection["revision"] == current["current"] and
                current["current"]["selected_asset"]["asset_id"] == output["asset_id"] and current["current"]["revision"] == 3)
            exported = ExportService(store).show("export")
            checks["exact_export"] = (Path(exported["path"]).read_bytes() == raw and exported["selected_asset"]["asset_id"] == output["asset_id"] and
                                      exported["revision"]["revision"] == 3)
            checks["zero_model_receipts"] = all(result["audio_model_calls"] == 0 for result in (saved, executed, quality, selection, exported))
            evidence = {"output_asset_id": output["asset_id"], "output_frames": pcm.frames,
                        "output_pcm_sha256": hashlib.sha256(pcm.payload).hexdigest(), "export_wav_sha256": hashlib.sha256(raw).hexdigest()}
    except (AudioError, OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        failure = str(exc)
    grade = grade_checks(case["oracle"]["outcome"], checks)
    if failure:
        grade["completed"] = False
    if trusted_run is not None and (trusted_run["failure"] or any(value is not True for value in trusted_run["observations"].values())):
        grade["completed"] = False
        failure = trusted_run["failure"] or "Scripted process observation failed"
    events = None if trusted_run is None else trusted_run["events"]
    return {"case_id": case["id"], "expected_outcome": case["oracle"]["outcome"], **grade, "failure": failure, "evidence": evidence,
            "process_observations": None if trusted_run is None else trusted_run["observations"],
            "setup_seconds": state["setup_seconds"], "task_elapsed_seconds": None if trusted_run is None else trusted_run["elapsed_seconds"],
            "cli_calls": None if events is None else len(events),
            "task_cli_calls": None if events is None else sum(item["phase"] == "task" for item in events),
            "verification_cli_calls": None if events is None else sum(item["phase"] == "verification" for item in events),
            "cli_elapsed_seconds": None if events is None else sum(item["elapsed_seconds"] for item in events)}


def _prepared_cases(output_dir):
    root = Path(output_dir).resolve()
    manifest = json.loads((root / "prepared.json").read_text(encoding="utf-8"))
    if manifest["schema"] != "matter-editing-eval-prepared/v1" or manifest["fixture"] != case_catalog()["fixture"]:
        raise ValueError("Unsupported prepared evaluation")
    cases = [case for case in case_catalog()["cases"] if case["id"] in manifest["case_ids"]]
    if not cases or len(cases) != len(manifest["case_ids"]):
        raise ValueError("Invalid prepared case selection")
    return root, cases


def grade_existing(output_dir, trusted_runs=None):
    """Return a report without writing anything, including the report itself."""
    root, cases = _prepared_cases(output_dir)
    started = time.perf_counter()
    results = [grade_case(root / case["id"], case, None if trusted_runs is None else trusted_runs[case["id"]]) for case in cases]
    completed = sum(item["completed"] is True for item in results)
    failed = sum(item["completed"] is False for item in results)
    unknown = len(results) - completed - failed
    return {"schema": REPORT_SCHEMA, "driver": "external_artifacts" if trusted_runs is None else "scripted_baseline",
        "fixture": case_catalog()["fixture"], "status": "failed" if failed else "unverified" if unknown else "passed",
        "tasks": len(results), "completed_tasks": completed, "failed_tasks": failed, "unverified_tasks": unknown,
        "verified_task_completion_rate": completed / len(results),
        "task_completion_rate": None if unknown else completed / len(results),
        "cli_calls": None if trusted_runs is None else sum(item["cli_calls"] for item in results),
        "task_cli_calls": None if trusted_runs is None else sum(item["task_cli_calls"] for item in results),
        "verification_cli_calls": None if trusted_runs is None else sum(item["verification_cli_calls"] for item in results),
        "task_elapsed_seconds": None if trusted_runs is None else sum(item["task_elapsed_seconds"] for item in results),
        "grading_elapsed_seconds": time.perf_counter() - started, "results": results,
        "evidence_scope": {"llm_calls": None if trusted_runs is None else 0,
            "actual_audio_model_calls": None if trusted_runs is None else 0,
            "actual_http_requests": None if trusted_runs is None else 0,
            "transcription_setup": "in-process stub through real publication service; receipt remote counters are simulated",
            "task_processes": "not_observed" if trusted_runs is None else "fresh CLI processes",
            "human_listening": "not_performed"},
        "limitations": ["Fixed engineering tasks do not establish general LLM-agent ability or listening improvement.",
            "External run counts, durations, inspect/show/replay calls and transient rejection errors are unknown without independently captured traces.",
            "Unchanged rejection workspaces alone cannot prove a correct attempt; such tasks remain unverified for external runs.",
            "The grader reads saved results and never performs missing tasks or accepts submitted completion flags.",
            "CLI timings include process startup and filesystem costs, not model latency."]}


def evaluate(output_dir, case_ids=None):
    prepare(output_dir, case_ids)
    root, cases = _prepared_cases(output_dir)
    runs = {case["id"]: _scripted_task(root / case["id"], case) for case in cases}
    report = grade_existing(root, runs)
    (root / "report.json").write_bytes(canonical(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--case", action="append", dest="case_ids", help="Repeat to select task IDs when preparing")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--list-cases", action="store_true")
    modes.add_argument("--prepare-only", action="store_true")
    modes.add_argument("--grade-existing", action="store_true")
    args = parser.parse_args(argv)
    if args.list_cases:
        print(json.dumps(case_catalog(), indent=2))
        return 0
    if args.output_dir is None:
        parser.error("--output-dir is required")
    if args.grade_existing and args.case_ids:
        parser.error("--case applies only when preparing a new evaluation")
    report = (grade_existing(args.output_dir) if args.grade_existing else
              prepare(args.output_dir, args.case_ids) if args.prepare_only else evaluate(args.output_dir, args.case_ids))
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in ("passed", "prepared") else 1


if __name__ == "__main__":
    raise SystemExit(main())
