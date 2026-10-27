"""Bind external analysis to exact local audio without changing its selection."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import math
import os
import uuid

from matter_audio_core.artifacts import safe_path
from matter_audio_core.contracts import ASSET_PATTERN, MAX_JSON_BYTES, REQUEST_PATTERN, canonical, fingerprint, object_schema, validate
from matter_audio_core.errors import AudioError
from matter_audio_core.execution import checkpoint, record_execution
from matter_audio_core.media import decode_wav

from . import __version__, client

REQUEST_SCHEMA = object_schema({
    "schema": {"const": "matter-transcription-request/v1"},
    "request_id": {"type": "string", "pattern": REQUEST_PATTERN},
    "asset_id": {"type": "string", "pattern": ASSET_PATTERN},
    "timestamps": {"enum": ["word", "segment"]},
    "language": {"type": "string", "pattern": r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}$"},
}, ["schema", "request_id", "asset_id"])

LIMITATIONS = [
    "Transcription and timestamps are model estimates, not human-approved text or exact edit boundaries.",
    "The service may fall back internally; alignment method and precision are unverified unless separately evidenced.",
    "A configured model/revision is a request declaration, not proof of the server's loaded weights.",
    "Remote inference counts and cancellation cannot be confirmed through this endpoint; there is no automatic retry.",
]


def _text(value, field, maximum=65536):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise AudioError("remote_response_invalid", f"Invalid or oversized {field} in transcription response.")
    return value


def _timed(items, field, pcm):
    if not isinstance(items, list) or len(items) > 10000:
        raise AudioError("remote_response_invalid", "Invalid or oversized timing list.")
    result = []
    previous = -1
    for item in items:
        if not isinstance(item, dict):
            raise AudioError("remote_response_invalid", "Timing entries must be objects.")
        times = [item.get("start"), item.get("end")]
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in times):
            raise AudioError("remote_response_invalid", "Timing entries require finite start/end seconds.")
        start, end = times
        if not 0 <= start <= end <= pcm.frames / pcm.sample_rate + 1 / pcm.sample_rate:
            raise AudioError("remote_response_invalid", "Timing entry is outside the source audio.")
        frames = [min(pcm.frames, int((Decimal(str(value)) * pcm.sample_rate).to_integral_value(rounding=ROUND_HALF_UP))) for value in times]
        if frames[0] < previous:
            raise AudioError("remote_response_invalid", "Timing entries must be ordered by source start.")
        previous = frames[0]
        result.append({"text": _text(item.get(field), "timed text", 8192),
                       "start_frame": frames[0], "end_frame": frames[1],
                       "provider_start_seconds": start, "provider_end_seconds": end})
    return result


def normalize(response, pcm, source, service, requested):
    warnings = []
    words = _timed(response.get("words", []), "word", pcm)
    segments = _timed(response.get("segments", []), "text", pcm)
    if requested["timestamps"] == "word" and not words:
        warnings.append("requested_word_timestamps_missing")
    if not segments:
        warnings.append("segment_timestamps_missing_subtitles_unavailable")
    language = response.get("language")
    if language is not None:
        language = _text(language, "language", 128)
    model = response.get("model")
    if model is not None:
        model = _text(model, "model", 256)
    return {"schema": "matter-transcript/v1", "source": source,
            "service": service, "requested": requested, "text": _text(response.get("text"), "text"),
            "language": language, "provider_reported_model": model,
            "segments": segments, "words": words, "warnings": warnings,
            "timing": {"sample_rate": pcm.sample_rate, "source_frames": pcm.frames,
                       "kind": "model_estimate", "conversion": "nearest-frame-half-up; at most one trailing frame clipped",
                       "alignment_status": "not_reported", "precision_verified": False},
            "limitations": LIMITATIONS}


def _milliseconds(frame, rate):
    return int((Decimal(frame) * 1000 / rate).to_integral_value(rounding=ROUND_HALF_UP))


def _stamp(milliseconds, separator):
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{ms:03}"


def subtitles(document, format):
    rate = document["timing"]["sample_rate"]
    cues = []
    for segment in document["segments"]:
        # Do not manufacture a cue for zero-duration or blank observations.
        text = " ".join(segment["text"].split())
        start = _milliseconds(segment["start_frame"], rate)
        end = _milliseconds(segment["end_frame"], rate)
        if not text or end <= start:
            continue
        if format == "vtt":
            text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        separator = "," if format == "srt" else "."
        cues.append(f"{len(cues) + 1}\n{_stamp(start, separator)} --> {_stamp(end, separator)}\n{text}\n")
    if not cues:
        return b""
    return (("WEBVTT\n\n" if format == "vtt" else "") + "\n".join(cues)).encode("utf-8")


class TranscriptionService:
    def __init__(self, store, config):
        self.store, self.config = store, config

    def resolve(self, request):
        validate(request, REQUEST_SCHEMA)
        record, data = self.store.asset(request["asset_id"])
        pcm = decode_wav(data)
        options = {"timestamps": request.get("timestamps", "word")}
        if "language" in request:
            options["language"] = request["language"]
        source = {"asset_id": record["asset_id"], "digest": record["digest"], "media": pcm.facts()}
        binding = {"operation": "voicestudio.transcribe/v1", "adapter_version": __version__,
                   "request": request, "source": source, "parameters": options, "service": client.identity(self.config)}
        return {"schema": "matter-transcription-resolution/v1", "binding": binding, "digest": fingerprint(binding),
                "network_requests": 0, "audio_model_calls": 0,
                "execution_effect": "Uploads the exact source WAV to the configured service when explicitly executed.",
                "limitations": LIMITATIONS}

    def execute(self, request, expected_resolution_digest):
        resolution = self.resolve(request)
        if resolution["digest"]["hex"] != expected_resolution_digest:
            raise AudioError("resolution_changed", "Resolve the exact source, parameters and endpoint before execution.")
        binding = resolution["binding"]

        def produce(publication):
            token = client.credential(self.config)
            record, audio = self.store.asset(request["asset_id"])
            if record["digest"] != binding["source"]["digest"]:
                raise AudioError("source_changed", "Source changed after preview.")
            pcm = decode_wav(audio)
            checkpoint()
            call_id = uuid.uuid4().hex
            event = {"call_id": call_id, "audio_model": "remote-asr",
                     "backend_started": None, "transport": "http", "remote_requests": 1}
            record_execution({**event, "phase": "remote_request_intent", "remote_outcome": "unknown"})
            try:
                response = client.transcribe(self.config, audio, binding["parameters"], token)
                document = normalize(response, pcm, binding["source"], binding["service"], binding["parameters"])
            except AudioError:
                record_execution({**event, "phase": "remote_request_unconfirmed", "remote_outcome": "unconfirmed"})
                raise
            record_execution({**event, "phase": "remote_response_received", "remote_outcome": "response_received",
                              "model_execution_count": None})
            subtitle_data = {format: data for format in ("srt", "vtt")
                             if (data := subtitles(document, format))}
            if document["segments"] and not subtitle_data:
                document["warnings"].append("no_usable_subtitle_cues")
            transcript_data = canonical(document)
            if len(transcript_data) > MAX_JSON_BYTES:
                raise AudioError("transcript_too_large", "Normalized transcript exceeds 1 MiB; use shorter source audio.")
            parents = [{"role": "transcription_source", "asset_id": record["asset_id"], "digest": record["digest"]}]
            transcript = publication.add(transcript_data, {"kind": "transcript", "content_type": "application/json"},
                                         parents=parents, role="transcript", provenance={"profile": "estimated-speech-timing/v1"})
            exports = {}
            for format, data in subtitle_data.items():
                output = publication.add(data, {"kind": "subtitles", "content_type": "text/vtt" if format == "vtt" else "application/x-subrip"},
                    parents=[*parents, {"role": "transcript", "asset_id": transcript["asset_id"], "digest": transcript["digest"]}], role=format)
                exports[format] = output["asset_id"]
            return {"transcript_asset_id": transcript["asset_id"], "subtitle_assets": exports,
                    "remote_requests": 1, "warnings": document["warnings"], "limitations": LIMITATIONS}

        return self.store.transact(request["request_id"], binding, produce)


def export_result(store, request_id, format, destination):
    result = store.show_request(request_id)
    if result.get("binding", {}).get("operation") != "voicestudio.transcribe/v1" or result["status"] != "succeeded":
        raise AudioError("transcription_unavailable", "Request is not a completed transcription.")
    asset_id = result["transcript_asset_id"] if format == "json" else result["subtitle_assets"].get(format)
    if not asset_id:
        raise AudioError("subtitles_unavailable", "The service returned no segment timings usable as subtitles; no timing was invented.")
    record, data = store.asset(asset_id)
    path = safe_path(Path(destination))
    if path.is_relative_to(store.root):
        raise AudioError("managed_export_path", "Export outside the managed workspace.")
    # Exclusive creation never overwrites an existing user file.
    stream = path.open("xb")
    created = os.fstat(stream.fileno())
    try:
        with stream:
            if stream.write(data) != len(data):
                raise OSError("Incomplete export write")
    except BaseException:
        # Never remove an existing destination or a replacement installed by
        # another writer after our exclusive create.
        try:
            current = safe_path(path).stat()
            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                path.unlink()
        except (OSError, AudioError):
            pass
        raise
    return {"schema": "matter-transcription-export/v1", "status": "succeeded", "asset_id": asset_id,
            "digest": record["digest"], "path": str(path), "audio_model_calls": 0, "network_requests": 0}
