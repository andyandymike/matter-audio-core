"""Managed job and batch requests. Audio attempt IDs are allocated by the core."""

from .contracts import ACTION_SCHEMA, object_schema
from .session_contracts import IDENTIFIER, REVISION

ACTION = object_schema({key: ACTION_SCHEMA["properties"][key]
                        for key in ("operation", "inputs", "parameters")})
SELECTION = object_schema({"expected_revision": REVISION,
                           "output_index": {"type": "integer", "minimum": 0, "maximum": 999}})
SPEC = object_schema({"job_id": IDENTIFIER, "action": ACTION, "selection": SELECTION},
                     ["job_id", "action"])
SUBMIT = object_schema({"schema": {"const": "matter-job-submit/v1"}, "request_id": IDENTIFIER,
                        "session_id": IDENTIFIER, **SPEC["properties"]},
                       ["schema", "request_id", "session_id", "job_id", "action"])
BATCH = object_schema({"schema": {"const": "matter-batch-submit/v1"}, "request_id": IDENTIFIER,
                       "session_id": IDENTIFIER, "batch_id": IDENTIFIER,
                       "items": {"type": "array", "minItems": 1, "maxItems": 100,
                                 "items": object_schema({"job_id": IDENTIFIER, "action": ACTION})}})
ATTEMPT = object_schema({"job_id": IDENTIFIER, "expected_attempt": REVISION})
RETRY = object_schema({"schema": {"const": "matter-job-retry/v1"}, "request_id": IDENTIFIER,
                       **ATTEMPT["properties"]})
CANCEL = object_schema({**RETRY["properties"], "schema": {"const": "matter-job-cancel/v1"}})
BATCH_RETRY = object_schema({"schema": {"const": "matter-batch-retry/v1"}, "request_id": IDENTIFIER,
                             "batch_id": IDENTIFIER,
                             "items": {"type": "array", "minItems": 1, "maxItems": 100,
                                       "items": ATTEMPT}})
MUTATIONS = {"submit": SUBMIT, "cancel": CANCEL, "retry": RETRY,
             "batch_submit": BATCH, "batch_retry": BATCH_RETRY}


def capabilities():
    return {"availability": "available", "mutation_schemas": MUTATIONS,
            "execution": "synchronous_local_worker", "recovery": "explicit_verify_and_register",
            "cancellation": "cooperative_checkpoints_and_owned_backend_processes",
            "model_attempt_evidence": "durable_launch_and_shutdown_journal/v1",
            "retry": "explicit_new_attempt_after_confirmed_stop",
            "scope": "Managed jobs only; legacy action claims are never reclaimed."}
