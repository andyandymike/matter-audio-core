"""Small immutable workspace documents and complete directory deliveries."""

from .artifacts import safe_path, stable_read
from .contracts import MAX_JSON_BYTES, canonical, digest, fingerprint, read_json, validate
from .errors import AudioError
from .session_contracts import IDENTIFIER, page_parameters


class Documents:
    def __init__(self, store, namespace):
        self.store, self.namespace = store, namespace

    def directory(self, identifier):
        validate(identifier, IDENTIFIER)
        return safe_path(self.store.root / self.namespace / digest(identifier.encode())["hex"])

    def show(self, identifier):
        self.store._workspace()
        directory = self.directory(identifier)
        if not directory.exists():
            raise AudioError("document_not_found", f"Unknown {self.namespace} document")
        manifest = read_json(safe_path(directory / "manifest.json"))
        if (manifest.get("document_id") != identifier or manifest.get("namespace") != self.namespace or
                fingerprint(manifest)["hex"] != stable_read(directory / "manifest.sha256", max_bytes=64).decode("ascii")):
            raise AudioError("integrity_error", "Document identity or digest mismatch")
        inventory = manifest.get("files", {})
        if set(inventory) | {"manifest.json", "manifest.sha256"} != {path.name for path in directory.iterdir()}:
            raise AudioError("integrity_error", "Document directory inventory mismatch")
        for name, expected in inventory.items():
            if "/" in name or "\\" in name or ":" in name or name in (".", ".."):
                raise AudioError("integrity_error", "Unsafe file in document manifest")
            if digest(stable_read(directory / name)) != expected:
                raise AudioError("integrity_error", "Delivered file differs from its bound snapshot")
        return manifest

    def create(self, identifier, request, produce):
        self.store._workspace(create=True)
        directory = self.directory(identifier)
        if directory.exists():
            previous = self.show(identifier)
            if previous["request"] != request:
                raise AudioError("request_conflict", "Document ID already binds a different request")
            return previous
        body, files = produce()
        if set(files) & {"manifest.json", "manifest.sha256"}:
            raise AudioError("invalid_document", "Reserved document filenames")
        manifest = {"schema": "matter-document/v1", "namespace": self.namespace, "document_id": identifier,
            "status": "succeeded", "request": request, "body": body,
            "files": {name: digest(data) for name, data in files.items()}, "audio_model_calls": 0}
        encoded = canonical(manifest)
        if len(encoded) > MAX_JSON_BYTES:
            raise AudioError("document_limit", "Document manifest exceeds 1 MiB")
        safe_path(directory.parent).mkdir(exist_ok=True)
        try:
            self.store._publish(directory, {**files, "manifest.json": encoded,
                "manifest.sha256": fingerprint(manifest)["hex"].encode("ascii")})
        except FileExistsError:
            pass
        result = self.show(identifier)
        if result["request"] != request:
            raise AudioError("request_conflict", "Concurrent document used a different request")
        return result

    def list(self, *, offset=0, limit=20):
        self.store._workspace()
        page_parameters(offset, limit)
        folder = safe_path(self.store.root / self.namespace)
        directories = sorted(folder.iterdir()) if folder.exists() else []
        items = []
        for directory in directories[offset:offset + limit]:
            value = read_json(safe_path(directory / "manifest.json"))
            verified = self.show(value["document_id"])
            items.append({"document_id": verified["document_id"], "name": verified["request"].get("name"),
                          "digest": fingerprint(verified)})
        return {"items": items, "next_offset": offset + limit if offset + limit < len(directories) else None}


def snapshot(store, asset_id):
    record, data = store.asset(asset_id)
    return {key: record[key] for key in ("asset_id", "digest", "media")}, data


def verify_snapshot(store, expected):
    actual, data = snapshot(store, expected["asset_id"])
    if actual != expected:
        raise AudioError("integrity_error", "Source asset differs from the document snapshot")
    return data
