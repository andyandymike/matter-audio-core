"""Independent initialization contender and interruption fixture for tests only."""

import json
import os
import sys
import time
from pathlib import Path

from matter_audio_core import artifacts
from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.errors import AudioError


def main():
    workspace, source, signals, identity, product, mode = sys.argv[1:]
    signals = Path(signals)
    store = ArtifactStore(workspace, product=product)
    (signals / (identity + ".ready")).touch()
    deadline = time.monotonic() + 30
    while not (signals / "go").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("Initialization barrier timed out")
        time.sleep(.01)
    if mode in ("interrupt", "slow"):
        original = artifacts._write

        def write_marker(path, data):
            if path.name == "workspace.json":
                with path.open("xb") as stream:
                    middle = len(data) // 2
                    stream.write(data[:middle])
                    stream.flush()
                    if mode == "interrupt":
                        os._exit(41)
                    time.sleep(.2)
                    stream.write(data[middle:])
                    stream.flush()
                    os.fsync(stream.fileno())
            else:
                original(path, data)

        artifacts._write = write_marker
    try:
        result = store.import_wav(Path(source), identity)
        print(json.dumps({"status": result["status"], "asset_id": result["outputs"][0]["asset_id"]}))
    except AudioError as exc:
        print(json.dumps({"error": exc.document()}))


if __name__ == "__main__":
    main()
