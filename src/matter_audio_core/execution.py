"""Local process ownership and cooperative CPU checkpoints."""

from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from contextvars import ContextVar

from .contracts import digest
from .errors import AudioError

_CHECKPOINT = ContextVar("matter_audio_checkpoint", default=None)
_EVIDENCE = ContextVar("matter_audio_execution_evidence", default=None)


def record_execution(event):
    recorder = _EVIDENCE.get()
    if recorder is not None:
        recorder(event)


@contextmanager
def execution_records(recorder):
    token = _EVIDENCE.set(recorder)
    try:
        yield
    finally:
        _EVIDENCE.reset(token)


def summarize_execution(events):
    if not events:
        return {}
    calls = {}
    for event in events:
        if event.get("audio_model"):
            calls.setdefault(event["call_id"], {}).update(event)
    known = sum(call.get("backend_started") is True for call in calls.values())
    uncertain = sum(call.get("backend_started") is None for call in calls.values())
    return {"audio_model_calls": None if uncertain else known,
            "execution": {"schema": "matter-execution/v1", "events": events,
                          "known_audio_model_calls": known, "uncertain_launch_count": uncertain}}


def checkpoint(*, force=False):
    callback = _CHECKPOINT.get()
    if callback is not None:
        callback(force)


@contextmanager
def checkpoints(callback):
    token = _CHECKPOINT.set(callback)
    try:
        yield
    finally:
        _CHECKPOINT.reset(token)


@contextmanager
def job_lock(store, job_id):
    """Never unlink lock files: an open inode must remain the unique lock target."""
    from .artifacts import safe_path
    store._workspace()
    directory = safe_path(store.root / ".job-locks")
    directory.mkdir(exist_ok=True)
    path = safe_path(directory / (digest(job_id.encode())["hex"] + ".lock"))
    with path.open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise AudioError("job_busy", "A worker owns this job; query or request cancellation") from exc
            raise
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
