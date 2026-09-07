"""Real process-tree fixture; no model or audio device is involved."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

mode, directory = sys.argv[1], Path(sys.argv[2])
(directory / f"{mode}.json").write_text(json.dumps({"pid": os.getpid()}))
if mode in ("tree", "short"):
    subprocess.Popen([sys.executable, __file__, "branch", str(directory)])
elif mode == "branch":
    subprocess.Popen([sys.executable, __file__, "leaf", str(directory)])
if mode == "short":
    deadline = time.monotonic() + 10
    while not (directory / "leaf.json").exists() and time.monotonic() < deadline:
        time.sleep(.02)
else:
    time.sleep(120)
