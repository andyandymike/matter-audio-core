"""Explicit local metadata indexes and transparent numeric similarity ranking."""

import math

from .analysis import describe
from .contracts import object_schema, validate
from .documents import Documents, snapshot, verify_snapshot
from .errors import AudioError
from .execution import checkpoint
from .media import MAX_AUDIO_BYTES, decode_wav
from .session_contracts import ASSET_ID, IDENTIFIER, NAME

TAGS = {"type": "array", "maxItems": 24, "uniqueItems": True,
        "items": {"type": "string", "minLength": 1, "maxLength": 64}}
LIBRARY_SCHEMA = object_schema({"schema": {"const": "matter-library/v1"}, "library_id": IDENTIFIER, "name": NAME,
    "entries": {"type": "array", "minItems": 1, "maxItems": 128,
        "items": object_schema({"asset_id": ASSET_ID, "name": NAME, "tags": TAGS})}})
SEARCH_SCHEMA = object_schema({"schema": {"const": "matter-library-search/v1"}, "library_id": IDENTIFIER,
    "text": {"type": "string", "maxLength": 240}, "tags": TAGS, "similar_to": ASSET_ID,
    "min_seconds": {"type": "number", "minimum": 0, "maximum": 86400},
    "max_seconds": {"type": "number", "minimum": 0, "maximum": 86400},
    "min_rms_dbfs": {"type": "number", "minimum": -120, "maximum": 0},
    "max_rms_dbfs": {"type": "number", "minimum": -120, "maximum": 0},
    "channels": {"enum": [1, 2]}, "sample_rate_hz": {"type": "integer", "minimum": 8000, "maximum": 192000},
    "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
    ["schema", "library_id"])


def distance(left, right):
    # Equal weight, normalized engineering features; each contribution is exposed.
    fields = {"duration": (math.log2(max(left["media"]["duration_seconds"], .001)), math.log2(max(right["media"]["duration_seconds"], .001)), 4),
        "rms": (left["rms_dbfs"] if left["rms_dbfs"] is not None else -120, right["rms_dbfs"] if right["rms_dbfs"] is not None else -120, 60),
        "crest": (left["crest_db"] or 0, right["crest_db"] or 0, 30),
        "zero_crossings": (left["zero_crossing_rate"], right["zero_crossing_rate"], 1),
        "activity": (left["active_frame_fraction"], right["active_frame_fraction"], 1),
        "spectral_centroid": (left["spectral_centroid_hz"] or 0, right["spectral_centroid_hz"] or 0, 10000)}
    components = {name: min(1., abs(a - b) / scale) for name, (a, b, scale) in fields.items()}
    return sum(components.values()) / len(components), components


class LibraryService:
    def __init__(self, store):
        self.store, self.documents = store, Documents(store, "libraries")

    def create(self, request):
        validate(request, LIBRARY_SCHEMA)
        ids = [entry["asset_id"] for entry in request["entries"]]
        if len(set(ids)) != len(ids):
            raise AudioError("duplicate_asset", "Each library asset must appear once")
        def produce():
            entries, total = [], 0
            for entry in request["entries"]:
                checkpoint()
                asset, data = snapshot(self.store, entry["asset_id"])
                total += len(data)
                if total > MAX_AUDIO_BYTES:
                    raise AudioError("input_limit", "Library snapshot is limited to 64 MiB of input WAV data")
                entries.append({**entry, "asset": asset, "analysis": describe(decode_wav(data))})
            return {"entries": entries, "total_input_bytes": total, "search_method": "metadata-and-numeric-distance/v1"}, {}
        return self.documents.create(request["library_id"], request, produce)

    def show(self, library_id):
        value = self.documents.show(library_id)
        for entry in value["body"]["entries"]:
            checkpoint()
            verify_snapshot(self.store, entry["asset"])
        return value

    def search(self, request):
        validate(request, SEARCH_SCHEMA)
        for lower, upper in (("min_seconds", "max_seconds"), ("min_rms_dbfs", "max_rms_dbfs")):
            if lower in request and upper in request and request[lower] > request[upper]:
                raise AudioError("invalid_range", "Search minimum exceeds maximum")
        entries = self.show(request["library_id"])["body"]["entries"]
        target = None
        if "similar_to" in request:
            target = next((entry["analysis"] for entry in entries if entry["asset_id"] == request["similar_to"]), None)
            if target is None:
                raise AudioError("asset_not_indexed", "Similarity reference must belong to the same measured library")
        results = []
        terms = request.get("text", "").casefold().split()
        for entry in entries:
            analysis, media = entry["analysis"], entry["asset"]["media"]
            haystack = " ".join([entry["name"], *entry["tags"]]).casefold()
            tags = {tag.casefold() for tag in entry["tags"]}
            if not all(term in haystack for term in terms) or not all(tag.casefold() in tags for tag in request.get("tags", [])):
                continue
            if any(key in request and media[key] != request[key] for key in ("channels", "sample_rate_hz")):
                continue
            duration, rms = media["duration_seconds"], analysis["rms_dbfs"]
            if not request.get("min_seconds", 0) <= duration <= request.get("max_seconds", 86400):
                continue
            if ("min_rms_dbfs" in request or "max_rms_dbfs" in request) and (rms is None or not request.get("min_rms_dbfs", -120) <= rms <= request.get("max_rms_dbfs", 0)):
                continue
            score, components = distance(analysis, target) if target else (0., {})
            results.append({"asset_id": entry["asset_id"], "name": entry["name"], "tags": entry["tags"],
                "media": media, "rms_dbfs": rms, "distance": score, "distance_components": components})
        results.sort(key=lambda item: (item["distance"], item["asset_id"]))
        offset, limit = request.get("offset", 0), request.get("limit", 20)
        return {"schema": "matter-library-results/v1", "status": "succeeded", "request": request,
            "items": results[offset:offset + limit], "total_matches": len(results),
            "next_offset": offset + limit if offset + limit < len(results) else None,
            "audio_model_calls": 0, "method": "metadata-and-numeric-distance/v1",
            "limitations": ["Text matches explicit names/tags. Distance describes measured features, not semantic or perceptual similarity."]}
