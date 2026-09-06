"""Four candidates, exact prefix protection, two tail edits, restore and branch."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import uuid
import wave
from pathlib import Path


def audio(path):
    with wave.open(str(path), "rb") as stream:
        assert stream.getsampwidth() == 2 and stream.getcomptype() == "NONE"
        return stream.getframerate(), stream.getnchannels(), stream.readframes(stream.getnframes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="New directory for requests and verification")
    source_options = parser.add_mutually_exclusive_group()
    source_options.add_argument("--input", type=Path, help="Existing PCM16 WAV; otherwise create a fixture")
    source_options.add_argument("--asset-id", help="Use an already imported/decoded asset in --workspace")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--protect-seconds", type=float, default=0.25)
    parser.add_argument("--trim-seconds", type=float, default=0.04)
    parser.add_argument("--fade-seconds", type=float, default=0.08)
    parser.add_argument("--launcher", type=Path, help="Optional installed Codex Skill run_audio.py")
    parser.add_argument("--product", choices=("core", "score", "sonic"), default="core")
    args = parser.parse_args()
    if args.asset_id and not args.workspace:
        parser.error("--asset-id requires --workspace")
    if args.product != "core" and not args.launcher:
        parser.error("Product routes require --launcher")
    if min(args.protect_seconds, args.trim_seconds, args.fade_seconds) <= 0:
        parser.error("Protection, trim and fade durations must be positive")
    out = (args.out or Path(".local/regions-demo") / uuid.uuid4().hex).absolute()
    out.mkdir(parents=True, exist_ok=False)
    workspace = args.workspace.absolute() if args.workspace else out / "workspace"
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    environment.pop("PYTHONPATH", None)
    calls = 0

    def invoke(command, exit_code=0):
        nonlocal calls
        prefix = ([sys.executable, str(args.launcher.absolute()), "--product", args.product,
                   "--workspace", str(workspace), "--"] if args.launcher else
                  [sys.executable, "-m", "matter_audio_core", "--workspace", str(workspace)])
        done = subprocess.run([*prefix, *map(str, command)], env=environment, capture_output=True,
                              text=True, encoding="utf-8", timeout=120)
        calls += 1
        if done.returncode != exit_code:
            raise RuntimeError(f"Unexpected exit {done.returncode}: {done.stdout} {done.stderr}")
        value = json.loads(done.stdout)
        (out / f"response-{calls:02d}.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
        return value

    def mutate(command, verb, body, exit_code=0):
        path = out / (body["request_id"] + ".json")
        path.write_text(json.dumps(body), encoding="utf-8")
        return invoke([command, verb, "--request", path], exit_code)

    asset_id = args.asset_id
    source = args.input.absolute() if args.input else None
    before = hashlib.sha256(source.read_bytes()).hexdigest() if source else None
    if not asset_id:
        if source is None:
            source = out / "source.wav"
            with wave.open(str(source), "wb") as stream:
                stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                stream.writeframes(struct.pack("<16000h", *[round(5000 * math.sin(i * math.pi / 40)) for i in range(16000)]))
            before = hashlib.sha256(source.read_bytes()).hexdigest()
        asset_id = invoke(["assets", "import", source, "--request-id", "import"])["outputs"][0]["asset_id"]
    mutate("session", "create", {"schema": "matter-session-create/v1", "request_id": "create-session",
        "session_id": "protected-demo", "name": "Protected tail editing", "asset_id": asset_id})
    original = invoke(["session", "show", "protected-demo"])
    original_path = Path(original["playback"][0]["path"])
    original_digest = hashlib.sha256(original_path.read_bytes()).hexdigest()
    rate, channels, original_pcm = audio(original_path)
    frames = len(original_pcm) // (channels * 2)
    # Four tail-envelope durations create comparable differences without changing level globally.
    durations = [args.fade_seconds * multiplier for multiplier in (0, 1, 2, 3)]
    if (args.protect_seconds + args.trim_seconds + max(durations)) * rate >= frames:
        raise ValueError("Input needs a longer unprotected tail for this example")
    mutate("batch", "submit", {"schema": "matter-batch-submit/v1", "request_id": "submit-four",
        "batch_id": "four", "session_id": "protected-demo", "items": [
            {"job_id": name, "action": {"operation": "fade/v1", "inputs": [asset_id],
                                        "parameters": {"fade_out_seconds": duration}}}
            for name, duration in zip("ABCD", durations)]})
    assert invoke(["batch", "run", "four"])["counts"] == {"succeeded": 4}
    candidates = {name: invoke(["job", "show", name]) for name in "ABCD"}
    paths = {name: candidate["playback"][0]["path"] for name, candidate in candidates.items()}
    payloads = {name: audio(path)[2] for name, path in paths.items()}
    assert len({hashlib.sha256(payload).hexdigest() for payload in payloads.values()}) == 4, "Candidate PCM must differ"
    selected = candidates["B"]["result"]["outputs"][0]["asset_id"]
    mutate("session", "select", {"schema": "matter-session-select/v1", "request_id": "select-B",
        "session_id": "protected-demo", "expected_revision": 1, "asset_id": selected})
    locked = mutate("constraints", "set", {"schema": "matter-constraints-set/v1", "request_id": "protect-prefix",
        "session_id": "protected-demo", "expected_revision": 2,
        "regions": [{"start_seconds": 0, "end_seconds": args.protect_seconds}]})
    policy = locked["revision"]["constraints"]
    prefix_bytes = policy["regions"][0]["end_frame"] * channels * 2
    assert payloads["B"][:prefix_bytes] == original_pcm[:prefix_bytes]
    rejected = mutate("job", "submit", {"schema": "matter-job-submit/v1", "request_id": "reject-prefix-write",
        "job_id": "bad", "session_id": "protected-demo",
        "action": {"operation": "fade/v1", "inputs": [selected], "parameters": {"fade_in_frames": 3}}}, 2)
    assert rejected["error"]["code"] == "constraint_violation"
    results = {}
    for job_id, operation, parameters, revision in (
        ("B2", "trim/v1", {"start_seconds": 0, "end_seconds": frames / rate - args.trim_seconds}, 3),
        ("B3", "fade/v1", {"fade_out_seconds": args.fade_seconds * 2}, 4),
    ):
        mutate("job", "submit", {"schema": "matter-job-submit/v1", "request_id": "submit-" + job_id,
            "job_id": job_id, "session_id": "protected-demo", "selection": {"expected_revision": revision, "output_index": 0},
            "action": {"operation": operation, "inputs": [selected], "parameters": parameters}})
        result = invoke(["job", "run", job_id])
        assert result["status"] == "succeeded" and result["job"]["selection"]["status"] == "selected"
        selected = result["result"]["outputs"][0]["asset_id"]
        path = result["playback"][0]["path"]
        payload = audio(path)[2]
        assert payload[:prefix_bytes] == payloads["B"][:prefix_bytes]
        assert result["result"]["findings"][0]["protection"]["status"] == "verified"
        results[job_id] = {"path": path, "asset_id": selected, "pcm_sha256": hashlib.sha256(payload).hexdigest()}
    assert audio(results["B3"]["path"])[2] != audio(results["B2"]["path"])[2]
    mutate("session", "select", {"schema": "matter-session-select/v1", "request_id": "restore-B2",
        "session_id": "protected-demo", "expected_revision": 5, "from_revision": 4})
    mutate("session", "branch", {"schema": "matter-session-branch/v1", "request_id": "branch-B3",
        "session_id": "final-branch", "name": "Final edit branch", "from_session": "protected-demo", "from_revision": 5})
    for session, expected in (("protected-demo", results["B2"]), ("final-branch", results["B3"])):
        context = invoke(["context", "show", session])
        assert context["current"]["selected_asset"]["asset_id"] == expected["asset_id"]
        assert context["constraints"]["policy"] == policy
        assert context["constraints"]["mapped_regions"][0]["end_frame"] * channels * 2 == prefix_bytes
    assert hashlib.sha256(original_path.read_bytes()).hexdigest() == original_digest
    assert source is None or hashlib.sha256(source.read_bytes()).hexdigest() == before
    report = {"status": "passed", "cli_processes": calls, "product": args.product,
        "audio_model_calls": 0, "source_unchanged": True, "four_distinct_candidate_pcm": True,
        "two_edits_preserve_exact_prefix": True, "conflict_rejected": True, "restore_and_branch_keep_policy": True,
        "protected_frames": prefix_bytes // (channels * 2), "sample_rate": rate,
        "protected_seconds": prefix_bytes / (channels * 2 * rate), "constraints": policy,
        "original_path": str(original_path), "candidates": paths, "edits": results,
        "workspace": str(workspace), "listening_acceptance": "not_evaluated"}
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
