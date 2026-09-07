"""Immutable complete groups plus exclusive request claims (M1, no database)."""

from __future__ import annotations

import ctypes
import errno
import os
import re
import shutil
import stat
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__
from .contracts import (ASSET_PATTERN, REQUEST_PATTERN, RESULT_SCHEMA, canonical,
                        digest, fingerprint, read_json, validate)
from .errors import AudioError
from .execution import execution_records, summarize_execution
from .media import MAX_AUDIO_BYTES, decode_wav


def safe_path(path: Path) -> Path:
    path = path.absolute()
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise AudioError("unsafe_path", f"Reparse points and symlinks are not supported: {part}")
    return path.resolve()


def stable_read(path: Path, *, max_bytes: int = MAX_AUDIO_BYTES) -> bytes:
    path = safe_path(path)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= max_bytes:
            raise AudioError("invalid_source", "Expected a nonempty regular file within the size limit")
        data = stream.read(max_bytes + 1)
        stream.seek(0)
        second = stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    current = path.stat()
    # On Windows, handle/path stat can report different ctime (creation/change
    # timestamps). File identity, length, mtime and two content reads are shared.
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if (identity(before) != identity(after) or before.st_ctime_ns != after.st_ctime_ns
            or identity(after) != identity(current) or data != second):
        raise AudioError("source_changed", "Source changed while taking a snapshot")
    if len(data) != before.st_size:
        raise AudioError("source_changed", "Source length changed while taking a snapshot")
    return data


def publish_directory(source: Path, target: Path) -> None:
    """Atomic, no-replace directory publication; unsupported platforms fail closed."""
    if os.name == "nt":
        os.rename(source, target)  # Windows refuses every existing destination.
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise AudioError("publication_unavailable", "renameat2 is required on Linux")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1):
            code = ctypes.get_errno()
            if code in (errno.EEXIST, errno.ENOTEMPTY):
                raise FileExistsError(code, os.strerror(code), str(target))
            raise OSError(code, os.strerror(code), str(target))
    else:
        raise AudioError("publication_unavailable", "Complete publication supports Windows and Linux")


def _write(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass
class Publication:
    group_id: str
    outputs: list = field(default_factory=list)
    files: dict = field(default_factory=dict)

    def add(self, data: bytes, media: dict, *, parents: list | None = None,
            provenance: dict | None = None, role: str = "audio") -> dict:
        index = len(self.outputs)
        if index >= 1000:
            raise AudioError("too_many_outputs", "A result supports up to 1000 outputs")
        suffix = "wav" if media.get("codec") == "pcm_s16le" else "bin"
        filename = f"output-{index:03d}.{suffix}"
        record = {"asset_id": f"a_{self.group_id}_{index}", "role": role, "digest": digest(data),
                  "byte_count": len(data), "media": media, "parents": parents or [],
                  "provenance": provenance or {}, "locator": f"objects/{self.group_id}/{filename}"}
        self.outputs.append(record)
        self.files[filename] = data
        return record


class ArtifactStore:
    def __init__(self, root: Path | str, *, product: str = "matter-audio"):
        self.root = safe_path(Path(root))
        self.product = product

    def _workspace(self, *, create: bool = False) -> None:
        safe_path(self.root)
        if not self.root.exists() and create:
            self.root.mkdir(parents=True)
        marker = self.root / "workspace.json"
        if not marker.exists():
            if not create:
                raise AudioError("workspace_missing", f"No workspace at {self.root}")
            if any(self.root.iterdir()):
                raise AudioError("workspace_unrecognized", "Refusing to initialize a nonempty directory")
            _write(marker, canonical({"schema": "matter-workspace/v1", "product": self.product}))
        safe_path(marker)
        if read_json(marker) != {"schema": "matter-workspace/v1", "product": self.product}:
            raise AudioError("workspace_mismatch", "Workspace belongs to a different product or schema")
        for name in ("objects", "requests", ".staging"):
            folder = safe_path(self.root / name)
            if create:
                folder.mkdir(exist_ok=True)

    def _publish(self, target: Path, files: dict[str, bytes]) -> None:
        staging_root = safe_path(self.root / ".staging")
        stage = staging_root / uuid.uuid4().hex
        stage.mkdir()
        try:
            for name, data in files.items():
                if Path(name).name != name or ":" in name:
                    raise AudioError("unsafe_path", "Invalid publication filename")
                _write(stage / name, data)
            publish_directory(stage, safe_path(target))
        finally:
            if stage.exists():
                # Cleanup is restricted to this exact task-created staging directory.
                if safe_path(stage).parent != staging_root:
                    raise AudioError("unsafe_path", "Staging cleanup escaped its root")
                shutil.rmtree(stage)

    def _request_path(self, request_id: str) -> Path:
        validate(request_id, {"type": "string", "pattern": REQUEST_PATTERN})
        return safe_path(self.root / "requests" / digest(request_id.encode())["hex"])

    def _load_group(self, group_id: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{32}", group_id):
            raise AudioError("invalid_asset", "Invalid asset group ID")
        directory = safe_path(self.root / "objects" / group_id)
        manifest_path = safe_path(directory / "manifest.json")
        result = read_json(manifest_path)
        if (result.get("schema") != RESULT_SCHEMA or result.get("group_id") != group_id
                or result.get("status") not in ("succeeded", "failed")):
            raise AudioError("integrity_error", "Invalid result identity")
        if fingerprint(result)["hex"] != (directory / "manifest.sha256").read_text("ascii"):
            raise AudioError("integrity_error", "Result manifest digest mismatch")
        expected = {"manifest.json", "manifest.sha256"}
        for index, output in enumerate(result["outputs"]):
            filename = Path(output["locator"]).name
            suffix = "wav" if output["media"].get("codec") == "pcm_s16le" else "bin"
            if (output["asset_id"] != f"a_{group_id}_{index}"
                    or filename != f"output-{index:03d}.{suffix}"
                    or output["locator"] != f"objects/{group_id}/{filename}"):
                raise AudioError("integrity_error", "Invalid result asset locator")
            data = stable_read(directory / filename)
            if digest(data) != output["digest"] or len(data) != output["byte_count"]:
                raise AudioError("integrity_error", "Asset content digest mismatch")
            if suffix == "wav" and decode_wav(data).facts() != output["media"]:
                raise AudioError("integrity_error", "Asset media facts mismatch")
            expected.add(filename)
        if {p.name for p in directory.iterdir()} != expected:
            raise AudioError("integrity_error", "Result file inventory mismatch")
        return result

    def show_request(self, request_id: str) -> dict:
        self._workspace()
        directory = self._request_path(request_id)
        if not directory.exists():
            raise AudioError("request_not_found", f"Unknown request: {request_id}")
        claim = read_json(safe_path(directory / "claim.json"))
        target = self.root / "objects" / claim["group_id"]
        if not target.exists():
            raise AudioError("recovery_pending", "Request is running or requires recovery; do not resubmit with a new ID",
                             details={"request_id": request_id, **self.execution_evidence(request_id)})
        result = self._load_group(claim["group_id"])
        if result["request_id"] != request_id or result["binding_digest"] != claim["binding_digest"]:
            raise AudioError("integrity_error", "Result does not match its request claim")
        return result

    def execution_evidence(self, request_id: str) -> dict:
        directory = self._request_path(request_id)
        if not directory.exists():
            return {}
        claim = read_json(directory / "claim.json")
        events = []
        for sequence, entry in enumerate(sorted(directory.glob("execution-*"))):
            safe_path(entry)
            document = read_json(entry / "event.json")
            if (entry.name != f"execution-{sequence:04d}" or document["claim"] != claim
                    or document["sequence"] != sequence
                    or fingerprint(document)["hex"] != (entry / "event.sha256").read_text("ascii")
                    or {p.name for p in entry.iterdir()} != {"event.json", "event.sha256"}):
                raise AudioError("integrity_error", "Invalid execution journal")
            events.append(document["event"])
        return summarize_execution(events)

    def transact(self, request_id: str, binding: dict, producer: Callable[[Publication], dict]) -> dict:
        self._workspace(create=True)
        target = self._request_path(request_id)
        binding_digest = fingerprint(binding)
        group_id = uuid.uuid4().hex
        claim = {"request_id": request_id, "binding_digest": binding_digest, "group_id": group_id}
        try:
            self._publish(target, {"claim.json": canonical(claim)})
        except FileExistsError:
            previous = read_json(target / "claim.json")
            if previous["binding_digest"] != binding_digest:
                raise AudioError("request_conflict", "Request ID already binds different inputs or parameters")
            return self.show_request(request_id)
        publication = Publication(group_id)
        events = []

        def record(event):
            if len(events) >= 1000:
                raise AudioError("execution_limit", "Execution journal exceeds its event limit")
            document = {"schema": "matter-execution-event/v1", "claim": claim,
                        "sequence": len(events), "event": event}
            self._publish(target / f"execution-{len(events):04d}", {
                "event.json": canonical(document), "event.sha256": fingerprint(document)["hex"].encode("ascii")})
            events.append(event)

        try:
            with execution_records(record):
                extra = producer(publication)
            status = "succeeded"
        except AudioError as exc:
            publication = Publication(group_id)
            extra, status = {"error": exc.document(), "findings": []}, "failed"
        result = {"schema": RESULT_SCHEMA, "group_id": group_id, "request_id": request_id,
                  "core_version": __version__, "product": self.product, "status": status,
                  "binding_digest": binding_digest, "binding": binding,
                  "audio_model_calls": 0, "outputs": publication.outputs, **extra, **summarize_execution(events)}
        self._publish(self.root / "objects" / group_id, {
            **publication.files, "manifest.json": canonical(result),
            "manifest.sha256": fingerprint(result)["hex"].encode("ascii"),
        })
        return self.show_request(request_id)

    def asset(self, asset_id: str) -> tuple[dict, bytes]:
        self._workspace()
        validate(asset_id, {"type": "string", "pattern": ASSET_PATTERN})
        _, group_id, index = asset_id.split("_")
        group = self._load_group(group_id)
        try:
            record = group["outputs"][int(index)]
        except IndexError as exc:
            raise AudioError("asset_not_found", asset_id) from exc
        if record["asset_id"] != asset_id:
            raise AudioError("asset_not_found", asset_id)
        data = stable_read(self.root / record["locator"])
        if digest(data) != record["digest"] or len(data) != record["byte_count"]:
            raise AudioError("integrity_error", "Asset changed after group verification")
        return record, data

    def list_assets(self, *, offset: int = 0, limit: int = 50) -> dict:
        self._workspace()
        groups = sorted((self.root / "objects").iterdir(), key=lambda p: p.name)
        records = []
        for group in groups:
            records.extend(self._load_group(group.name)["outputs"])
        return {"assets": records[offset:offset + limit],
                "next_offset": offset + limit if offset + limit < len(records) else None}

    def playback_refs(self, result: dict) -> list[dict]:
        return [{"asset_id": item["asset_id"], "path": str(self.root / item["locator"])}
                for item in result.get("outputs", []) if item["media"].get("codec") == "pcm_s16le"]

    def import_wav(self, path: Path, request_id: str) -> dict:
        data = stable_read(path)
        pcm = decode_wav(data)
        origin = {"kind": "local_import", "path": str(safe_path(path)), "rights": "unknown"}
        binding = {"operation": "import.wav/v1", "digest": digest(data), "provenance": origin}

        def produce(publication):
            publication.add(data, pcm.facts(), provenance=origin)
            return {"findings": [], "limitations": ["Import does not establish distribution rights."]}

        return self.transact(request_id, binding, produce)
