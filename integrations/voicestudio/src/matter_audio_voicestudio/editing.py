"""Offline, source-bound transcript selections and immutable PCM edit plans."""
from __future__ import annotations

import unicodedata

from matter_audio_core.actions import ActionService
from matter_audio_core.artifacts import Publication
from matter_audio_core.contracts import (ASSET_PATTERN, MAX_JSON_BYTES, PROTECTION_REF,
    REQUEST_PATTERN, canonical, fingerprint, object_schema, parse_json, validate)
from matter_audio_core.errors import AudioError
from matter_audio_core.fades import FRAME_COUNT
from matter_audio_core.media import decode_wav

from .service import REQUEST_SCHEMA as TRANSCRIPTION_SCHEMA, normalize, subtitles

INDEX = {"type": "integer", "minimum": 1, "maximum": 10000}
UNIT = {"enum": ["word", "segment"]}
SELECTION_SCHEMA = {"oneOf": [
    object_schema({"unit": UNIT, "index": INDEX}),
    object_schema({"unit": UNIT, "first_index": INDEX, "last_index": INDEX}),
    object_schema({"unit": UNIT, "text": {"type": "string", "minLength": 1, "maxLength": 8192, "pattern": r"\S"},
                   "occurrence": INDEX}, ["unit", "text"]),
]}
REQUEST_SCHEMA = object_schema({
    "schema": {"const": "matter-transcript-edit/v1"},
    "request_id": {"type": "string", "pattern": REQUEST_PATTERN},
    "transcription_request_id": {"type": "string", "pattern": REQUEST_PATTERN},
    "transcript_asset_id": {"type": "string", "pattern": ASSET_PATTERN},
    "source_asset_id": {"type": "string", "pattern": ASSET_PATTERN},
    "selection": SELECTION_SCHEMA,
    "padding_frames": object_schema({"before": FRAME_COUNT, "after": FRAME_COUNT}),
    "edit": {"oneOf": [object_schema({"kind": {"const": "extract"}}),
        object_schema({"kind": {"const": "replace"},
                       "replacement_asset_id": {"type": "string", "pattern": ASSET_PATTERN},
                       "replacement_start_frame": FRAME_COUNT, "transition_frames": FRAME_COUNT},
                      ["kind", "replacement_asset_id"])]},
    "protection": PROTECTION_REF,
}, ["schema", "request_id", "transcription_request_id", "transcript_asset_id", "source_asset_id", "selection", "edit"])
OPERATION = "transcript.edit/v1"
PLAN_SCHEMA = "matter-transcript-edit-plan/v1"
PLAN_MEDIA = {"kind": "transcript_edit_plan", "content_type": "application/json"}
MATCHING = "unicode-nfc-whitespace-case-sensitive/v1"
LIMITATIONS = [
    "Transcript times are model estimates; selecting words does not verify their acoustic boundaries.",
    "Entry indices and text occurrences are one-based; first_index/last_index are inclusive. PCM frame ranges are end-exclusive.",
    "Exact entry matching normalizes Unicode NFC and whitespace but preserves case and punctuation; there is no fuzzy search or sentence inference.",
    "Extract keeps the selected range; replace preserves duration and writes only its resolved range. Neither operation deletes a word and closes the gap.",
    "Existing protection is enforced only when its explicit reference is included; no selection or feedback is changed automatically.",
    "Evidence establishes local source relationships, not independent model, licensing or listening attestation.",
]


def _ref(record):
    return {key: record[key] for key in ("asset_id", "digest", "media")}


def _mismatch(message):
    raise AudioError("transcript_source_mismatch", message)


def _source(store, request_id, transcript_id=None, source_id=None):
    """Require a real successful transcription receipt and reconstruct its timing."""
    receipt = store.show_request(request_id)
    if receipt.get("status") != "succeeded":
        _mismatch("Transcription must have a completed successful receipt")
    try:
        binding = receipt["binding"]
        original = binding["request"]
        validate(original, TRANSCRIPTION_SCHEMA)
        if (binding["operation"] != "voicestudio.transcribe/v1" or original["request_id"] != request_id
                or receipt["binding_digest"] != fingerprint(binding)):
            _mismatch("The receipt is not the requested transcription")
        transcript_id = transcript_id or receipt["transcript_asset_id"]
        source_id = source_id or original["asset_id"]
        if transcript_id != receipt["transcript_asset_id"] or source_id != original["asset_id"]:
            _mismatch("Transcript and source must be the exact assets bound by the transcription request")
        transcript_record, raw = store.asset(transcript_id)
        source_record, audio = store.asset(source_id)
        pcm = decode_wav(audio)
        source = {"asset_id": source_id, "digest": source_record["digest"], "media": pcm.facts()}
        parameters = {"timestamps": original.get("timestamps", "word")}
        if "language" in original:
            parameters["language"] = original["language"]
        if (binding["source"] != source or binding["parameters"] != parameters
                or transcript_record != receipt["outputs"][0]
                or transcript_record["role"] != "transcript"
                or transcript_record["media"] != {"kind": "transcript", "content_type": "application/json"}
                or transcript_record["parents"] != [{"role": "transcription_source", "asset_id": source_id,
                                                      "digest": source_record["digest"]}]):
            _mismatch("Transcription output metadata or source identity differs from the saved evidence")
        document = parse_json(raw)
        # Recreate the normalized document from its original provider estimates.
        # Edited frame indices, precision claims or hidden fields cannot become
        # trusted coordinates just because the surrounding JSON is valid.
        response = {"text": document["text"], "language": document["language"],
                    "model": document["provider_reported_model"]}
        for collection, text_key in (("words", "word"), ("segments", "text")):
            response[collection] = [{text_key: item["text"], "start": item["provider_start_seconds"],
                                     "end": item["provider_end_seconds"]} for item in document[collection]]
        expected = normalize(response, pcm, source, binding["service"], parameters)
        if expected["segments"] and not subtitles(expected, "srt"):
            expected["warnings"].append("no_usable_subtitle_cues")
        if document != expected or receipt["warnings"] != document["warnings"]:
            _mismatch("Transcript coordinates and warnings do not reconstruct from the original timing estimates")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AudioError("transcript_source_mismatch", "Incomplete or malformed transcription evidence") from exc
    return {"receipt": {"request_id": request_id, "group_id": receipt["group_id"], "digest": fingerprint(receipt)},
            "transcript": _ref(transcript_record), "source": _ref(source_record)}, document, pcm


def inspect_transcript(store, transcription_request_id, unit="word", offset=0, limit=50):
    if unit not in ("word", "segment") or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 128:
        raise AudioError("invalid_selection", "Use word/segment, a nonnegative offset and a limit from 1 to 128")
    evidence, document, _ = _source(store, transcription_request_id)
    entries = document["words" if unit == "word" else "segments"]
    items = [{"index": index + 1, **entry} for index, entry in enumerate(entries[offset:offset + limit], offset)]
    # Rich word text can make even 128 entries exceed the JSON response budget.
    # Paginate further rather than emitting a response that readers cannot open.
    result = {"schema": "matter-transcript-inspection/v1", "status": "succeeded", **evidence,
              "unit": unit, "offset": offset, "limit": limit, "total_items": len(entries), "items": items,
              "warnings": document["warnings"], "timing": document["timing"], "matching": MATCHING,
              "audio_model_calls": 0, "network_requests": 0, "limitations": LIMITATIONS}
    while True:
        result["next_offset"] = offset + len(items) if offset + len(items) < len(entries) else None
        if len(canonical(result)) <= MAX_JSON_BYTES:
            break
        if len(items) <= 1:
            raise AudioError("json_too_large", "Transcript inspection cannot fit a progressing page within 1 MiB")
        items.pop()
    return result


def _text(value):
    return unicodedata.normalize("NFC", " ".join(value.split()))


def _selection(document, request, pcm):
    selector = request["selection"]
    entries = document["words" if selector["unit"] == "word" else "segments"]
    if not entries:
        raise AudioError("selection_unavailable", "The transcript has no timings for the requested unit; no boundary was invented")
    if "text" in selector:
        matches = [index + 1 for index, entry in enumerate(entries) if _text(entry["text"]) == _text(selector["text"])]
        if not matches or selector.get("occurrence", 1) > len(matches):
            raise AudioError("selection_not_found", "No matching entry at the requested occurrence")
        if len(matches) != 1 and "occurrence" not in selector:
            raise AudioError("selection_ambiguous", "Repeated text needs an explicit one-based occurrence", details={
                "matching_indices": matches[:32], "total_matches": len(matches), "truncated": len(matches) > 32})
        first = last = matches[selector.get("occurrence", 1) - 1]
    else:
        first = selector.get("index", selector.get("first_index"))
        last = selector.get("index", selector.get("last_index"))
    if not 1 <= first <= last <= len(entries) or last - first + 1 > 128:
        raise AudioError("invalid_selection", "Select an ordered range of 1 to 128 existing entries")
    selected = [{"index": index + 1, **entry} for index, entry in enumerate(entries[first - 1:last], first - 1)]
    if any(entry["end_frame"] <= entry["start_frame"] for entry in selected):
        raise AudioError("invalid_selection", "Every selected entry must have a nonempty estimated time range")
    start, end = min(item["start_frame"] for item in selected), max(item["end_frame"] for item in selected)
    padding = request.get("padding_frames", {"before": 0, "after": 0})
    padded_start, padded_end = start - padding["before"], end + padding["after"]
    if not 0 <= padded_start < padded_end <= pcm.frames:
        raise AudioError("invalid_padding", "Explicit padding must fit inside the source; it is never silently clipped")
    overlaps = [index + 1 for index, entry in enumerate(entries)
                if not first <= index + 1 <= last and entry["start_frame"] < padded_end and padded_start < entry["end_frame"]]
    return {"unit": selector["unit"], "indices": list(range(first, last + 1)), "entries": selected,
            "observed_range": {"start_frame": start, "end_frame": end},
            "range": {"start_frame": padded_start, "end_frame": padded_end}, "padding_frames": padding,
            "matching": MATCHING, "envelope": "minimum-start-maximum-end",
            "overlap_with_unselected": {"count": len(overlaps), "indices": overlaps[:16], "truncated": len(overlaps) > 16},
            "timing_kind": "model_estimate", "precision_verified": False}


def _binding(store, request):
    validate(request, REQUEST_SCHEMA)
    evidence, document, pcm = _source(store, request["transcription_request_id"], request["transcript_asset_id"], request["source_asset_id"])
    selection = _selection(document, request, pcm)
    if request["edit"]["kind"] == "replace":
        replacement, _ = store.asset(request["edit"]["replacement_asset_id"])
        evidence["replacement"] = _ref(replacement)
    return {"operation": OPERATION, "request": request, "evidence": evidence, "selection": selection}


def _core_request(binding):
    request = binding["request"]
    edit = request["edit"]
    result = {"schema": "matter-action/v1", "request_id": "transcript-edit-" + fingerprint(binding)["hex"],
              "operation": "trim/v1" if edit["kind"] == "extract" else "splice/v1",
              "inputs": [request["source_asset_id"]], "parameters": dict(binding["selection"]["range"])}
    if edit["kind"] == "replace":
        result["inputs"].append(edit["replacement_asset_id"])
        result["parameters"].update(replacement_start_frame=edit.get("replacement_start_frame", 0),
                                    transition_frames=edit.get("transition_frames", 0))
    if "protection" in request:
        result["protection"] = request["protection"]
    return result


def _document(binding, resolution):
    return {"schema": PLAN_SCHEMA, "binding": binding, "core_request": _core_request(binding),
            "core_resolution": resolution, "expected_resolution_digest": resolution["digest"]["hex"],
            "audio_model_calls": 0, "network_requests": 0, "limitations": LIMITATIONS}


def _parents(binding):
    return [{"role": role, "asset_id": binding["evidence"][role]["asset_id"], "digest": binding["evidence"][role]["digest"]}
            for role in ("source", "transcript", "replacement") if role in binding["evidence"]]


def plan(store, request):
    binding = _binding(store, request)
    try:
        previous = store.show_request(request["request_id"])
    except AudioError as exc:
        if exc.code != "request_not_found":
            raise
    else:
        if previous["binding"] != binding:
            raise AudioError("request_conflict", "Plan request ID already binds another transcript selection or edit")
        return previous
    resolution = ActionService(store).resolve(_core_request(binding))
    document = _document(binding, resolution)
    data = canonical(document)
    if len(data) > MAX_JSON_BYTES:
        raise AudioError("json_too_large", "Transcript edit plan exceeds 1 MiB; select fewer entries")

    def produce(publication):
        asset = publication.add(data, PLAN_MEDIA, role="transcript_edit_plan", parents=_parents(binding))
        return {"plan": asset, "document": document, "plan_digest": asset["digest"]["hex"],
                "audio_model_calls": 0, "network_requests": 0, "limitations": LIMITATIONS}

    preview = Publication("0" * 32)
    store.validate_publication(request["request_id"], binding, preview, produce(preview))
    return store.transact(request["request_id"], binding, produce)


def _load(store, asset_id):
    record, raw = store.asset(asset_id)
    if record["role"] != "transcript_edit_plan" or record["media"] != PLAN_MEDIA:
        raise AudioError("invalid_edit_plan", "Expected a saved transcript edit plan asset")
    document = parse_json(raw)
    try:
        binding = _binding(store, document["binding"]["request"])
        resolution = document["core_resolution"]
        if (record["parents"] != _parents(binding) or document != _document(binding, resolution)
                or resolution["request"] != _core_request(binding)
                or resolution["digest"] != fingerprint({key: value for key, value in resolution.items() if key != "digest"})):
            raise AudioError("edit_plan_mismatch", "Plan fields differ from the verified sources and frozen request")
        owner = store.show_request(binding["request"]["request_id"])
        if (owner["status"] != "succeeded" or owner["binding"] != binding or owner["plan"] != record
                or owner["document"] != document or owner["outputs"] != [record]
                or owner["plan_digest"] != record["digest"]["hex"]):
            raise AudioError("edit_plan_mismatch", "Plan does not belong to its claimed publication receipt")
        # Readability/replay must not depend on today's locks. Reconstruct PCM
        # semantics without protection; only a new execution checks current locks.
        unprotected = {key: value for key, value in resolution["request"].items() if key != "protection"}
        checked = ActionService(store).resolve(unprotected)
        keys = {"schema", "request", "inputs", "profile", "effective_parameters", "audio_model_calls", "unverified_goals", "digest"}
        if "protection" in resolution["request"]:
            keys.add("protection")
        if (set(resolution) != keys or any(resolution[key] != checked[key] for key in (
                "schema", "inputs", "profile", "effective_parameters", "audio_model_calls", "unverified_goals"))):
            raise AudioError("edit_plan_mismatch", "Frozen resolution does not match the verified PCM inputs")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AudioError("invalid_edit_plan", "Incomplete or malformed transcript edit plan") from exc
    return record, document


def show(store, asset_id):
    record, document = _load(store, asset_id)
    return {"schema": "matter-transcript-edit-document/v1", "status": "succeeded", "asset": record,
            "document": document, "plan_digest": record["digest"]["hex"], "audio_model_calls": 0, "network_requests": 0}


def execute(store, asset_id, expected_plan_digest):
    record, document = _load(store, asset_id)
    if record["digest"]["hex"] != expected_plan_digest:
        raise AudioError("plan_digest_conflict", "Inspect the saved plan and execute its exact digest")
    request = document["core_request"]
    try:
        result = store.show_request(request["request_id"])
    except AudioError as exc:
        if exc.code != "request_not_found":
            raise
        result = ActionService(store).execute(request, expected_resolution_digest=document["expected_resolution_digest"])
    else:
        if result["binding"] != {"resolution": document["core_resolution"]}:
            raise AudioError("request_conflict", "Execution request already binds a different resolution")
    return {**result, "edit_plan": _ref(record), "selection": document["binding"]["selection"],
            "network_requests": 0}


def capabilities():
    return {"availability": "available", "request_schema": REQUEST_SCHEMA, "document_schema": PLAN_SCHEMA,
            "commands": ["transcribe inspect", "edit plan", "edit show", "edit execute"],
            "offline": True, "selection_index_origin": 1, "selection_range_end": "inclusive",
            "frame_range_end": "exclusive", "matching": MATCHING, "edits": ["extract", "replace"],
            "limitations": LIMITATIONS}
