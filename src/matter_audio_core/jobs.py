"""Recoverable local jobs, immutable attempts and independently retried batches."""

from __future__ import annotations

import time
import uuid
from collections import Counter
from contextlib import ExitStack

from .actions import ActionService, Registry
from .contracts import canonical, fingerprint, validate
from .errors import AudioError
from .execution import checkpoints, job_lock
from .job_contracts import MUTATIONS
from .session_contracts import IDENTIFIER, REVISION, page_parameters
from .session_db import SessionDatabase
from .sessions import SessionService, document, timestamp

TERMINAL = {"succeeded", "failed", "interrupted", "cancelled"}
RETRYABLE = {"failed", "interrupted", "cancelled"}
JOB_ORDER = "CASE WHEN state IN ('queued', 'running', 'cancel_requested') THEN 0 ELSE 1 END, job_id"


def job_record(row):
    return {key: row[key] for key in ("job_id", "session_id", "base_revision", "state", "attempt",
                                     "created_at", "updated_at")} | {
        "selection": document(row["selection_json"])}


def session_jobs(connection, session_id, *, limit=20):
    rows = connection.execute("SELECT * FROM jobs WHERE session_id = ? ORDER BY " + JOB_ORDER + " LIMIT ?",
                              (session_id, limit + 1)).fetchall()
    return {"availability": "available", "items": [job_record(row) for row in rows[:limit]],
            "next_offset": limit if len(rows) > limit else None, "query": "job list --session <id>"}


class JobService:
    def __init__(self, store, registry=None):
        self.store, self.registry = store, registry or Registry()
        self.database = SessionDatabase(store)
        self.actions = ActionService(store, self.registry)
        self.sessions = SessionService(store, self.registry)

    @staticmethod
    def _job(connection, job_id):
        row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise AudioError("job_not_found", f"Unknown managed job: {job_id}")
        return row

    @staticmethod
    def _attempt(connection, job):
        return connection.execute("SELECT * FROM job_attempts WHERE job_id = ? AND attempt = ?",
                                  (job["job_id"], job["attempt"])).fetchone()

    @staticmethod
    def _batch(connection, batch_id):
        row = connection.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
        if row is None:
            raise AudioError("batch_not_found", f"Unknown batch: {batch_id}")
        return row

    def _resolve(self, action, protection=None):
        return self.actions.resolve({"schema": "matter-action/v1",
                                     "request_id": "job-" + uuid.uuid4().hex, **action,
                                     **({"protection": protection} if protection else {})})

    def _legacy_guard(self, connection, session_id):
        session = self.sessions._session(connection, session_id)
        policy = document(self.sessions._revision(connection, session_id, session["head_revision"])["constraints_json"])
        if policy and policy["regions"]:
            raise AudioError("constraint_conflict", "Legacy job has no bound PCM locks; submit a new protected job")

    @staticmethod
    def _comparable(resolution):
        body = {key: value for key, value in resolution.items() if key != "digest"}
        body["request"] = {key: value for key, value in body["request"].items() if key != "request_id"}
        return canonical(body)

    @staticmethod
    def _insert_attempt(connection, job_id, number, resolution):
        connection.execute("""INSERT INTO job_attempts
            (job_id, attempt, action_request_id, resolution_json, state) VALUES (?, ?, ?, ?, 'queued')""",
                           (job_id, number, resolution["request"]["request_id"], canonical(resolution).decode()))

    def _submit(self, connection, session_id, spec, resolution):
        session = self.sessions._session(connection, session_id)
        if connection.execute("SELECT 1 FROM jobs WHERE job_id = ?", (spec["job_id"],)).fetchone():
            raise AudioError("job_exists", "Job ID is already used; retry the original submission request")
        selection = spec.get("selection")
        protection = resolution["request"].get("protection")
        if protection and protection["revision"] != session["head_revision"]:
            raise AudioError("revision_conflict", "Session changed while preparing the job; read current context")
        if selection and selection["expected_revision"] != session["head_revision"]:
            raise AudioError("revision_conflict", "Selection changed before job submission")
        created = timestamp()
        connection.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, 'queued', 1, ?, ?, ?)", (
            spec["job_id"], session_id, session["head_revision"], canonical(spec).decode(),
            canonical({"status": "pending" if selection else "not_requested"}).decode(), created, created))
        self._insert_attempt(connection, spec["job_id"], 1, resolution)
        return job_record(self._job(connection, spec["job_id"]))

    @staticmethod
    def _expected(job, request):
        if request["expected_attempt"] != job["attempt"]:
            raise AudioError("attempt_conflict", "Attempt changed; read the job before retrying or cancelling",
                             details={"current_attempt": job["attempt"]})

    def _retry(self, connection, request, resolution, *, batch=False):
        job = self._job(connection, request["job_id"])
        self._expected(job, request)
        eligible = {"failed", "interrupted"} if batch else RETRYABLE
        if job["state"] not in eligible:
            raise AudioError("job_not_retryable", "Only stopped failed/interrupted attempts can be retried here")
        if job["attempt"] >= REVISION["maximum"]:
            raise AudioError("attempt_limit", "Job reached its attempt limit")
        spec = document(job["spec_json"])
        first = connection.execute("SELECT resolution_json FROM job_attempts WHERE job_id = ? AND attempt = 1",
                                   (job["job_id"],)).fetchone()[0]
        if self._comparable(resolution) != self._comparable(document(first)):
            raise AudioError("resolution_conflict", "Inputs, profile or effective parameters changed since submission")
        self._insert_attempt(connection, job["job_id"], job["attempt"] + 1, resolution)
        connection.execute("UPDATE jobs SET state = 'queued', attempt = attempt + 1, selection_json = ?, updated_at = ? WHERE job_id = ?",
                           (canonical({"status": "pending" if "selection" in spec else "not_requested"}).decode(),
                            timestamp(), job["job_id"]))
        return job_record(self._job(connection, job["job_id"]))

    def mutate(self, operation, request):
        if operation not in MUTATIONS:
            raise AudioError("unsupported_job_operation", operation)
        validate(request, MUTATIONS[operation])
        binding = canonical({"operation": "job." + operation, "request": request}).decode()
        # Return committed receipts even while a subsequent attempt owns the worker lock.
        with self.database.transaction() as connection:
            previous = connection.execute("SELECT * FROM mutations WHERE request_id = ?",
                                          (request["request_id"],)).fetchone()
            if previous is not None:
                if previous["binding_json"] != binding:
                    raise AudioError("request_conflict", "Mutation request ID binds different data")
                return document(previous["response_json"])
        # Retry also holds process ownership. No stale state can authorize a second worker.
        retry_ids = ([request["job_id"]] if operation == "retry" else
                     [item["job_id"] for item in request["items"]] if operation == "batch_retry" else [])
        with ExitStack() as stack:
            for job_id in sorted(set(retry_ids)):
                stack.enter_context(job_lock(self.store, job_id))
            # Resolve audio snapshots outside the short write transaction.
            prepared = {}
            specs = [request] if operation == "submit" else request["items"] if operation == "batch_submit" else []
            protection = None
            if specs:
                with self.database.transaction() as connection:
                    session = self.sessions._session(connection, request["session_id"])
                    protection = {"session_id": request["session_id"], "revision": session["head_revision"]}
            for spec in specs:
                prepared[spec["job_id"]] = self._resolve(spec["action"], protection)
            for job_id in retry_ids:
                with self.database.transaction() as connection:
                    job = self._job(connection, job_id)
                    spec = document(job["spec_json"])
                    protection = document(self._attempt(connection, job)["resolution_json"])["request"].get("protection")
                    if protection is None:
                        self._legacy_guard(connection, job["session_id"])
                prepared[job_id] = self._resolve(spec["action"], protection)
            with self.database.transaction(write=True) as connection:
                previous = connection.execute("SELECT * FROM mutations WHERE request_id = ?",
                                              (request["request_id"],)).fetchone()
                if previous is not None:
                    if previous["binding_json"] != binding:
                        raise AudioError("request_conflict", "Mutation request ID binds different data")
                    return document(previous["response_json"])
                if operation == "submit":
                    spec = {key: request[key] for key in ("job_id", "action", "selection") if key in request}
                    result = {"job": self._submit(connection, request["session_id"], spec, prepared[spec["job_id"]])}
                elif operation == "batch_submit":
                    if connection.execute("SELECT 1 FROM batches WHERE batch_id = ?", (request["batch_id"],)).fetchone():
                        raise AudioError("batch_exists", "Batch ID is already used")
                    self.sessions._session(connection, request["session_id"])
                    connection.execute("INSERT INTO batches VALUES (?, ?, ?)",
                                       (request["batch_id"], request["session_id"], timestamp()))
                    items = []
                    for index, spec in enumerate(request["items"]):
                        items.append(self._submit(connection, request["session_id"], spec, prepared[spec["job_id"]]))
                        connection.execute("INSERT INTO batch_items VALUES (?, ?, ?)",
                                           (request["batch_id"], index, spec["job_id"]))
                    result = {"batch_id": request["batch_id"], "jobs": items}
                elif operation == "retry":
                    result = {"job": self._retry(connection, request, prepared[request["job_id"]])}
                elif operation == "batch_retry":
                    self._batch(connection, request["batch_id"])
                    if len(retry_ids) != len(set(retry_ids)):
                        raise AudioError("invalid_request", "A retry lists each failed job once")
                    items = []
                    for item in request["items"]:
                        if not connection.execute("SELECT 1 FROM batch_items WHERE batch_id = ? AND job_id = ?",
                                                  (request["batch_id"], item["job_id"])).fetchone():
                            raise AudioError("batch_item_mismatch", "Retry item does not belong to this batch")
                        items.append(self._retry(connection, item, prepared[item["job_id"]], batch=True))
                    result = {"batch_id": request["batch_id"], "jobs": items}
                else:
                    job = self._job(connection, request["job_id"])
                    self._expected(job, request)
                    if job["state"] == "queued":
                        self._stop(connection, job, "cancelled", {"code": "job_cancelled", "message": "Cancelled before execution"})
                    elif job["state"] == "running":
                        connection.execute("UPDATE jobs SET state = 'cancel_requested', updated_at = ? WHERE job_id = ?",
                                           (timestamp(), job["job_id"]))
                        connection.execute("UPDATE job_attempts SET state = 'cancel_requested' WHERE job_id = ? AND attempt = ?",
                                           (job["job_id"], job["attempt"]))
                    result = {"job": job_record(self._job(connection, job["job_id"]))}
                response = {"schema": "matter-job-mutation/v1", "status": "succeeded", **result}
                connection.execute("INSERT INTO mutations VALUES (?, ?, ?, ?)",
                                   (request["request_id"], binding, canonical(response).decode(), timestamp()))
                return response

    @staticmethod
    def _stop(connection, job, state, error):
        now = timestamp()
        connection.execute("UPDATE job_attempts SET state = ?, error_json = ?, finished_at = ? WHERE job_id = ? AND attempt = ?",
                           (state, canonical(error).decode(), now, job["job_id"], job["attempt"]))
        selection = document(job["selection_json"])
        if selection["status"] == "pending":
            selection = {"status": "not_applied", "reason": state}
        connection.execute("UPDATE jobs SET state = ?, selection_json = ?, updated_at = ? WHERE job_id = ?",
                           (state, canonical(selection).decode(), now, job["job_id"]))

    def _finish(self, job_id, result):
        with self.database.transaction(write=True) as connection:
            job = self._job(connection, job_id)
            attempt = self._attempt(connection, job)
            resolution = document(attempt["resolution_json"])
            if (result["request_id"] != attempt["action_request_id"] or
                    result["binding_digest"] != fingerprint({"resolution": resolution})):
                raise AudioError("integrity_error", "Published result does not match the managed attempt")
            if job["state"] in TERMINAL:
                return
            state = result["status"]
            if result.get("error", {}).get("code") == "job_cancelled":
                state = "cancelled"
            selection = document(job["selection_json"])
            spec = document(job["spec_json"])
            if "selection" in spec:
                selection = {"status": "not_applied", "reason": state}
                if state == "succeeded" and job["state"] != "cancel_requested":
                    desired = spec["selection"]
                    try:
                        output = result["outputs"][desired["output_index"]]
                        selected = self.sessions._select(connection, {
                            "session_id": job["session_id"], "expected_revision": desired["expected_revision"],
                            "asset_id": output["asset_id"]}, timestamp())
                        selection = {"status": "selected", "revision": selected["revision"]["revision"]}
                    except IndexError:
                        selection = {"status": "not_applied", "reason": "output_not_found"}
                    except AudioError as exc:
                        selection = {"status": "not_applied", "reason": exc.code, "details": exc.details}
                elif job["state"] == "cancel_requested":
                    selection["reason"] = "cancel_requested"
            now = timestamp()
            connection.execute("UPDATE job_attempts SET state = ?, result_json = ?, error_json = ?, finished_at = ? WHERE job_id = ? AND attempt = ?",
                               (state, canonical(result).decode(), canonical(result.get("error")).decode(),
                                now, job_id, job["attempt"]))
            connection.execute("UPDATE jobs SET state = ?, selection_json = ?, updated_at = ? WHERE job_id = ?",
                               (state, canonical(selection).decode(), now, job_id))

    def _recover(self, job_id, error=None):
        with self.database.transaction() as connection:
            job = self._job(connection, job_id)
            attempt = self._attempt(connection, job)
        if job["state"] in TERMINAL or job["state"] == "queued":
            return
        try:
            result = self.store.show_request(attempt["action_request_id"])
        except AudioError as exc:
            if exc.code not in ("request_not_found", "recovery_pending"):
                raise  # Damaged complete results are never reclassified as safe to retry.
            with self.database.transaction(write=True) as connection:
                job = self._job(connection, job_id)
                cancelled = job["state"] == "cancel_requested"
                self._stop(connection, job, "cancelled" if cancelled else "failed" if error else "interrupted",
                           {"code": "job_cancelled", "message": "Worker stopped without a complete result",
                            **({"worker_error": error} if error else {})}
                           if cancelled else error or {"code": "worker_interrupted", "message": "No complete result; explicit retry is available"})
        else:
            self._finish(job_id, result)

    def recover(self, job_id):
        validate(job_id, IDENTIFIER)
        with job_lock(self.store, job_id):
            self._recover(job_id)
            return self.show(job_id)

    def run(self, job_id):
        validate(job_id, IDENTIFIER)
        with job_lock(self.store, job_id):
            with self.database.transaction(write=True) as connection:
                job = self._job(connection, job_id)
                if job["state"] in ("running", "cancel_requested"):
                    raise AudioError("recovery_required", "Previous worker stopped; run job recover before retrying")
                execute = job["state"] == "queued"
                if execute:
                    resolution = document(self._attempt(connection, job)["resolution_json"])
                    now = timestamp()
                    connection.execute("UPDATE jobs SET state = 'running', updated_at = ? WHERE job_id = ?", (now, job_id))
                    connection.execute("UPDATE job_attempts SET state = 'running', started_at = ? WHERE job_id = ? AND attempt = ?",
                                       (now, job_id, job["attempt"]))
            if execute:
                last_check = 0.0

                def check(force):
                    nonlocal last_check
                    now = time.monotonic()
                    if not force and now - last_check < 0.05:
                        return
                    last_check = now
                    with self.database.transaction() as connection:
                        if self._job(connection, job_id)["state"] == "cancel_requested":
                            raise AudioError("job_cancelled", "Worker acknowledged cancellation at a CPU checkpoint")
                        if "protection" not in resolution["request"]:
                            self._legacy_guard(connection, job["session_id"])

                try:
                    with checkpoints(check):
                        result = self.actions.execute(resolution["request"],
                                                      expected_resolution_digest=resolution["digest"]["hex"])
                except KeyboardInterrupt:
                    with self.database.transaction(write=True) as connection:
                        connection.execute("UPDATE jobs SET state = 'cancel_requested' WHERE job_id = ?", (job_id,))
                    self._recover(job_id)
                except Exception as exc:
                    error = exc.document() if isinstance(exc, AudioError) else {
                        "code": "worker_error", "message": str(exc)[:1000], "type": type(exc).__name__}
                    self._recover(job_id, error)
                else:
                    # Deliberately outside the execution exception handler: a crash here must be recovered,
                    # never turned into a second execution or an apparently completed registration.
                    self._finish(job_id, result)
            return self.show(job_id)

    def show(self, job_id, *, offset=0, limit=50):
        validate(job_id, IDENTIFIER)
        page_parameters(offset, limit)
        with self.database.transaction() as connection:
            job = self._job(connection, job_id)
            current = self._attempt(connection, job)
            rows = connection.execute("SELECT * FROM job_attempts WHERE job_id = ? ORDER BY attempt DESC LIMIT ? OFFSET ?",
                                      (job_id, limit + 1, offset)).fetchall()
        result = document(current["result_json"]) if current["result_json"] else None
        if result is not None and self.store.show_request(current["action_request_id"]) != result:
            raise AudioError("integrity_error", "Registered result differs from the immutable publication")
        attempts = [{key: row[key] for key in ("attempt", "action_request_id", "state", "started_at", "finished_at")} |
                    {"error": document(row["error_json"]) if row["error_json"] else None} |
                    self.store.execution_evidence(row["action_request_id"])
                    for row in rows[:limit]]
        return {"schema": "matter-job/v1", "status": job["state"], "job": job_record(job),
                "action": document(job["spec_json"])["action"],
                "resolution": document(current["resolution_json"]), "attempts": attempts,
                "next_offset": offset + limit if len(rows) > limit else None, "result": result,
                "error": document(current["error_json"]) if current["error_json"] else None,
                "playback": self.store.playback_refs(result or {}),
                **self.store.execution_evidence(current["action_request_id"])}

    def list_jobs(self, *, session_id=None, offset=0, limit=50):
        page_parameters(offset, limit)
        parameters = []
        where = ""
        if session_id is not None:
            validate(session_id, IDENTIFIER)
            where = " WHERE session_id = ?"
            parameters.append(session_id)
        with self.database.transaction() as connection:
            if session_id is not None:
                self.sessions._session(connection, session_id)
            rows = connection.execute("SELECT * FROM jobs" + where + " ORDER BY " + JOB_ORDER + " LIMIT ? OFFSET ?",
                                      (*parameters, limit + 1, offset)).fetchall()
        return {"schema": "matter-job-list/v1", "jobs": [job_record(row) for row in rows[:limit]],
                "next_offset": offset + limit if len(rows) > limit else None}

    def batch_show(self, batch_id):
        validate(batch_id, IDENTIFIER)
        with self.database.transaction() as connection:
            batch = dict(self._batch(connection, batch_id))
            rows = connection.execute("SELECT j.* FROM batch_items b JOIN jobs j ON b.job_id = j.job_id WHERE b.batch_id = ? ORDER BY b.position",
                                      (batch_id,)).fetchall()
        counts = Counter(row["state"] for row in rows)
        state = ("succeeded" if counts["succeeded"] == len(rows) else
                 "in_progress" if any(row["state"] not in TERMINAL for row in rows) else "partial_failure")
        return {"schema": "matter-batch/v1", "status": state, "batch": batch,
                "counts": dict(counts), "jobs": [job_record(row) for row in rows]}

    def batch_run(self, batch_id):
        # A batch is a bounded sequential runner, not a daemon or a second GPU queue.
        for job in self.batch_show(batch_id)["jobs"]:
            if job["state"] == "queued":
                try:
                    self.run(job["job_id"])
                except AudioError as exc:
                    if exc.code not in ("job_busy", "recovery_required"):
                        raise
        return self.batch_show(batch_id)

    def batch_recover(self, batch_id):
        busy = []
        for job in self.batch_show(batch_id)["jobs"]:
            if job["state"] in ("running", "cancel_requested"):
                try:
                    self.recover(job["job_id"])
                except AudioError as exc:
                    if exc.code != "job_busy":
                        raise
                    busy.append(job["job_id"])
        return {**self.batch_show(batch_id), "busy_jobs": busy}
