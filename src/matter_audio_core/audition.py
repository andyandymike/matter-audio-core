"""Persistent comparison sets over immutable assets and the shared session state."""

from .contracts import ASSET_PATTERN, canonical, object_schema, validate
from .errors import AudioError
from .media import decode_wav, inspect
from .regions import project_constraints
from .session_contracts import IDENTIFIER, NAME, REVISION, page_parameters
from .sessions import SessionService, document, timestamp

ASSET = {"type": "string", "pattern": ASSET_PATTERN}
AUDITION_SCHEMA = object_schema({
    "schema": {"const": "matter-audition-create/v1"}, "request_id": IDENTIFIER,
    "audition_id": IDENTIFIER, "session_id": IDENTIFIER, "expected_revision": REVISION,
    "name": NAME, "reference_asset_id": ASSET,
    "candidates": {"type": "array", "minItems": 1, "maxItems": 16,
                   "items": object_schema({"label": NAME, "asset_id": ASSET})},
})


def session_auditions(connection, session_id, *, offset=0, limit=20):
    rows = connection.execute("SELECT audition_id, spec_json, created_at FROM auditions WHERE session_id = ? ORDER BY created_at DESC, audition_id LIMIT ? OFFSET ?",
                              (session_id, limit + 1, offset)).fetchall()
    return {"availability": "available", "items": [{"audition_id": row["audition_id"],
        "name": document(row["spec_json"])["request"]["name"], "created_at": row["created_at"]} for row in rows[:limit]],
        "next_offset": offset + limit if len(rows) > limit else None}


class AuditionService:
    def __init__(self, store, registry=None):
        self.store = store
        self.sessions = SessionService(store, registry)

    def list(self, session_id, *, offset=0, limit=20):
        validate(session_id, IDENTIFIER)
        page_parameters(offset, limit)
        with self.sessions.database.transaction() as connection:
            self.sessions._session(connection, session_id)
            return session_auditions(connection, session_id, offset=offset, limit=limit)

    def create(self, request):
        validate(request, AUDITION_SCHEMA)
        binding = canonical({"operation": "audition.create", "request": request}).decode()
        with self.sessions.database.transaction(write=True) as connection:
            previous = connection.execute("SELECT * FROM mutations WHERE request_id = ?", (request["request_id"],)).fetchone()
            if previous is not None:
                if previous["binding_json"] != binding:
                    raise AudioError("request_conflict", "Mutation request ID binds different parameters")
                return document(previous["response_json"])
            session = self.sessions._session(connection, request["session_id"])
            if session["head_revision"] != request["expected_revision"]:
                raise AudioError("revision_conflict", "Read context before preparing a comparison")
            if connection.execute("SELECT 1 FROM auditions WHERE audition_id = ?", (request["audition_id"],)).fetchone():
                raise AudioError("audition_exists", "Comparison ID already exists")
            ids = [item["asset_id"] for item in request["candidates"]]
            if len(ids) != len(set(ids)):
                raise AudioError("invalid_request", "Each candidate must have a distinct asset ID")
            assets = {asset_id: self.sessions._asset(asset_id) for asset_id in {request["reference_asset_id"], *ids}}
            value = {"schema": "matter-audition/v1", "status": "succeeded", "request": request,
                     "assets": assets, "created_at": timestamp(), "audio_model_calls": 0}
            encoded = canonical(value).decode()
            connection.execute("INSERT INTO auditions VALUES (?, ?, ?, ?)",
                               (request["audition_id"], request["session_id"], encoded, value["created_at"]))
            connection.execute("INSERT INTO mutations VALUES (?, ?, ?, ?)",
                               (request["request_id"], binding, encoded, value["created_at"]))
        return value

    def show(self, audition_id):
        validate(audition_id, IDENTIFIER)
        with self.sessions.database.transaction() as connection:
            row = connection.execute("SELECT spec_json FROM auditions WHERE audition_id = ?", (audition_id,)).fetchone()
            if row is None:
                raise AudioError("audition_not_found", "Unknown comparison")
            value = document(row[0])
        for snapshot in value["assets"].values():
            self.sessions._verify_asset(snapshot)
        return value

    def state(self, audition_id):
        audition = self.show(audition_id)
        session_id = audition["request"]["session_id"]
        session = self.sessions.show(session_id, limit=30)
        items = [{"label": "原版", "asset_id": audition["request"]["reference_asset_id"]}, *audition["request"]["candidates"]]
        current = session["current"]["selected_asset"]
        if current and current["asset_id"] not in {item["asset_id"] for item in items}:
            items.append({"label": "已保存版本", "asset_id": current["asset_id"]})
        candidates, seen = [], set()
        for item in items:
            if item["asset_id"] in seen:
                continue
            seen.add(item["asset_id"])
            record, data = self.store.asset(item["asset_id"])
            pcm = decode_wav(data)
            values = pcm.samples()
            window = max(1, (pcm.frames + 599) // 600)
            peaks = []
            for start in range(0, pcm.frames, window):
                samples = values[start * pcm.channels:min(start + window, pcm.frames) * pcm.channels]
                peaks.append([min(samples) / 32768, max(samples) / 32768])
            regions, conflict = [], None
            try:
                regions = project_constraints(self.store, session["current"]["constraints"], item["asset_id"])
            except AudioError as exc:
                conflict = exc.code
            levels = inspect(pcm, window_frames=pcm.frames, limit=1)["levels"]
            candidates.append({**item, "media": record["media"], "digest": record["digest"],
                "waveform": peaks, "levels": levels, "protected_regions": regions, "selection_conflict": conflict})
        return {"audition": audition, "session": session, "candidates": candidates,
                "feedback": self.sessions.list_feedback(session_id, limit=20)["feedback"]}
