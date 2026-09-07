"""Typed operation registry and shared resolve/execute services."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable

from . import __version__
from .artifacts import ArtifactStore, Publication
from .contracts import ACTION_SCHEMA, fingerprint, object_schema, validate
from .errors import AudioError
from .execution import checkpoint
from .fades import FADE_PROFILE, FADE_SCHEMA, fade, fade_writes, identity_mapping, resolve_fade
from .regions import check_plan, verify_regions
from .media import (MAX_AUDIO_BYTES, PROFILE, PCM, decode_wav, encode_wav, gain, gain_multiplier,
                    inspect, trim, Q24)


@dataclass(frozen=True)
class Operation:
    name: str
    parameters_schema: dict
    resolve: Callable[[dict, PCM], dict]
    execute: Callable[[dict, PCM], tuple[PCM | None, dict]]
    profile: str = PROFILE
    mapping: Callable[[dict, PCM], dict] | None = None
    writes: Callable[[dict, PCM], list] | None = None
    input_count: tuple[int, int] = (1, 1)
    realization: str = "deterministic"
    availability: str = "available"


@dataclass(frozen=True)
class AudioAttachment:
    pcm: PCM
    role: str
    provenance: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OperationOutput:
    pcm: PCM | None
    observation: dict
    attachments: tuple[AudioAttachment, ...] = ()


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
                 "parameters_schema": op.parameters_schema, "availability": op.availability,
                 "realization": op.realization, "verification": "exact_pcm_and_measurements",
                 "input_count": {"minimum": op.input_count[0], "maximum": op.input_count[1]},
                 "pcm_region_protection": "supported" if op.mapping and op.writes else "unavailable",
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


def _trim_mapping(parameters, pcm):
    return {"kind": "slice", "source_start_frame": parameters["start_frame"],
            "output_start_frame": 0, "frame_count": parameters["end_frame"] - parameters["start_frame"]}


def builtin_operations() -> list[Operation]:
    from .composition import composition_operations
    integer = {"type": "integer", "minimum": 0, "maximum": 230400000}
    seconds = {"type": "number", "minimum": 0, "maximum": 86400}
    return [
        Operation("inspect/v1", object_schema({
            "window_frames": {"type": "integer", "minimum": 1, "maximum": 230400000},
            "offset": {"type": "integer", "minimum": 0, "maximum": 230400000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 128}}, []),
            lambda p, pcm: {"window_frames": p.get("window_frames", max(1, pcm.sample_rate // 10)),
                            "offset": p.get("offset", 0), "limit": p.get("limit", 128)},
            lambda p, pcm: (None, inspect(pcm, **p)), "pcm16-levels/v1",
            mapping=identity_mapping, writes=lambda p, pcm: []),
        Operation("gain/v1", object_schema({
            "db": {"type": "number", "minimum": -60, "maximum": 24},
            "clip": {"enum": ["reject", "saturate"]}}, ["db"]),
            lambda p, pcm: {"db": p["db"], "gain_q24": gain_multiplier(p["db"]),
                            "clip": p.get("clip", "reject")}, _gain_execute,
            mapping=identity_mapping, writes=lambda p, pcm: [] if p["gain_q24"] == Q24 else [
                {"start_frame": 0, "end_frame": pcm.frames}]),
        Operation("trim/v1", {"oneOf": [
            object_schema({"start_frame": integer, "end_frame": integer}),
            object_schema({"start_seconds": seconds, "end_seconds": seconds})]},
            _trim_resolve,
            lambda p, pcm: (trim(pcm, p["start_frame"], p["end_frame"]), {
                "time_mapping": {"kind": "slice", "source_start_frame": p["start_frame"],
                                 "output_start_frame": 0, "frame_count": p["end_frame"] - p["start_frame"]}}),
            mapping=_trim_mapping, writes=lambda p, pcm: []),
        Operation("fade/v1", FADE_SCHEMA, resolve_fade, fade, FADE_PROFILE,
                  mapping=identity_mapping, writes=fade_writes),
    ] + composition_operations()


class ActionService:
    def __init__(self, store: ArtifactStore, registry: Registry | None = None):
        self.store, self.registry = store, registry or Registry()

    def _inputs(self, request, operation):
        if not operation.input_count[0] <= len(request["inputs"]) <= operation.input_count[1]:
            raise AudioError("invalid_request", f"{operation.name} expects {operation.input_count} inputs")
        records, pcms, total = [], [], 0
        for asset_id in request["inputs"]:
            checkpoint()
            record, data = self.store.asset(asset_id)
            total += len(data)
            if total > MAX_AUDIO_BYTES:
                raise AudioError("input_limit", "Combined input WAV data exceeds 64 MiB")
            records.append(record)
            pcms.append(decode_wav(data))
        return records, pcms[0] if operation.input_count == (1, 1) else pcms

    def resolve(self, request: dict) -> dict:
        validate(request, ACTION_SCHEMA)
        operation = self.registry.get(request["operation"])
        validate(request["parameters"], operation.parameters_schema)
        records, audio = self._inputs(request, operation)
        record = records[0]
        pcm = audio[0] if isinstance(audio, list) else audio
        body = {"schema": "matter-resolution/v1", "request": request,
                "inputs": [{"asset_id": item["asset_id"], "digest": item["digest"]} for item in records],
                "profile": operation.profile, "effective_parameters": operation.resolve(request["parameters"], audio),
                "audio_model_calls": 0, "unverified_goals": []}
        if "protection" in request:
            from .sessions import SessionService
            protection = SessionService(self.store, self.registry).protection(request["protection"], record["asset_id"])
            parameters = body["effective_parameters"]
            mapping = operation.mapping(parameters, audio) if operation.mapping else None
            writes = operation.writes(parameters, audio) if operation.writes else None
            protection["output_regions"] = check_plan(protection["input_regions"], mapping, writes, pcm.frames)
            protection["time_mapping"] = mapping
            protection["write_ranges"] = writes
            body["protection"] = protection
        return {**body, "digest": fingerprint(body)}

    def execute(self, request: dict, *, expected_resolution_digest: str | None = None) -> dict:
        checkpoint(force=True)
        resolution = self.resolve(request)
        if expected_resolution_digest is not None and resolution["digest"]["hex"] != expected_resolution_digest:
            raise AudioError("resolution_conflict", "Resolution changed since preview")
        operation = self.registry.get(request["operation"])

        def produce(publication: Publication):
            checkpoint(force=True)
            records, audio = self._inputs(request, operation)
            record = records[0]
            if [{"asset_id": item["asset_id"], "digest": item["digest"]} for item in records] != resolution["inputs"]:
                raise AudioError("input_changed", "Resolved input changed before execution")
            pcm = audio[0] if isinstance(audio, list) else audio
            executed = operation.execute(resolution["effective_parameters"], audio)
            attachments = executed.attachments if isinstance(executed, OperationOutput) else ()
            output, observation = (executed.pcm, executed.observation) if isinstance(executed, OperationOutput) else executed
            checkpoint(force=True)
            if "protection" in resolution:
                from .sessions import SessionService
                bound = resolution["protection"]
                fresh = SessionService(self.store, self.registry).protection(request["protection"], record["asset_id"])
                if fresh["constraints_digest"] != bound["constraints_digest"]:
                    raise AudioError("constraint_conflict", "Constraints changed during execution")
                if bound["input_regions"]:
                    if output is not None:
                        if (output.sample_rate, output.channels) != (pcm.sample_rate, pcm.channels):
                            raise AudioError("constraint_violation", "Protected PCM format changed")
                        if observation.get("time_mapping") != bound["time_mapping"] or output.frames != bound["time_mapping"]["frame_count"]:
                            raise AudioError("constraint_mapping_unavailable", "Actual output mapping differs from the preview")
                        verify_regions(output, bound["output_regions"])
                    else:
                        verify_regions(pcm, bound["input_regions"])
                observation = {**observation, "protection": {
                    "method": "pcm-region-sha256/v1", "constraints_digest": bound["constraints_digest"],
                    "status": "verified" if bound["input_regions"] else "no_regions",
                    "regions": bound["output_regions"] if output is not None else bound["input_regions"]}}
            if output is not None:
                publication.add(encode_wav(output), output.facts(),
                                parents=[{"role": "source" if i == 0 else "layer", "asset_id": item["asset_id"], "digest": item["digest"]}
                                         for i, item in enumerate(records)],
                                provenance={"operation": operation.name, "profile": operation.profile})
            for attachment in attachments:
                publication.add(encode_wav(attachment.pcm), attachment.pcm.facts(), role=attachment.role,
                    parents=[{"role": "context", "asset_id": item["asset_id"], "digest": item["digest"]} for item in records],
                    provenance={"operation": operation.name, "profile": operation.profile, **attachment.provenance})
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
