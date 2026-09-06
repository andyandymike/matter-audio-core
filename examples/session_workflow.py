"""Import A, make/select B, persist a note, reopen, restore A and branch from B."""

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


def file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="New output directory; must not already exist")
    parser.add_argument("--input", type=Path, help="Optional existing PCM16 WAV; otherwise synthesize a fixture")
    args = parser.parse_args()
    out = (args.out or Path(".local/sessions-demo") / uuid.uuid4().hex).absolute()
    out.mkdir(parents=True, exist_ok=False)
    source = args.input.absolute() if args.input else out / "source.wav"
    if args.input is None:
        with wave.open(str(source), "wb") as stream:
            stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            stream.writeframes(struct.pack("<16000h", *[2000 if i % 80 < 40 else -2000 for i in range(16000)]))
    original_hash = file_hash(source)
    workspace = out / "workspace"
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    environment.pop("PYTHONPATH", None)
    calls = 0

    def call(*arguments, expected_error=None):
        nonlocal calls
        # Each command is a fresh process: no in-memory session state survives.
        completed = subprocess.run([sys.executable, "-m", "matter_audio_core", "--workspace", str(workspace),
                                    *map(str, arguments)], env=environment, capture_output=True,
                                   text=True, encoding="utf-8", timeout=60)
        calls += 1
        value = json.loads(completed.stdout)
        if expected_error:
            if completed.returncode != 2 or value.get("error", {}).get("code") != expected_error:
                raise RuntimeError(f"Expected {expected_error}: {value}")
        elif completed.returncode:
            raise RuntimeError(f"CLI failed: {value}; {completed.stderr}")
        return value

    def mutation(command, verb, body):
        path = out / (body["request_id"] + ".json")
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        result = call(command, verb, "--request", path)
        if call(command, verb, "--request", path) != result:
            raise RuntimeError("A completed mutation was not replayed exactly")
        return result

    imported = call("assets", "import", source, "--request-id", "import-a")
    original_id = imported["outputs"][0]["asset_id"]
    mutation("session", "create", {"schema": "matter-session-create/v1", "request_id": "create-session",
                                    "session_id": "bgm-demo", "name": "BGM session example", "asset_id": original_id})
    gain_path = out / "gain-b.json"
    gain_path.write_text(json.dumps({"schema": "matter-action/v1", "request_id": "gain-b",
                                    "operation": "gain/v1", "inputs": [original_id], "parameters": {"db": -3}}))
    candidate = call("action", "execute", "--request", gain_path)
    candidate_id = candidate["outputs"][0]["asset_id"]
    selection = {"schema": "matter-session-select/v1", "request_id": "select-b",
                 "session_id": "bgm-demo", "expected_revision": 1, "asset_id": candidate_id}
    mutation("session", "select", selection)
    # This is an agent's verification note, never a fabricated user listening verdict.
    note = "自动化验证备注：会话状态已保存；尚未人工试听。"
    recorded = mutation("feedback", "add", {"schema": "matter-feedback/v1", "request_id": "record-note",
                                             "session_id": "bgm-demo", "revision": 2, "text": note,
                                             "source": "agent", "listening_context": "Automated CLI workflow check"})
    reopened = call("context", "show", "bgm-demo")
    if (reopened["current"]["selected_asset"]["asset_id"] != candidate_id
            or reopened["feedback"][0]["text"] != note
            or reopened["feedback"][0]["kind"] != "agent_hypothesis"):
        raise RuntimeError("Selection or attributed feedback did not survive process restart")
    if call("session", "request", "record-note") != recorded:
        raise RuntimeError("The persisted feedback receipt differs")
    stale_path = out / "stale-selection.json"
    stale_path.write_text(json.dumps({**selection, "request_id": "stale-a", "asset_id": original_id}))
    call("session", "select", "--request", stale_path, expected_error="revision_conflict")
    mutation("session", "select", {"schema": "matter-session-select/v1", "request_id": "restore-a",
                                    "session_id": "bgm-demo", "expected_revision": 2, "from_revision": 1})
    mutation("session", "branch", {"schema": "matter-session-branch/v1", "request_id": "branch-b",
                                    "session_id": "bgm-alternative", "name": "Continue from B",
                                    "from_session": "bgm-demo", "from_revision": 2})
    restored = call("session", "show", "bgm-demo")
    branch = call("context", "show", "bgm-alternative")
    if (restored["current"]["selected_asset"]["asset_id"] != original_id
            or len(restored["history"]) != 3
            or branch["current"]["selected_asset"]["asset_id"] != candidate_id
            or branch["origin_feedback"]["feedback"][0]["feedback_id"] != recorded["feedback"]["feedback_id"]):
        raise RuntimeError("Restore or branch lost history/feedback provenance")
    if file_hash(source) != original_hash or file_hash(Path(restored["playback"][0]["path"])) != original_hash:
        raise RuntimeError("The original audio changed")
    print(json.dumps({"status": "succeeded", "workspace": str(workspace), "cli_processes": calls,
                      "reopened_selection_and_feedback_verified": True, "completed_retries_verified": True,
                      "stale_selection_rejected": True, "restored_original_bytes_verified": True,
                      "branch_origin_verified": True, "revision_count": 3, "audio_model_calls": 0,
                      "feedback_source": "agent", "human_listening": "not_performed",
                      "current_playback": restored["playback"], "alternative_playback": branch["playback"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
