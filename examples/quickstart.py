"""Exercise the installed CLI with a generated, low-level PCM16 test signal."""

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="New output directory; must not already exist")
    args = parser.parse_args()
    out = (args.out or Path(".local/demo") / uuid.uuid4().hex).absolute()
    out.mkdir(parents=True, exist_ok=False)
    source = out / "source.wav"
    samples = [2000 if (index // 40) % 2 else -2000 for index in range(8000)]
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(struct.pack("<8000h", *samples))
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    workspace = out / "workspace"
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    environment.pop("PYTHONPATH", None)

    def call(*arguments: str) -> dict:
        completed = subprocess.run(
            [sys.executable, "-m", "matter_audio_core", "--workspace", str(workspace), *arguments],
            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        if completed.returncode:
            raise RuntimeError(f"CLI failed: {completed.stdout}\n{completed.stderr}")
        return json.loads(completed.stdout)

    def action(name: str, operation: str, asset_id: str, parameters: dict) -> dict:
        request = out / f"{name}.json"
        request.write_text(json.dumps({
            "schema": "matter-action/v1", "request_id": name, "operation": operation,
            "inputs": [asset_id], "parameters": parameters,
        }), encoding="utf-8")
        resolution = call("action", "resolve", "--request", str(request))
        result = call("action", "execute", "--request", str(request),
                      "--expected-resolution-digest", resolution["digest"]["hex"])
        if call("action", "execute", "--request", str(request)) != result:
            raise RuntimeError("A repeated request did not return its original result")
        if call("action", "show", name) != result:
            raise RuntimeError("The stored result differs from the execution response")
        if result["status"] != "succeeded" or result["audio_model_calls"] != 0:
            raise RuntimeError("Unexpected action status or model usage")
        return result

    imported = call("assets", "import", str(source), "--request-id", "import-demo")
    input_id = imported["outputs"][0]["asset_id"]
    quieter = action("quieter", "gain/v1", input_id, {"db": -3})
    quieter_id = quieter["outputs"][0]["asset_id"]
    before = call("inspect", input_id)["findings"][0]["levels"]["rms_dbfs"]
    after = call("inspect", quieter_id)["findings"][0]["levels"]["rms_dbfs"]
    if abs((after - before) + 3) >= 0.01:
        raise RuntimeError("Measured gain is outside the PCM16 quantization tolerance")
    cropped = action("cropped", "trim/v1", quieter_id,
                     {"start_seconds": 0.25, "end_seconds": 0.75})
    if cropped["outputs"][0]["media"]["frame_count"] != 4000:
        raise RuntimeError("Trim produced the wrong number of frames")
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError("The source file changed")
    print(json.dumps({
        "status": "succeeded", "workspace": str(workspace), "source": str(source),
        "gain_db_measured": after - before, "trimmed_frames": 4000,
        "completed_retries_verified": True, "source_unchanged": True,
        "audio_model_calls": 0, "gain_playback": quieter["playback"],
        "trim_playback": cropped["playback"], "listening_acceptance": "not_evaluated",
    }, indent=2))


if __name__ == "__main__":
    main()
