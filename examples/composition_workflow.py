"""Layer revision, comparison persistence and exact export via fresh CLI processes."""

import json
import subprocess
import sys
import uuid
from array import array
from pathlib import Path

from matter_audio_core.media import PCM, decode_wav, encode_wav, sample_bytes


def main():
    root = Path(".local/composition-demo") / uuid.uuid4().hex[:12]
    root.mkdir(parents=True)
    workspace = root / "workspace"

    def call(*args):
        process = subprocess.run([sys.executable, "-m", "matter_audio_core", "--workspace", str(workspace), *args, "--json"],
            capture_output=True, text=True, encoding="utf-8", check=True)
        return json.loads(process.stdout)

    def write(command, value):
        path = root / (value["request_id"] + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        return call(*command, "--request", str(path))

    assets = []
    for index, values in enumerate(([100, -100] * 12000, [1000, -1000] * 2000, [500, -500] * 2000)):
        path = root / f"fixture-{index}.wav"
        path.write_bytes(encode_wav(PCM(sample_bytes(array("h", values)), 8000, 2)))
        assets.append(call("assets", "import", str(path), "--request-id", f"import-{index}")["outputs"][0]["asset_id"])
    write(["session", "create"], {"schema": "matter-session-create/v1", "request_id": "create",
        "session_id": "layers", "name": "Layer editing - synthetic example", "asset_id": assets[0]})
    write(["constraints", "set"], {"schema": "matter-constraints-set/v1", "request_id": "protect",
        "session_id": "layers", "expected_revision": 1, "regions": [{"start_frame": 0, "end_frame": 1000}]})
    candidates = []
    for index, db in enumerate((-3, -9)):
        result = write(["action", "execute"], {"schema": "matter-action/v1", "request_id": f"mix-{index}",
            "operation": "mix/v1", "inputs": assets, "parameters": {"layers": [
                {"input_index": 1, "source_start_frame": 0, "source_end_frame": 2000, "offset_frame": 2000},
                {"input_index": 2, "source_start_frame": 0, "source_end_frame": 2000, "offset_frame": 9000, "db": db}]},
            "protection": {"session_id": "layers", "revision": 2}})
        assert result["audio_model_calls"] == 0
        assert result["findings"][0]["observed_changes"]["outside_changed_sample_count"] == 0
        candidates.append(result["outputs"][0])
        # Re-render the immutable recipe base; select against the current head.
        write(["session", "select"], {"schema": "matter-session-select/v1", "request_id": f"select-{index}",
            "session_id": "layers", "expected_revision": 2 + index, "asset_id": candidates[-1]["asset_id"]})
    first, second = [decode_wav((workspace / item["locator"]).read_bytes()) for item in candidates]
    assert first.payload[:9000 * 4] == second.payload[:9000 * 4]
    assert first.payload[11000 * 4:] == second.payload[11000 * 4:]
    assert first.payload[9000 * 4:11000 * 4] != second.payload[9000 * 4:11000 * 4]
    comparison = write(["audition", "create"], {"schema": "matter-audition-create/v1", "request_id": "compare",
        "audition_id": "layers", "session_id": "layers", "expected_revision": 4, "name": "Layer level comparison",
        "reference_asset_id": assets[0], "candidates": [{"label": label, "asset_id": item["asset_id"]}
            for label, item in zip(("Layer 2 at -3 dB", "Layer 2 at -9 dB"), candidates)]})
    assert call("audition", "show", "layers")["request"] == comparison["request"]
    exported = write(["export", "create"], {"schema": "matter-export/v1", "request_id": "export",
        "session_id": "layers", "expected_revision": 4})
    assert Path(exported["path"]).read_bytes() == (workspace / candidates[-1]["locator"]).read_bytes()
    assert call("export", "show", "export") == exported
    print(json.dumps({"status": "passed", "workspace": str(workspace.resolve()), "audio_model_calls": 0,
        "other_layer_and_prefix": "exact_pcm_match", "export": exported["path"],
        "audition_command": [sys.executable, "-m", "matter_audio_core", "--workspace", str(workspace.resolve()),
                             "audition", "serve", "layers"], "human_listening": "not_performed"}, indent=2))


if __name__ == "__main__":
    main()
