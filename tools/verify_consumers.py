"""Run both real product CLIs against existing assets, without model execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    for product in ("score", "sonic"):
        parser.add_argument(f"--{product}-root", type=Path, required=True)
        parser.add_argument(f"--{product}-python", type=Path, required=True)
    parser.add_argument("--bgm", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
    environment.pop("PYTHONPATH", None)
    sequence = 0

    def call(product, *command):
        nonlocal sequence
        prefix = [str(getattr(args, f"{product}_python")), "-m"]
        prefix += ["score_matter", "audio"] if product == "score" else ["tools.authoring"]
        prefix += ["--workspace", str(out / product / "workspace")]
        completed = subprocess.run([*prefix, *map(str, command), "--json"], cwd=getattr(args, f"{product}_root"),
                                   env=environment, capture_output=True, text=True, encoding="utf-8", timeout=120)
        value = json.loads(completed.stdout)
        if completed.returncode:
            raise RuntimeError(f"{product} {command}: {value}; stderr={completed.stderr}")
        sequence += 1
        path = out / f"{sequence:02d}-{product}.json"
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return value

    def action(product, request_id, operation, asset_id, parameters):
        request_path = out / f"request-{product}-{request_id}.json"
        request_path.write_text(json.dumps({"schema": "matter-action/v1", "request_id": request_id,
                                           "operation": operation, "inputs": [asset_id], "parameters": parameters}), encoding="utf-8")
        resolution = call(product, "action", "resolve", "--request", request_path)
        result = call(product, "action", "execute", "--request", request_path,
                      "--expected-resolution-digest", resolution["digest"]["hex"])
        assert result["audio_model_calls"] == 0 and result["status"] == "succeeded"
        assert call(product, "action", "execute", "--request", request_path) == result
        return result

    caps = {product: call(product, "capabilities") for product in ("score", "sonic")}
    assert caps["score"]["core_version"] == caps["sonic"]["core_version"]
    # Compare the installed modules, not just the reported package version.
    inventories = {}
    probe = "import json,hashlib,pathlib,matter_audio_core; p=pathlib.Path(matter_audio_core.__file__).parent; print(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.glob('*.py')},sort_keys=True))"
    for product in ("score", "sonic"):
        completed = subprocess.run([str(getattr(args, f"{product}_python")), "-c", probe],
                                   env=environment, capture_output=True, text=True, check=True)
        inventories[product] = json.loads(completed.stdout)
    assert inventories["score"] == inventories["sonic"]

    bgm_before = sha(args.bgm)
    bgm = call("score", "assets", "import", args.bgm.resolve(), "--request-id", "bgm-import")["outputs"][0]
    bgm_facts = call("score", "inspect", bgm["asset_id"], "--limit", "4")["findings"][0]
    bgm_gain = action("score", "bgm-gain", "gain/v1", bgm["asset_id"], {"db": -3})
    gain_id = bgm_gain["outputs"][0]["asset_id"]
    quieter_facts = call("score", "inspect", gain_id, "--limit", "4")["findings"][0]
    delta_db = quieter_facts["levels"]["rms_dbfs"] - bgm_facts["levels"]["rms_dbfs"]
    assert abs(delta_db + 3) < 0.01
    end = bgm["media"]["frame_count"] - bgm["media"]["sample_rate_hz"]
    assert end > 0
    bgm_trim = action("score", "bgm-trim", "trim/v1", gain_id, {"start_frame": 0, "end_frame": end})
    assert bgm_trim["outputs"][0]["media"]["frame_count"] == end
    assert sha(args.bgm) == bgm_before

    catalog = call("sonic", "catalog", "list")
    recipe_path = args.sonic_root / "tools/ui_foley/recipes/judgement-horror-paper-switch-v1.json"
    definition = json.loads(recipe_path.read_text(encoding="utf-8"))["outputs"][0]
    source_id = definition["source_asset_id"]
    recording = next(record for record in catalog["assets"] if record["source_asset_id"] == source_id)
    recording_before = sha(recording["path"])
    decoded = call("sonic", "catalog", "decode", source_id, "--request-id", "paper-decode")
    decoded_asset = next(asset for asset in decoded["outputs"] if asset["role"] == "audio")
    call("sonic", "inspect", decoded_asset["asset_id"], "--limit", "4")
    parameters = {key: definition[key] for key in ("start_frame", "frame_count", "fade_in_frames", "fade_out_frames", "gain_q15")}
    paper = action("sonic", "paper-fused", "sonic.recording_condition/v1", decoded_asset["asset_id"], parameters)
    assert paper["outputs"][0]["digest"]["hex"] == definition["output_sha256"]
    paper_gain = action("sonic", "paper-gain", "gain/v1", paper["outputs"][0]["asset_id"], {"db": -3})
    paper_trim = action("sonic", "paper-trim", "trim/v1", decoded_asset["asset_id"], {
        "start_frame": definition["start_frame"], "end_frame": definition["start_frame"] + definition["frame_count"]})
    assert paper_trim["outputs"][0]["media"]["frame_count"] == definition["frame_count"]
    assert sha(recording["path"]) == recording_before

    report = {"core_version": caps["score"]["core_version"], "installed_module_hashes": inventories["score"],
              "product_calls": sequence, "audio_model_calls": 0, "same_core_modules": True,
              "bgm_source_unchanged": True, "recording_source_unchanged": True,
              "bgm_rms_delta_db": delta_db, "legacy_paper_sha256": definition["output_sha256"],
              "bgm_gain_playback": bgm_gain["playback"], "bgm_trim_playback": bgm_trim["playback"],
              "paper_gain_playback": paper_gain["playback"], "paper_legacy_playback": paper["playback"],
              "human_listening": "not_performed", "consumer_game_validation": "not_performed"}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
