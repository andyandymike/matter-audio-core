"""Four local candidates: one failed write, one process crash, explicit recovery and retry."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import uuid
import wave
from pathlib import Path


def fault_worker(workspace, job_id, fault):
    # Fault injection lives in this example only, never in the production CLI.
    from matter_audio_core.artifacts import ArtifactStore
    from matter_audio_core.jobs import JobService
    store = ArtifactStore(workspace)
    jobs = JobService(store)
    if fault == "after-publication":
        jobs._finish = lambda *args: os._exit(17)
    else:
        publish = store._publish

        def fail_write(target, files):
            if target.parent == store.root / "objects":
                raise OSError("Example-only simulated transient output-write failure")
            publish(target, files)

        store._publish = fail_write
    result = jobs.run(job_id)
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="New output directory")
    parser.add_argument("--input", type=Path, help="Existing PCM16 WAV; otherwise synthesize a fixture")
    parser.add_argument("--fault-worker", nargs=3, metavar=("WORKSPACE", "JOB", "FAULT"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.fault_worker:
        fault_worker(*args.fault_worker)
        return
    out = (args.out or Path(".local/jobs-demo") / uuid.uuid4().hex).absolute()
    out.mkdir(parents=True, exist_ok=False)
    source = args.input.absolute() if args.input else out / "source.wav"
    if not args.input:
        with wave.open(str(source), "wb") as stream:
            stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            stream.writeframes(struct.pack("<16000h", *[10000 if i % 80 < 40 else -10000 for i in range(16000)]))
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    workspace = out / "workspace"
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    environment.pop("PYTHONPATH", None)
    calls = 0

    def invoke(command, *, fault=False, exit_code=0):
        nonlocal calls
        prefix = [sys.executable, str(Path(__file__).absolute())] if fault else [
            sys.executable, "-m", "matter_audio_core", "--workspace", str(workspace)]
        done = subprocess.run([*prefix, *map(str, command)], env=environment, capture_output=True,
                              text=True, encoding="utf-8", timeout=120)
        calls += 1
        if done.returncode != exit_code:
            raise RuntimeError(f"Unexpected exit {done.returncode}: {done.stdout} {done.stderr}")
        value = json.loads(done.stdout) if done.stdout.strip() else {"injected_process_exit": done.returncode}
        (out / f"response-{calls:02d}.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
        return value

    def mutate(command, verb, body):
        path = out / (body["request_id"] + ".json")
        path.write_text(json.dumps(body), encoding="utf-8")
        return invoke([command, verb, "--request", path])

    imported = invoke(["assets", "import", source, "--request-id", "import"])
    asset_id = imported["outputs"][0]["asset_id"]
    mutate("session", "create", {"schema": "matter-session-create/v1", "request_id": "create-session",
                                  "session_id": "demo", "name": "Job recovery example", "asset_id": asset_id})
    mutate("batch", "submit", {"schema": "matter-batch-submit/v1", "request_id": "submit-four",
                                "batch_id": "four", "session_id": "demo", "items": [
        {"job_id": name, "action": {"operation": "gain/v1", "inputs": [asset_id], "parameters": {"db": db}}}
        for name, db in zip(("A", "B", "C", "D"), (-1, -2, -3, -4))]})
    invoke(["job", "run", "A"])
    failed = invoke(["--fault-worker", workspace, "B", "write-failure"], fault=True)
    assert failed["status"] == "failed"
    invoke(["--fault-worker", workspace, "C", "after-publication"], fault=True, exit_code=17)
    interrupted = invoke(["batch", "show", "four"])
    assert interrupted["counts"] == {"succeeded": 1, "failed": 1, "running": 1, "queued": 1}
    invoke(["batch", "recover", "four"])
    partial = invoke(["batch", "run", "four"], exit_code=2)
    assert partial["counts"] == {"succeeded": 3, "failed": 1}
    kept = {name: invoke(["job", "show", name])["result"] for name in ("A", "C", "D")}
    retry = {"schema": "matter-batch-retry/v1", "request_id": "retry-B", "batch_id": "four",
             "items": [{"job_id": "B", "expected_attempt": 1}]}
    receipt = mutate("batch", "retry", retry)
    complete = invoke(["batch", "run", "four"])
    assert complete["counts"] == {"succeeded": 4}
    assert mutate("batch", "retry", retry) == receipt
    results = {name: invoke(["job", "show", name]) for name in ("A", "B", "C", "D")}
    assert all(results[name]["result"] == kept[name] for name in kept)
    assert {name: result["job"]["attempt"] for name, result in results.items()} == {"A": 1, "B": 2, "C": 1, "D": 1}
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    report = {"status": "passed", "cli_processes": calls, "audio_model_calls": 0,
              "source_unchanged": True, "successful_items_not_reexecuted": True,
              "published_result_recovered_without_new_audio": True,
              "attempts": {name: result["job"]["attempt"] for name, result in results.items()},
              "faults": ["simulated transient output-write failure", "process exit after publication"],
              "listening_acceptance": "not_evaluated", "workspace": str(workspace)}
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
