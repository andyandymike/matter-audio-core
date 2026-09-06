"""Versioned requests for durable authoring state, separate from audio actions."""

from .contracts import ASSET_PATTERN, REQUEST_PATTERN, object_schema, validate
from .regions import RANGE_SCHEMA

IDENTIFIER = {"type": "string", "pattern": REQUEST_PATTERN}
REVISION = {"type": "integer", "minimum": 1, "maximum": 2147483647}
ASSET_ID = {"type": "string", "pattern": ASSET_PATTERN}
NAME = {"type": "string", "minLength": 1, "maxLength": 240, "pattern": r"\S"}

CREATE_SCHEMA = object_schema({
    "schema": {"const": "matter-session-create/v1"},
    "request_id": IDENTIFIER, "session_id": IDENTIFIER, "name": NAME,
    "asset_id": ASSET_ID,
}, ["schema", "request_id", "session_id", "name"])

SELECT_SCHEMA = {
    **object_schema({
        "schema": {"const": "matter-session-select/v1"},
        "request_id": IDENTIFIER, "session_id": IDENTIFIER,
        "expected_revision": REVISION, "asset_id": ASSET_ID, "from_revision": REVISION,
    }, ["schema", "request_id", "session_id", "expected_revision"]),
    "oneOf": [{"required": ["asset_id"]}, {"required": ["from_revision"]}],
}

BRANCH_SCHEMA = object_schema({
    "schema": {"const": "matter-session-branch/v1"},
    "request_id": IDENTIFIER, "session_id": IDENTIFIER, "name": NAME,
    "from_session": IDENTIFIER, "from_revision": REVISION,
})

FEEDBACK_SCHEMA = object_schema({
    "schema": {"const": "matter-feedback/v1"},
    "request_id": IDENTIFIER, "session_id": IDENTIFIER, "revision": REVISION,
    "text": {"type": "string", "minLength": 1, "maxLength": 4000, "pattern": r"\S"},
    "source": {"enum": ["agent_relay", "user_cli", "user_ui", "agent"]},
    "listening_context": {"type": "string", "maxLength": 2000},
}, ["schema", "request_id", "session_id", "revision", "text", "source"])

CONSTRAINTS_SCHEMA = object_schema({
    "schema": {"const": "matter-constraints-set/v1"}, "request_id": IDENTIFIER,
    "session_id": IDENTIFIER, "expected_revision": REVISION,
    "regions": {"type": "array", "maxItems": 16, "items": RANGE_SCHEMA},
})

MUTATIONS = {"create": CREATE_SCHEMA, "select": SELECT_SCHEMA,
             "branch": BRANCH_SCHEMA, "feedback": FEEDBACK_SCHEMA, "constraints": CONSTRAINTS_SCHEMA}


def page_parameters(offset: int, limit: int) -> None:
    validate({"offset": offset, "limit": limit}, object_schema({
        "offset": {"type": "integer", "minimum": 0, "maximum": 2147483647},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    }))


def capabilities() -> dict:
    return {"schema": "matter-session-capabilities/v1", "availability": "available",
            "storage": "sqlite/v1", "mutation_schemas": MUTATIONS,
            "queries": ["session list", "session show", "session request",
                        "feedback list", "context show", "constraints show"],
            "migration_command": "session migrate", "database_schema_version": 3,
            "pcm_region_protection": {"availability": "available", "schema": "matter-pcm-constraints/v1",
                                      "max_regions": 16, "mapping_kinds": ["identity", "slice"],
                                      "verification": "pcm-region-sha256/v1"},
            "limitations": ["Direct audio actions require explicit selection into a session."]}
