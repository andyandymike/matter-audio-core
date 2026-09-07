"""Immutable selected-version WAV delivery, independent of browser playback."""

from .artifacts import safe_path, stable_read
from .contracts import canonical, digest, fingerprint, object_schema, read_json, validate
from .errors import AudioError
from .session_contracts import IDENTIFIER, REVISION
from .sessions import SessionService

EXPORT_SCHEMA = object_schema({"schema": {"const": "matter-export/v1"}, "request_id": IDENTIFIER,
                               "session_id": IDENTIFIER, "expected_revision": REVISION})


class ExportService:
    def __init__(self, store):
        self.store = store
        self.sessions = SessionService(store)

    def _directory(self, request_id):
        validate(request_id, IDENTIFIER)
        return safe_path(self.store.root / "exports" / digest(request_id.encode())["hex"])

    def show(self, request_id):
        self.store._workspace()
        directory = self._directory(request_id)
        if not directory.exists():
            raise AudioError("export_not_found", "No complete export for this request")
        receipt = read_json(directory / "receipt.json")
        if (receipt.get("schema") != "matter-export-result/v1" or receipt.get("request_id") != request_id
                or fingerprint(receipt)["hex"] != (directory / "receipt.sha256").read_text("ascii")
                or {path.name for path in directory.iterdir()} != {"selected.wav", "receipt.json", "receipt.sha256"}):
            raise AudioError("integrity_error", "Invalid export receipt or inventory")
        data = stable_read(directory / "selected.wav")
        if digest(data) != receipt["selected_asset"]["digest"]:
            raise AudioError("integrity_error", "Exported WAV differs from the selected asset")
        return {**receipt, "path": str(directory / "selected.wav")}

    def create(self, request):
        validate(request, EXPORT_SCHEMA)
        directory = self._directory(request["request_id"])
        if directory.exists():
            previous = self.show(request["request_id"])
            if previous["request"] != request:
                raise AudioError("request_conflict", "Export request ID binds a different selection")
            return previous
        # Capture one exact revision. A later selection does not rename this export.
        with self.sessions.database.transaction() as connection:
            session = self.sessions._session(connection, request["session_id"])
            if session["head_revision"] != request["expected_revision"]:
                raise AudioError("revision_conflict", "Read the current selection before exporting")
            from .sessions import revision_record
            revision = revision_record(self.sessions._revision(connection, request["session_id"], session["head_revision"]))
        asset = revision["selected_asset"]
        if asset is None:
            raise AudioError("selection_required", "Select audio before exporting")
        self.sessions._verify_asset(asset)
        from .regions import project_constraints
        project_constraints(self.store, revision["constraints"], asset["asset_id"])
        _, data = self.store.asset(asset["asset_id"])
        receipt = {"schema": "matter-export-result/v1", "status": "succeeded", "request": request,
                   "request_id": request["request_id"], "selected_asset": asset,
                   "revision": revision, "audio_model_calls": 0, "byte_count": len(data)}
        safe_path(directory.parent).mkdir(exist_ok=True)
        try:
            self.store._publish(directory, {"selected.wav": data, "receipt.json": canonical(receipt),
                "receipt.sha256": fingerprint(receipt)["hex"].encode("ascii")})
        except FileExistsError:
            pass
        result = self.show(request["request_id"])
        if result["request"] != request:
            raise AudioError("request_conflict", "Concurrent export used different parameters")
        return result
