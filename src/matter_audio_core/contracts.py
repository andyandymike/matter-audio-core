"""Versioned wire contracts, strict JSON, and canonical action identity."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import rfc8785
from jsonschema import Draft202012Validator, validators

from .errors import AudioError

MAX_JSON_BYTES = 1024 * 1024
REQUEST_SCHEMA = "matter-action/v1"
RESULT_SCHEMA = "matter-result/v1"
ASSET_PATTERN = r"^a_[0-9a-f]{32}_[0-9]{1,3}$"
REQUEST_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$"


def object_schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


STRICT_VALIDATOR = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine_many({
        "integer": lambda checker, value: type(value) is int,
        "number": lambda checker, value: type(value) in (int, float) and math.isfinite(value),
    }),
)


def validate(value, schema: dict) -> None:
    error = next(STRICT_VALIDATOR(schema).iter_errors(value), None)
    if error:
        location = ".".join(map(str, error.absolute_path)) or "$"
        raise AudioError("invalid_request", f"{location}: {error.message}")
    canonical(value)


def canonical(value) -> bytes:
    try:
        return rfc8785.dumps(value)
    except (ValueError, TypeError) as exc:
        raise AudioError("invalid_json", f"Cannot canonicalize JSON: {exc}") from exc


def digest(data: bytes) -> dict:
    return {"algorithm": "sha256", "hex": hashlib.sha256(data).hexdigest()}


def fingerprint(value) -> dict:
    return digest(canonical(value))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AudioError("invalid_json", f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise AudioError("invalid_json", f"Nonfinite JSON value: {value}")


def parse_json(data: bytes):
    try:
        if len(data) > MAX_JSON_BYTES:
            raise AudioError("json_too_large", "JSON exceeds 1 MiB")
        result = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_pairs,
                            parse_constant=_constant)
        canonical(result)
        return result
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise AudioError("invalid_json", str(exc)) from exc


def read_json(path: Path):
    with path.open("rb") as stream:
        return parse_json(stream.read(MAX_JSON_BYTES + 1))


PROTECTION_REF = object_schema({"session_id": {"type": "string", "pattern": REQUEST_PATTERN},
                                "revision": {"type": "integer", "minimum": 1, "maximum": 2147483647}})

ACTION_SCHEMA = object_schema({
    "schema": {"const": REQUEST_SCHEMA},
    "request_id": {"type": "string", "pattern": REQUEST_PATTERN},
    "operation": {"type": "string", "minLength": 1, "maxLength": 96},
    "inputs": {"type": "array", "minItems": 1, "maxItems": 16,
               "items": {"type": "string", "pattern": ASSET_PATTERN}},
    "parameters": {"type": "object"},
    "protection": PROTECTION_REF,
}, ["schema", "request_id", "operation", "inputs", "parameters"])


def request(request_id: str, operation: str, asset_id: str, parameters: dict) -> dict:
    return {"schema": REQUEST_SCHEMA, "request_id": request_id, "operation": operation,
            "inputs": [asset_id], "parameters": parameters}
