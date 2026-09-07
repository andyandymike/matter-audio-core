"""Crash-test owner for the process-tree fixture."""
from pathlib import Path
import sys

from matter_audio_core.processes import run_process

run_process([sys.executable, str(Path(__file__).with_name("process_fixture.py")), "tree", sys.argv[1]],
            cwd=sys.argv[1], timeout_seconds=120)
