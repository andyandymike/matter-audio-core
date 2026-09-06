"""Typed operation registry and shared resolve/execute services."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable

from . import __version__
from .artifacts import ArtifactStore, Publication
from .contracts import ACTION_SCHEMA, fingerprint, object_schema, validate
from .errors import AudioError
from .execution import checkpoint
from .media import (PROFILE, PCM, decode_wav, encode_wav, gain, gain_multiplier,
                    inspect, trim)


@dataclass(frozen=True)
class Operation:
    name: str
    parameters_schema: dict
    resolve: Callable[[dict, PCM], dict]
    execute: Callable[[dict, PCM], tuple[PCM | None, dict]]
    profile: str = PROFILE


class Registry:
    def __init__(self, operations: list[Operation] | None = None):
        self._operations = {}
        for operation in operations if operations is not None else builtin_operations():
            self.register(operation)

    def register(self, operation: Operation) -> None:
        if operation.name in self._operations:
            raise AudioError("duplicate_operation", operation.name)
        self._operations[operation.name] = operation

    def get(self, name: str) -> Operation:
        if name not in self._operations:
            raise AudioError("unsupported_operation", f"Operation is not registered: {name}")
        return self._operations[name]

    def capabilities(self) -> list[dict]:
        return [{"operation": op.name, "profile": op.profile,
                 "parameters_schema": op.parameters_schema, "availability": "available",
                 "realization": "deterministic", "verification": "exact_pcm_and_measurements",
                 "evidence": {"kind": "implementation", "core_version": __version__,
                              "scope": "Local execution evidence is stored per result; no quality claim."}}
                for op in self._operations.values()]


def _trim_resolve(parameters: dict, pcm: PCM) -> dict:
    if "start_frame" in parameters:
        start, end = parameters["start_frame"], parameters["end_frame"]
    else:
        to_frame = lambda value: int((Decimal(str(value)) * pcm.sample_rate).to_integral_value(rounding=ROUND_HALF_UP))
        start, end = to_frame(parameters["start_seconds"]), to_frame(parameters["end_seconds"])
    if not 0 <= start < end <= pcm.frames:
        raise AudioError("invalid_range", "Resolved trim range is empty or outside the source")
    return {"start_frame": start, "end_frame": end,
            "start_seconds": start / pcm.sample_rate, "end_seconds": end / pcm.sample_rate}


def _gain_execute(parameters: dict, pcm: PCM) -> tuple[PCM, dict]:
    output, clipped = gain(pcm, parameters["gain_q24"], parameters["clip"])
    return output, {"overflow_sample_count": clipped,
                    "time_mapping": {"kind": "identity", "frame_count": pcm.frames}}


def builtin_operations() -> list[Operation]:
    integer = {"type": "integer", "minimum": 0, "maximum": 230400000}
    seconds = {"type": "number", "minimum": 0, "maximum": 86400}
    return [
        Operation("inspect/v1", object_schema({
            "window_frames": {"type": "integer", "minimum": 1, "maximum": 230400000},
            "offset": {"type": "integer", "minimum": 0, "maximum": 230400000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 128}}, []),
            lambda p, pcm: {"window_frames": p.get("window_frames", max(1, pcm.sample_rate // 10)),
                            "offset": p.get("offset", 0), "limit": p.get("limit", 128)},
            lambda p, pcm: (None, inspect(pcm, **p)), "pcm16-levels/v1"),
        Operation("gain/v1", object_schema({
            "db": {"type": "number", "minimum": -60, "maximum": 24},
            "clip": {"enum": ["reject", "saturate"]}}, ["db"]),
            lambda p, pcm: {"db": p["db"], "gain_q24": gain_multiplier(p["db"]),
                            "clip": p.get("clip", "reject")}, _gain_execute),
        Operation("trim/v1", {"oneOf": [
            object_schema({"start_frame": integer, "end_frame": integer}),
            object_schema({"start_seconds": seconds, "end_seconds": seconds})]},
            _trim_resolve,
            lambda p, pcm: (trim(pcm, p["start_frame"], p["end_frame"]), {
                "time_mapping": {"kind": "slice", "source_start_frame": p["start_frame"],
                                 "output_start_frame": 0, "frame_count": p["end_frame"] - p["start_frame"]}})),
    ]


class ActionService:
    def __init__(self, store: ArtifactStore, registry: Registry | None = None):
        self.store, self.registry = store, registry or Registry()

    def resolve(self, request: dict) -> dict:
        validate(request, ACTION_SCHEMA)
        operation = self.registry.get(request["operation"])
        validate(request["parameters"], operation.parameters_schema)
        record, data = self.store.asset(request["inputs"][0])
        pcm = decode_wav(data)
        body = {"schema": "matter-resolution/v1", "request": request,
                "inputs": [{"asset_id": record["asset_id"], "digest": record["digest"]}],
                "profile": operation.profile, "effective_parameters": operation.resolve(request["parameters"], pcm),
                "audio_model_calls": 0, "unverified_goals": []}
        return {**body, "digest": fingerprint(body)}

    def execute(self, request: dict, *, expected_resolution_digest: str | None = None) -> dict:
        checkpoint(force=True)
        resolution = self.resolve(request)
        if expected_resolution_digest is not None and resolution["digest"]["hex"] != expected_resolution_digest:
            raise AudioError("resolution_conflict", "Resolution changed since preview")
        operation = self.registry.get(request["operation"])

        def produce(publication: Publication):
            checkpoint(force=True)
            record, data = self.store.asset(request["inputs"][0])
            if record["digest"] != resolution["inputs"][0]["digest"]:
                raise AudioError("input_changed", "Resolved input changed before execution")
            pcm = decode_wav(data)
            output, observation = operation.execute(resolution["effective_parameters"], pcm)
            checkpoint(force=True)
            if output is not None:
                publication.add(encode_wav(output), output.facts(),
                                parents=[{"role": "source", "asset_id": record["asset_id"], "digest": record["digest"]}],
                                provenance={"operation": operation.name, "profile": operation.profile})
            checkpoint(force=True)
            return {"resolution": resolution, "findings": [{"kind": "measurement", "method": operation.profile,
                                                            "source_asset_id": record["asset_id"], **observation}],
                    "limitations": ["Successful execution does not establish listening acceptance."]}

        return self.store.transact(request["request_id"], {"resolution": resolution}, produce)

    def inspect_asset(self, asset_id: str, parameters: dict) -> dict:
        op = self.registry.get("inspect/v1")
        validate(parameters, op.parameters_schema)
        record, data = self.store.asset(asset_id)
        pcm = decode_wav(data)
        _, observation = op.execute(op.resolve(parameters, pcm), pcm)
        return {"schema": "matter-inspection/v1", "status": "succeeded", "outputs": [],
                "asset_id": asset_id, "audio_model_calls": 0, "findings": [observation]}
