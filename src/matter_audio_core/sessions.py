"""Durable selection history and feedback over existing immutable audio assets."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from .actions import ActionService, Registry
from .artifacts import ArtifactStore
from .contracts import PROTECTION_REF, canonical, fingerprint, parse_json, validate
from .regions import make_constraints, project_constraints
from .errors import AudioError
from .session_contracts import IDENTIFIER, MUTATIONS, REVISION, page_parameters
from .session_db import DATABASE_VERSION, SessionDatabase


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def document(text: str):
    return parse_json(text.encode("utf-8"))


def revision_record(row: sqlite3.Row) -> dict:
    return {"session_id": row["session_id"], "revision": row["revision"],
            "parent_revision": row["parent_revision"], "selected_asset": document(row["asset_json"]),
            "reason": row["reason"], "restored_from_revision": row["restored_from_revision"],
            "constraints": document(row["constraints_json"]),
            "created_at": row["created_at"]}


def session_record(row: sqlite3.Row) -> dict:
    origin = None if row["origin_session"] is None else {
        "session_id": row["origin_session"], "revision": row["origin_revision"]}
    return {"session_id": row["session_id"], "name": row["name"],
            "head_revision": row["head_revision"], "created_at": row["created_at"], "origin": origin}


def feedback_record(row: sqlite3.Row) -> dict:
    value = dict(row)
    value.pop("sequence")
    value["kind"] = "agent_hypothesis" if row["source"] == "agent" else "user_observation"
    return value


def page(rows: list, offset: int, limit: int, convert) -> tuple[list, int | None]:
    return [convert(row) for row in rows[:limit]], offset + limit if len(rows) > limit else None


class SessionService:
    def __init__(self, store: ArtifactStore, registry: Registry | None = None):
        self.store, self.registry = store, registry or Registry()
        self.database = SessionDatabase(store)

    def migrate(self):
        with self.database.transaction(write=True):
            pass
        return {"schema": "matter-session-migration/v1", "status": "succeeded",
                "database_version": DATABASE_VERSION}

    @staticmethod
    def _session(connection, session_id):
        row = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            raise AudioError("session_not_found", f"Unknown session: {session_id}")
        return row

    @staticmethod
    def _revision(connection, session_id, revision):
        row = connection.execute("SELECT * FROM revisions WHERE session_id = ? AND revision = ?",
                                 (session_id, revision)).fetchone()
        if row is None:
            raise AudioError("revision_not_found", "Revision does not belong to this session",
                             details={"session_id": session_id, "revision": revision})
        return row

    def _asset(self, asset_id: str) -> dict:
        record, _ = self.store.asset(asset_id)
        if record["media"].get("codec") != "pcm_s16le":
            raise AudioError("unsupported_session_asset", "Select a PCM16 audio output, not a provenance file")
        return {key: record[key] for key in ("asset_id", "digest", "media")}

    def _verify_asset(self, snapshot: dict | None) -> list[dict]:
        if snapshot is None:
            return []
        record, _ = self.store.asset(snapshot["asset_id"])
        actual = {key: record[key] for key in ("asset_id", "digest", "media")}
        if actual != snapshot or actual["media"].get("codec") != "pcm_s16le":
            raise AudioError("session_asset_changed", "Stored selection no longer matches its asset")
        return self.store.playback_refs({"outputs": [record]})

    @staticmethod
    def _insert_revision(connection, session_id, revision, asset, reason, created_at, restored=None, constraints=None):
        connection.execute("""INSERT INTO revisions
            (session_id, revision, parent_revision, asset_json, reason, restored_from_revision, created_at, constraints_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
            session_id, revision, revision - 1 if revision > 1 else None,
            canonical(asset).decode(), reason, restored, created_at, canonical(constraints).decode()))
        return revision_record(SessionService._revision(connection, session_id, revision))

    def mutate(self, operation: str, request: dict) -> dict:
        if operation not in MUTATIONS:
            raise AudioError("unsupported_session_operation", operation)
        validate(request, MUTATIONS[operation])
        binding = canonical({"operation": operation, "request": request}).decode()
        with self.database.transaction(write=True, create=operation == "create") as connection:
            previous = connection.execute("SELECT * FROM mutations WHERE request_id = ?",
                                          (request["request_id"],)).fetchone()
            if previous is not None:
                if previous["binding_json"] != binding:
                    raise AudioError("request_conflict", "Session request ID already binds a different mutation")
                return document(previous["response_json"])
            created_at = timestamp()
            if operation in ("create", "branch"):
                result = self._create(connection, request, created_at, branch=operation == "branch")
            elif operation == "select":
                result = self._select(connection, request, created_at)
            elif operation == "constraints":
                result = self._set_constraints(connection, request, created_at)
            else:
                result = self._feedback(connection, request, created_at)
            response = {"schema": "matter-session-mutation/v1", "status": "succeeded",
                        "operation": operation, "request_id": request["request_id"],
                        "session_id": request["session_id"], "audio_model_calls": 0, **result}
            connection.execute("INSERT INTO mutations VALUES (?, ?, ?, ?)", (
                request["request_id"], binding, canonical(response).decode(), created_at))
        return response

    def _create(self, connection, request, created_at, *, branch):
        session_id = request["session_id"]
        if connection.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone():
            raise AudioError("session_exists", f"Session already exists: {session_id}")
        origin_session, origin_revision = None, None
        constraints = None
        if branch:
            origin_session, origin_revision = request["from_session"], request["from_revision"]
            self._session(connection, origin_session)
            source = self._revision(connection, origin_session, origin_revision)
            asset = document(source["asset_json"])
            constraints = document(source["constraints_json"])
            self._verify_asset(asset)
            project_constraints(self.store, constraints, asset["asset_id"] if asset else None)
        else:
            asset = self._asset(request["asset_id"]) if "asset_id" in request else None
        connection.execute("INSERT INTO sessions VALUES (?, ?, 1, ?, ?, ?)", (
            session_id, request["name"], created_at, origin_session, origin_revision))
        revision = self._insert_revision(connection, session_id, 1, asset,
                                         "branch" if branch else "create", created_at, constraints=constraints)
        return {"revision": revision}

    def _select(self, connection, request, created_at):
        session_id = request["session_id"]
        session = self._session(connection, session_id)
        expected = request["expected_revision"]
        if session["head_revision"] != expected:
            raise AudioError("revision_conflict", "Selection changed; read context before selecting again",
                             details={"expected_revision": expected, "current_revision": session["head_revision"]})
        if expected == REVISION["maximum"]:
            raise AudioError("revision_limit", "Session reached its revision limit; branch into a new session")
        restored = request.get("from_revision")
        if restored is not None:
            source = self._revision(connection, session_id, restored)
            asset = document(source["asset_json"])
            constraints = document(source["constraints_json"])
            self._verify_asset(asset)
        else:
            asset = self._asset(request["asset_id"])
            constraints = document(self._revision(connection, session_id, expected)["constraints_json"])
        project_constraints(self.store, constraints, asset["asset_id"] if asset else None)
        revision = self._insert_revision(connection, session_id, expected + 1, asset,
                                         "restore" if restored is not None else "select", created_at, restored, constraints)
        connection.execute("UPDATE sessions SET head_revision = ? WHERE session_id = ?",
                           (expected + 1, session_id))
        return {"revision": revision}

    def _set_constraints(self, connection, request, created_at):
        session_id, expected = request["session_id"], request["expected_revision"]
        session = self._session(connection, session_id)
        if session["head_revision"] != expected:
            raise AudioError("revision_conflict", "Read the current selection before changing PCM locks")
        if expected == REVISION["maximum"]:
            raise AudioError("revision_limit", "Session reached its revision limit")
        asset = document(self._revision(connection, session_id, expected)["asset_json"])
        self._verify_asset(asset)
        constraints = make_constraints(self.store, asset, request["regions"])
        revision = self._insert_revision(connection, session_id, expected + 1, asset, "constraints", created_at,
                                         constraints=constraints)
        connection.execute("UPDATE sessions SET head_revision = ? WHERE session_id = ?", (expected + 1, session_id))
        return {"revision": revision}

    def constraints(self, session_id, *, revision=None):
        validate(session_id, IDENTIFIER)
        if revision is not None:
            validate(revision, REVISION)
        with self.database.transaction() as connection:
            session = self._session(connection, session_id)
            number = session["head_revision"] if revision is None else revision
            row = revision_record(self._revision(connection, session_id, number))
        asset = row["selected_asset"]
        return {"schema": "matter-constraints/v1", "availability": "available", "session_id": session_id,
                "revision": number, "constraints": row["constraints"],
                "mapped_regions": project_constraints(self.store, row["constraints"], asset["asset_id"] if asset else None)}

    def protection(self, reference, input_asset_id):
        validate(reference, PROTECTION_REF)
        with self.database.transaction() as connection:
            session = self._session(connection, reference["session_id"])
            current = self._revision(connection, reference["session_id"], session["head_revision"])
            bound = self._revision(connection, reference["session_id"], reference["revision"])
            constraints = document(bound["constraints_json"])
            if fingerprint(document(current["constraints_json"])) != fingerprint(constraints):
                raise AudioError("constraint_conflict", "Constraint set changed; read current context and submit a new request")
            selected = document(bound["asset_json"])
        if constraints and constraints["regions"] and (selected is None or selected["asset_id"] != input_asset_id):
            raise AudioError("constraint_input_mismatch", "Protected action must use the selected asset at its bound revision")
        return {"reference": reference, "constraints": constraints, "constraints_digest": fingerprint(constraints),
                "input_regions": project_constraints(self.store, constraints, input_asset_id)}

    def _feedback(self, connection, request, created_at):
        session_id, number = request["session_id"], request["revision"]
        self._session(connection, session_id)
        asset = document(self._revision(connection, session_id, number)["asset_json"])
        if asset is None:
            raise AudioError("selection_required", "Feedback must refer to a revision with selected audio")
        self._verify_asset(asset)
        feedback_id = "f_" + uuid.uuid4().hex
        connection.execute("""INSERT INTO feedback
            (feedback_id, session_id, revision, source, text, listening_context, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""", (feedback_id, session_id, number, request["source"],
                                              request["text"], request.get("listening_context", ""), created_at))
        row = connection.execute("SELECT * FROM feedback WHERE feedback_id = ?", (feedback_id,)).fetchone()
        return {"feedback": {**feedback_record(row), "asset": asset}}

    def request_status(self, request_id: str) -> dict:
        validate(request_id, IDENTIFIER)
        with self.database.transaction() as connection:
            row = connection.execute("SELECT response_json FROM mutations WHERE request_id = ?",
                                     (request_id,)).fetchone()
            if row is None:
                raise AudioError("request_not_found", "No committed session mutation with that ID")
            return document(row[0])

    def list_sessions(self, *, offset: int = 0, limit: int = 50) -> dict:
        page_parameters(offset, limit)
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT * FROM sessions ORDER BY session_id LIMIT ? OFFSET ?",
                                      (limit + 1, offset)).fetchall()
            sessions, next_offset = page(rows, offset, limit, session_record)
        return {"schema": "matter-session-list/v1", "sessions": sessions, "next_offset": next_offset}

    def _show(self, connection, session_id, offset, limit):
        session = session_record(self._session(connection, session_id))
        current = revision_record(self._revision(connection, session_id, session["head_revision"]))
        rows = connection.execute("SELECT * FROM revisions WHERE session_id = ? ORDER BY revision DESC LIMIT ? OFFSET ?",
                                  (session_id, limit + 1, offset)).fetchall()
        history, next_offset = page(rows, offset, limit, revision_record)
        return {"schema": "matter-session/v1", "status": "succeeded", "session": session,
                "current": current, "history": history, "next_offset": next_offset}

    def show(self, session_id: str, *, offset: int = 0, limit: int = 50) -> dict:
        validate(session_id, IDENTIFIER)
        page_parameters(offset, limit)
        with self.database.transaction() as connection:
            result = self._show(connection, session_id, offset, limit)
        result["playback"] = self._verify_asset(result["current"]["selected_asset"])
        current = result["current"]
        result["protected_regions"] = project_constraints(self.store, current["constraints"],
            current["selected_asset"]["asset_id"] if current["selected_asset"] else None)
        return result

    def _feedback_list(self, connection, session_id, revision, offset, limit, *, asset=None):
        parameters = [session_id]
        where = "f.session_id = ?"
        if revision is not None:
            self._revision(connection, session_id, revision)
            where += " AND f.revision = ?"
            parameters.append(revision)
        if asset is not None:
            where += " AND r.asset_json = ?"
            parameters.append(canonical(asset).decode())
        rows = connection.execute(f"""SELECT f.*, r.asset_json FROM feedback f JOIN revisions r
            ON f.session_id = r.session_id AND f.revision = r.revision
            WHERE {where} ORDER BY f.sequence DESC LIMIT ? OFFSET ?""",
                                  (*parameters, limit + 1, offset)).fetchall()

        def convert(row):
            value = feedback_record(row)
            value["asset"] = document(value.pop("asset_json"))
            return value

        feedback, next_offset = page(rows, offset, limit, convert)
        return {"schema": "matter-feedback-list/v1", "session_id": session_id,
                "revision": revision, "feedback": feedback, "next_offset": next_offset}

    def list_feedback(self, session_id: str, *, revision: int | None = None,
                      offset: int = 0, limit: int = 50) -> dict:
        validate(session_id, IDENTIFIER)
        if revision is not None:
            validate(revision, REVISION)
        page_parameters(offset, limit)
        with self.database.transaction() as connection:
            self._session(connection, session_id)
            return self._feedback_list(connection, session_id, revision, offset, limit)

    def context(self, session_id: str, *, history_limit: int = 10, feedback_limit: int = 20) -> dict:
        validate(session_id, IDENTIFIER)
        page_parameters(0, history_limit)
        page_parameters(0, feedback_limit)
        # Selection and feedback are from one SQLite read snapshot. Audio is immutable.
        with self.database.transaction() as connection:
            state = self._show(connection, session_id, 0, history_limit)
            from .jobs import session_jobs
            jobs = session_jobs(connection, session_id)
            feedback = self._feedback_list(connection, session_id, None, 0, feedback_limit)
            asset = state["current"]["selected_asset"]
            relevant = {"feedback": [], "next_offset": None} if asset is None else self._feedback_list(
                connection, session_id, None, 0, feedback_limit, asset=asset)
            origin = state["session"]["origin"]
            source_feedback = None if origin is None else self._feedback_list(
                connection, origin["session_id"], origin["revision"], 0, feedback_limit)
        asset = state["current"]["selected_asset"]
        playback = self._verify_asset(asset)
        measurements = [] if asset is None else ActionService(self.store, self.registry).inspect_asset(
            asset["asset_id"], {"limit": 1})["findings"]
        return {"schema": "matter-context/v1", "status": "succeeded", "session": state["session"],
                "current": state["current"], "history": state["history"],
                "history_next_offset": state["next_offset"], "feedback": feedback["feedback"],
                "feedback_next_offset": feedback["next_offset"], "origin_feedback": source_feedback,
                "current_feedback": relevant["feedback"], "current_feedback_next_offset": relevant["next_offset"],
                "measurements": measurements, "measurement_method": "pcm16-levels/v1",
                "capabilities": self.registry.capabilities(), "playback": playback, "audio_model_calls": 0,
                "constraints": {"availability": "available", "policy": state["current"]["constraints"],
                    "mapped_regions": project_constraints(self.store, state["current"]["constraints"],
                                                          asset["asset_id"] if asset else None)},
                "jobs": jobs,
                "limitations": ["Feedback is attributed to its recorded source; it is not verified listening acceptance.",
                                "Direct actions do not select outputs; managed jobs may request guarded selection."]}
