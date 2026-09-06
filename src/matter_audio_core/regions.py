"""Exact PCM locks anchored to immutable assets and projected through verified lineage."""

from __future__ import annotations

import struct
import uuid

from .contracts import digest, object_schema, validate
from .errors import AudioError
from .execution import checkpoint
from .fades import FRAME_COUNT, SECONDS, seconds_to_frames
from .media import decode_wav

RANGE_SCHEMA = {"oneOf": [object_schema({"start_frame": FRAME_COUNT, "end_frame": FRAME_COUNT}),
                          object_schema({"start_seconds": SECONDS, "end_seconds": SECONDS})]}
MAX_LINEAGE_DEPTH = 128


def region_digest(pcm, start, end):
    width = pcm.channels * 2
    return digest(struct.pack("<IH", pcm.sample_rate, pcm.channels) + pcm.payload[start * width:end * width])


def make_constraints(store, asset, ranges):
    policy = {"schema": "matter-pcm-constraints/v1", "constraint_id": "c_" + uuid.uuid4().hex,
              "anchor_asset": None, "regions": []}
    if not ranges:
        return policy
    if asset is None:
        raise AudioError("selection_required", "Select audio before protecting a region")
    record, data = store.asset(asset["asset_id"])
    pcm = decode_wav(data)
    policy["anchor_asset"] = {key: record[key] for key in ("asset_id", "digest", "media")}
    previous_end = 0
    for region in ranges:
        validate(region, RANGE_SCHEMA)
        start, end = ((region["start_frame"], region["end_frame"]) if "start_frame" in region else
                      (seconds_to_frames(region["start_seconds"], pcm.sample_rate),
                       seconds_to_frames(region["end_seconds"], pcm.sample_rate)))
        if not previous_end <= start < end <= pcm.frames:
            raise AudioError("invalid_region", "Regions must be nonempty, ordered, disjoint and inside the source")
        policy["regions"].append({"start_frame": start, "end_frame": end,
                                  "pcm_digest": region_digest(pcm, start, end)})
        previous_end = end
    return policy


def map_regions(regions, mapping, source_frames, output_frames):
    if not isinstance(mapping, dict):
        raise AudioError("constraint_mapping_unavailable", "Operation has no supported frame mapping")
    count = mapping.get("frame_count")
    if type(count) is not int or count != output_frames or count <= 0:
        raise AudioError("constraint_mapping_unavailable", "Invalid mapping frame count")
    if mapping.get("kind") == "identity" and source_frames == output_frames:
        offset = 0
    elif (mapping.get("kind") == "slice" and type(mapping.get("output_start_frame")) is int
          and mapping["output_start_frame"] == 0):
        offset = mapping.get("source_start_frame")
        if type(offset) is not int or not 0 <= offset < offset + count <= source_frames:
            raise AudioError("constraint_mapping_unavailable", "Invalid source slice mapping")
    else:
        raise AudioError("constraint_mapping_unavailable", "Only identity and exact slice mappings are supported")
    mapped = []
    for region in regions:
        start, end = region["start_frame"] - offset, region["end_frame"] - offset
        if not 0 <= start < end <= output_frames:
            raise AudioError("constraint_violation", "Operation would remove a protected region",
                             details={"region": region, "time_mapping": mapping})
        mapped.append({**region, "start_frame": start, "end_frame": end})
    return mapped


def verify_regions(pcm, regions):
    for region in regions:
        if not 0 <= region["start_frame"] < region["end_frame"] <= pcm.frames:
            raise AudioError("constraint_violation", "Protected region is outside the output")
        if region_digest(pcm, region["start_frame"], region["end_frame"]) != region["pcm_digest"]:
            raise AudioError("constraint_violation", "Protected PCM samples changed", details={"region": region})


def project_constraints(store, policy, asset_id):
    if not policy or not policy["regions"]:
        return []
    if asset_id is None:
        raise AudioError("constraint_violation", "Cannot clear a selection while retaining PCM locks")
    anchor = policy["anchor_asset"]
    target, data = store.asset(asset_id)
    pcm = decode_wav(data)
    if any(target["media"][key] != anchor["media"][key] for key in ("sample_rate_hz", "channels", "channel_layout")):
        raise AudioError("constraint_violation", "Protected audio format changed")
    current = target
    edges, seen = [], set()
    while current["asset_id"] != anchor["asset_id"]:
        checkpoint()
        if current["asset_id"] in seen or len(edges) >= MAX_LINEAGE_DEPTH:
            raise AudioError("constraint_mapping_unavailable", "Cyclic or excessively deep audio lineage")
        seen.add(current["asset_id"])
        parents = [parent for parent in current["parents"] if parent.get("role") == "source"]
        if len(parents) != 1:
            raise AudioError("constraint_mapping_unavailable", "Candidate has no unique path to the protected anchor")
        parent, _ = store.asset(parents[0]["asset_id"])
        if parent["digest"] != parents[0]["digest"]:
            raise AudioError("integrity_error", "Audio lineage parent digest changed")
        if any(current["media"].get(key) != parent["media"].get(key) for key in ("sample_rate_hz", "channels", "channel_layout")):
            raise AudioError("constraint_violation", "Lineage changes the protected PCM format")
        group = store._load_group(current["asset_id"].split("_")[1])
        maps = [finding["time_mapping"] for finding in group.get("findings", [])
                if finding.get("source_asset_id") == parent["asset_id"] and "time_mapping" in finding]
        if len(maps) != 1:
            raise AudioError("constraint_mapping_unavailable", "Candidate has no unambiguous recorded frame mapping")
        edges.append((maps[0], parent["media"]["frame_count"], current["media"]["frame_count"]))
        current = parent
    if {key: current[key] for key in ("asset_id", "digest", "media")} != anchor:
        raise AudioError("integrity_error", "Constraint anchor no longer matches the source asset")
    regions = [{**region, "anchor_start_frame": region["start_frame"], "anchor_end_frame": region["end_frame"]}
               for region in policy["regions"]]
    for mapping, source_frames, output_frames in reversed(edges):
        regions = map_regions(regions, mapping, source_frames, output_frames)
    verify_regions(pcm, regions)
    return regions


def check_plan(regions, mapping, writes, source_frames):
    if not regions:
        return []
    if not isinstance(mapping, dict) or not isinstance(writes, list):
        raise AudioError("constraint_mapping_unavailable", "Operation does not declare its frame mapping and write ranges")
    mapped = map_regions(regions, mapping, source_frames, mapping.get("frame_count"))
    for changed in writes:
        if not isinstance(changed, dict):
            raise AudioError("constraint_mapping_unavailable", "Operation declared an invalid write range")
        start, end = changed.get("start_frame"), changed.get("end_frame")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= source_frames:
            raise AudioError("constraint_mapping_unavailable", "Operation declared an invalid write range")
        for region in regions:
            if max(start, region["start_frame"]) < min(end, region["end_frame"]):
                raise AudioError("constraint_violation", "Operation writes into a protected region",
                                 details={"region": region, "write_range": changed})
    return mapped
