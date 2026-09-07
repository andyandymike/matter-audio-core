"""Versioned cue/variant snapshots and exact, portable batch delivery."""

from .arrangement import KEY
from .contracts import fingerprint, object_schema, validate
from .documents import Documents, snapshot, verify_snapshot
from .errors import AudioError
from .execution import checkpoint
from .media import MAX_AUDIO_BYTES, decode_wav, inspect
from .session_contracts import ASSET_ID, IDENTIFIER, NAME, REVISION

LOOP = object_schema({"begin_frame": {"type": "integer", "minimum": 0},
                     "end_frame": {"type": "integer", "minimum": 1}})
VARIANT = object_schema({"key": KEY, "asset_id": ASSET_ID, "loop": LOOP,
    "selection": object_schema({"session_id": IDENTIFIER, "revision": REVISION})}, ["key", "asset_id"])
CUE = object_schema({"key": KEY, "name": NAME, "selected_variant": KEY,
    "variants": {"type": "array", "minItems": 1, "maxItems": 8, "items": VARIANT}})
SET_SCHEMA = object_schema({"schema": {"const": "matter-cue-set/v1"}, "set_id": IDENTIFIER, "name": NAME,
    "supersedes": IDENTIFIER, "cues": {"type": "array", "minItems": 1, "maxItems": 64, "items": CUE}},
    ["schema", "set_id", "name", "cues"])
DELIVERY_SCHEMA = object_schema({"schema": {"const": "matter-cue-export/v1"}, "request_id": IDENTIFIER,
    "set_id": IDENTIFIER, "variants": {"enum": ["selected", "all"]}})


class CueSetService:
    def __init__(self, store):
        self.store = store
        self.documents, self.deliveries = Documents(store, "cue-sets"), Documents(store, "cue-exports")

    def create(self, request):
        validate(request, SET_SCHEMA)
        if sum(len(cue["variants"]) for cue in request["cues"]) > 128:
            raise AudioError("variant_limit", "A cue set supports at most 128 variants")
        def produce():
            parent = self.show(request["supersedes"]) if "supersedes" in request else None
            if request.get("supersedes") == request["set_id"]:
                raise AudioError("invalid_parent", "A set cannot supersede itself")
            cues, keys, total, levels = [], set(), 0, []
            for cue in request["cues"]:
                variant_keys = [item["key"] for item in cue["variants"]]
                if cue["key"] in keys or len(set(variant_keys)) != len(variant_keys) or cue["selected_variant"] not in variant_keys:
                    raise AudioError("invalid_cue", "Cue/variant keys must be distinct and selection must exist")
                keys.add(cue["key"])
                variants = []
                for variant in cue["variants"]:
                    checkpoint()
                    asset, data = snapshot(self.store, variant["asset_id"])
                    total += len(data)
                    if total > MAX_AUDIO_BYTES:
                        raise AudioError("input_limit", "Cue set WAV contents exceed 64 MiB")
                    pcm = decode_wav(data)
                    if "loop" in variant and not 0 <= variant["loop"]["begin_frame"] < variant["loop"]["end_frame"] <= pcm.frames:
                        raise AudioError("invalid_loop", "Loop coordinates must fit the exact variant WAV")
                    if "selection" in variant:
                        from .sessions import SessionService, revision_record
                        sessions = SessionService(self.store)
                        ref = variant["selection"]
                        with sessions.database.transaction() as connection:
                            revision = revision_record(sessions._revision(connection, ref["session_id"], ref["revision"]))
                        if revision["selected_asset"] != asset:
                            raise AudioError("selection_conflict", "Cue variant differs from the named saved selection")
                        from .regions import project_constraints
                        project_constraints(self.store, revision["constraints"], asset["asset_id"])
                    measured = inspect(pcm, window_frames=pcm.frames, limit=1)["levels"]
                    variants.append({**variant, "asset": asset, "levels": measured})
                    levels.append(measured["rms_dbfs"])
                cues.append({**cue, "variants": variants})
            finite = [value for value in levels if value is not None]
            return {"cues": cues, "total_wav_bytes": total, "supersedes_digest": fingerprint(parent) if parent else None,
                "rms_spread_db": max(finite) - min(finite) if finite else None,
                "acceptance": "not_evaluated", "rights": "source_eligibility_not_inferred"}, {}
        return self.documents.create(request["set_id"], request, produce)

    def show(self, set_id):
        value = self.documents.show(set_id)
        for cue in value["body"]["cues"]:
            for variant in cue["variants"]:
                checkpoint()
                verify_snapshot(self.store, variant["asset"])
        return value

    def export(self, request):
        validate(request, DELIVERY_SCHEMA)
        def produce():
            cue_set = self.show(request["set_id"])
            files, entries = {}, []
            for cue in cue_set["body"]["cues"]:
                for variant in cue["variants"]:
                    if request["variants"] == "selected" and variant["key"] != cue["selected_variant"]:
                        continue
                    name = cue["key"] + "__" + variant["key"] + ".wav"
                    if name in files:
                        raise AudioError("filename_conflict", "Cue and variant keys produce the same filename")
                    files[name] = verify_snapshot(self.store, variant["asset"])
                    entries.append({"cue": cue["key"], "variant": variant["key"], "filename": name,
                        "asset": variant["asset"], "loop": variant.get("loop"), "selection": variant.get("selection")})
            return {"set_id": request["set_id"], "set_digest": fingerprint(cue_set), "entries": entries,
                "loop_metadata": "sidecar_end_exclusive_frames", "acceptance": "not_evaluated"}, files
        result = self.deliveries.create(request["request_id"], request, produce)
        return {**result, "directory": str(self.deliveries.directory(request["request_id"]))}

    def export_show(self, request_id):
        return {**self.deliveries.show(request_id), "directory": str(self.deliveries.directory(request_id))}
