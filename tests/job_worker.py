"""Subprocess fault/barrier fixture; never installed as a production command."""

import os
import sys
import time
from dataclasses import replace
from pathlib import Path

from matter_audio_core.actions import Registry
from matter_audio_core.artifacts import ArtifactStore
from matter_audio_core.execution import checkpoint
from matter_audio_core.jobs import JobService


def main():
    workspace, job_id, mode, signal_dir = sys.argv[1:]
    store = ArtifactStore(workspace)
    registry = Registry()
    service = JobService(store, registry)
    signals = Path(signal_dir)
    if mode == "after_publish":
        service._finish = lambda *args: os._exit(17)
    elif mode == "after_claim":
        original = store._publish

        def publish(target, files):
            original(target, files)
            if target.parent == store.root / "requests":
                os._exit(18)

        store._publish = publish
    else:
        operation = registry.get("gain/v1")

        def held(parameters, pcm):
            (signals / "ready").write_text("entered operation", encoding="utf-8")
            deadline = time.monotonic() + 20
            while not (signals / "continue").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("Test barrier timed out")
                if mode == "cooperative":
                    checkpoint(force=True)
                time.sleep(0.01)
            return operation.execute(parameters, pcm)

        registry._operations[operation.name] = replace(operation, execute=held)
    service.run(job_id)


if __name__ == "__main__":
    main()
