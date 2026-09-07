"""Private stdlib launch gate. The supervisor owns its entire process tree."""

import json
import os
import signal
import subprocess
import sys
import threading


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    # No backend may start until the parent has assigned Windows job ownership.
    emit({"phase": "gate_ready", "pid": os.getpid()})
    line = sys.stdin.buffer.readline(1024 * 1024 + 1)
    if not line or len(line) > 1024 * 1024:
        return 2
    spec = json.loads(line)
    if os.name != "nt":
        def parent_watch():
            sys.stdin.buffer.read()
            os.killpg(os.getpgrp(), signal.SIGKILL)
        threading.Thread(target=parent_watch, daemon=True).start()
    try:
        with open(spec["stdout"], "wb") as out, open(spec["stderr"], "wb") as err:
            process = subprocess.Popen(spec["argv"], cwd=spec["cwd"], env=spec["env"],
                stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            emit({"phase": "started", "backend_pid": process.pid})
            code = process.wait()
        emit({"phase": "finished", "returncode": code})
        return 0
    except Exception as exc:
        emit({"phase": "launch_failed", "error": str(exc)[:1000], "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
