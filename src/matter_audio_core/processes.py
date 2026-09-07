"""Bounded local backend processes with owned-tree cancellation and evidence."""

from __future__ import annotations

import ctypes
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from .contracts import canonical
from .errors import AudioError
from .execution import checkpoint, record_execution


class WindowsJob:
    """Unnamed, noninheritable KILL_ON_JOB_CLOSE job; no breakaway permissions."""
    def __init__(self):
        from ctypes import wintypes as w

        class BasicLimits(ctypes.Structure):
            _fields_ = [("ProcessTime", ctypes.c_longlong), ("JobTime", ctypes.c_longlong),
                ("LimitFlags", w.DWORD), ("MinWorkingSet", ctypes.c_size_t), ("MaxWorkingSet", ctypes.c_size_t),
                ("ActiveProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t), ("Priority", w.DWORD), ("Scheduling", w.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("Basic", BasicLimits), ("IOCounters", ctypes.c_ulonglong * 6),
                ("ProcessMemory", ctypes.c_size_t), ("JobMemory", ctypes.c_size_t),
                ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [("Times", ctypes.c_longlong * 4), ("PageFaults", w.DWORD),
                ("TotalProcesses", w.DWORD), ("ActiveProcesses", w.DWORD), ("TerminatedProcesses", w.DWORD)]

        self.accounting_type = Accounting
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "IsProcessInJob": ([w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL), "CloseHandle": ([w.HANDLE], w.BOOL)}
        for name, (args, result) in signatures.items():
            method = getattr(self.api, name)
            method.argtypes, method.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        self.check(self.handle)
        limits = ExtendedLimits()
        limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            self.check(self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        except BaseException:
            self.close()
            raise

    @staticmethod
    def check(value):
        if not value:
            raise AudioError("process_ownership_unavailable", str(ctypes.WinError(ctypes.get_last_error())))

    def assign(self, process):
        self.check(self.api.AssignProcessToJobObject(self.handle, int(process._handle)))

    def contains(self, process):
        from ctypes import wintypes as w
        contained = w.BOOL()
        self.check(self.api.IsProcessInJob(int(process._handle), self.handle, ctypes.byref(contained)))
        return bool(contained.value)

    def active(self):
        info = self.accounting_type()
        self.check(self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None))
        return info.ActiveProcesses

    def terminate(self):
        self.check(self.api.TerminateJobObject(self.handle, 1))

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _linux_active(group_id):
    # Ignore already dead zombies awaiting the system reaper; they consume no CPU.
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == group_id and fields[0] not in ("Z", "X"):
                    return True
            except (OSError, ValueError, IndexError):
                continue
    return False


def _stop_tree(process, job):
    if job:
        job.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise AudioError("process_termination_unconfirmed", "Backend launcher has not stopped") from exc
    deadline = time.monotonic() + 5
    while job.active() if job else _linux_active(process.pid):
        if time.monotonic() >= deadline:
            raise AudioError("process_termination_unconfirmed", "Backend descendants have not stopped")
        time.sleep(.02)


@dataclass(frozen=True)
class ProcessResult:
    stdout: str
    stderr: str
    report: dict


def _tail(path, count=65536):
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - count))
        return stream.read(count).decode("utf-8", "replace")


@contextmanager
def temporary_process_directory(*, prefix="matter-backend-", directory=None):
    """Windows may release process file handles just after process accounting ends."""
    temporary = tempfile.TemporaryDirectory(prefix=prefix, dir=directory)
    try:
        yield Path(temporary.name)
    finally:
        deadline = time.monotonic() + 3
        while True:
            try:
                temporary.cleanup()
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)


def run_process(argv, *, cwd, environment=None, timeout_seconds=600, audio_model=None, scratch=None,
                log_limit_bytes=4 * 1024 * 1024):
    """Run only a trusted registered adapter's argv, never an arbitrary UI command.

    Windows uses a job object assigned before releasing the launch gate. Linux
    uses a new process group and a parent-pipe watchdog. Detached Linux backends
    are unsupported. A cancellation is acknowledged only after tree shutdown.
    """
    if (not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x or "\0" in x for x in argv)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or log_limit_bytes < 1):
        raise AudioError("invalid_process", "Expected bounded argv, timeout and log size")
    if os.name != "nt" and not sys.platform.startswith("linux"):
        raise AudioError("process_ownership_unavailable", "Owned backend processes support Windows and Linux")
    checkpoint(force=True)
    call_id, started_at = uuid.uuid4().hex, time.monotonic()
    common = {"call_id": call_id, "audio_model": audio_model,
              "ownership": "windows-job-object/v1" if os.name == "nt" else "linux-process-group/v1"}
    def event(**value):
        record_execution({**common, **value, "elapsed_seconds": round(time.monotonic() - started_at, 6)})
    event(phase="launch_intent", backend_started=None)
    process = job = None
    released, started, stopped = False, None, False
    outcome, returncode, failure = "failed", None, None
    stdout = stderr = ""
    try:
        with temporary_process_directory(directory=scratch) as directory:
            control, diagnostics = directory / "control.jsonl", directory / "launcher.log"
            out, err = directory / "stdout.log", directory / "stderr.log"
            position, messages, gate_pid = 0, [], None

            def poll_messages():
                nonlocal position, started, returncode, gate_pid
                if control.stat().st_size > 64 * 1024:
                    raise AudioError("process_protocol_error", "Launcher control log exceeded its limit")
                with control.open("rb") as stream:
                    stream.seek(position)
                    for line in stream:
                        if not line.endswith(b"\n"):
                            break
                        position += len(line)
                        message = json.loads(line)
                        messages.append(message)
                        if message["phase"] == "gate_ready":
                            gate_pid = message["pid"]
                        elif message["phase"] == "started":
                            started = True
                            event(phase="started", backend_started=True, backend_pid=message["backend_pid"],
                                  gate_pid=gate_pid, gate_ownership_verified=True)
                        elif message["phase"] == "finished":
                            returncode = message["returncode"]
                        elif message["phase"] == "launch_failed":
                            started = False if started is None else started
                            raise AudioError("backend_launch_failed", message["error"])

            try:
                job = WindowsJob() if os.name == "nt" else None
                with control.open("xb") as control_stream, diagnostics.open("xb") as diag_stream:
                    # A Windows venv executable may be a process-spawning launcher.
                    # The stdlib-only gate uses the actual base interpreter instead.
                    gate_python = (getattr(sys, "_base_executable", None) or sys.executable) if os.name == "nt" else sys.executable
                    process = subprocess.Popen([gate_python, "-I", "-u", str(Path(__file__).with_name("_process_worker.py"))],
                        stdin=subprocess.PIPE, stdout=control_stream, stderr=diag_stream,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        start_new_session=os.name != "nt")
                if job:
                    job.assign(process)
                gate_deadline = min(started_at + timeout_seconds, time.monotonic() + 10)
                while gate_pid is None:
                    poll_messages()
                    checkpoint(force=True)
                    if process.poll() is not None or time.monotonic() >= gate_deadline:
                        raise AudioError("process_gate_failed", "Backend gate did not become ready")
                    if gate_pid is None:
                        time.sleep(.01)
                if gate_pid != process.pid or (job and not job.contains(process)):
                    raise AudioError("process_ownership_unavailable", "Actual execution gate is outside the owned process tree")
                checkpoint(force=True)
                payload = canonical({"argv": argv, "cwd": str(Path(cwd).resolve()), "env": environment,
                                     "stdout": str(out), "stderr": str(err)}) + b"\n"
                if len(payload) > 1024 * 1024:
                    raise AudioError("invalid_process", "Backend launch data exceeds 1 MiB")
                # From this point a missing acknowledgement means an uncertain launch.
                released = True
                process.stdin.write(payload)
                process.stdin.flush()
                while True:
                    poll_messages()
                    checkpoint(force=True)
                    if time.monotonic() - started_at >= timeout_seconds:
                        raise AudioError("backend_timeout", "Backend exceeded its execution deadline")
                    if any(p.exists() and p.stat().st_size > log_limit_bytes for p in (out, err, diagnostics)):
                        raise AudioError("backend_log_limit", "Backend logs exceeded their configured size limit")
                    if process.poll() is not None:
                        poll_messages()
                        break
                    time.sleep(.05)
                if not messages or returncode is None:
                    raise AudioError("process_protocol_error", "Launcher stopped without a complete backend result")
                if returncode:
                    raise AudioError("backend_failed", "Backend exited with an error", details={"returncode": returncode})
                outcome = "succeeded"
            finally:
                try:
                    if process:
                        _stop_tree(process, job)
                        stopped = True
                finally:
                    if job:
                        job.close()
                        job = None
                    if process and process.stdin:
                        with suppress(BrokenPipeError):
                            process.stdin.close()
                stdout, stderr = _tail(out), _tail(err)
    except BaseException as exc:
        if job:
            job.close()
        failure = exc
        outcome = "cancelled" if isinstance(exc, (KeyboardInterrupt, AudioError)) and (isinstance(exc, KeyboardInterrupt) or exc.code == "job_cancelled") else "failed"
    report = {"phase": "finished", "backend_started": started if released else False,
        "outcome": outcome, "returncode": returncode, "tree_stopped": stopped,
        "stderr_tail": stderr[-2000:], "error_code": failure.code if isinstance(failure, AudioError) else type(failure).__name__ if failure else None,
        "error_message": str(failure)[:1000] if failure else None}
    event(**report)
    if failure:
        raise failure
    return ProcessResult(stdout, stderr, {**common, **report, "elapsed_seconds": round(time.monotonic() - started_at, 6)})
