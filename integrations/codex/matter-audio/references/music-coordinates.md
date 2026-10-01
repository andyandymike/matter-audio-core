# Music coordinates and named sections

Use this route only when the installed ScoreMatter `audio capabilities` exposes
music annotation and planning schemas. It extends the existing audio workspace;
it installs no model and makes no audio-model call. Source audio must already be
registered. The complete request examples live in ScoreMatter's
`docs/shared-audio.md`.

## Bind musical meaning to a saved version

`music annotate --request <json>` saves an immutable annotation asset that refers
to the exact source audio, including its digest and media facts. Record the actual
source of the timing/section declarations. User-supplied, project-supplied and
agent-proposed markings are not interchangeable. A prompt asking for 120 BPM is
not a measurement that the generated WAV follows 120 BPM.

Fixed timing declares BPM as an exact decimal string, the BPM note unit as a
fraction of a whole note, meter and the first-beat frame. Thus a quarter-note
pulse is 1/4 and a dotted-quarter pulse is 3/8. Bar and beat positions start at 1;
the position's beat unit follows the meter denominator, not an inferred compound
pulse. Use the installed schema for bounds and allowed values.

For free or unknown timing, use explicit seconds or frame positions and named
regions. Do not invent a grid to satisfy a schema. Sections such as intro, loop
and outro are declarations, not automatically detected musical boundaries.

Coordinates resolve from an absolute origin using exact rational arithmetic,
followed by one half-up conversion to frames. Ranges are start-inclusive and
end-exclusive. Inspect the actual frames and reported quantization error before
using them. Invalid, collapsed and out-of-bounds regions are rejected.

Annotations never change the raw WAV, generation record, musical intent or
listening feedback. Revised annotations are separate assets. A derivative WAV
needs a newly bound annotation; do not silently reuse coordinates from its parent.

## Plan, inspect, then execute

```text
python <skill-dir>/scripts/run_audio.py --product score -- music annotate --request annotations.json
python <skill-dir>/scripts/run_audio.py --product score -- music show <annotation-asset-id>
python <skill-dir>/scripts/run_audio.py --product score -- music plan --request music-plan.json
python <skill-dir>/scripts/run_audio.py --product score -- music show <plan-asset-id>
python <skill-dir>/scripts/run_audio.py --product score -- music execute <plan-asset-id>
```

The launcher resolves request paths from the consuming project's cwd. IDs passed
to `show` or `execute` are immutable asset IDs, not file paths. A plan binds the
annotation and audio identities to a frozen Core request and execution ID. It is
saved metadata; creating it does not execute audio processing or alter selection.

Trim and loop plans use a named region. Loop crossfade must be explicit: zero
retains the region's frame length, while F overlap frames shorten the output by F.
Report the actual period; an overlapped four-bar region is not automatically a
four-bar output. Existing PCM protection can reject removed or modified locks.

Protection plans require the selected annotation audio and an observed session
revision. They add the requested regions to the existing locks; they do not
silently replace or clear other protections. Unlocking remains an explicit
separate operation through the existing Core session interface.

Execution delegates to the existing Core operation or session mutation and uses
the frozen execution ID. Query `action show` for audio execution, or
`session request` for protection mutations, with the ID returned by the plan.
Completed executions can be replayed after subsequent session changes. Pending
publication retains the Core recovery boundary; never invent a new ID to conceal
an interrupted attempt. Audio outputs are candidates and need explicit session
selection with the current revision.

## Replace a named section

When the installed plan schema includes `target.kind: splice`, select one base
region and one replacement region from exact saved annotations. For example,
replace the base's `chorus` with the replacement candidate's `chorus`:

```json
{
  "schema": "score-music-plan/v1",
  "request_id": "replace-chorus-001",
  "annotation_id": "BASE_ANNOTATION_ID",
  "region_ids": ["chorus"],
  "target": {
    "kind": "splice",
    "replacement": {
      "annotation_id": "REPLACEMENT_ANNOTATION_ID",
      "region_id": "chorus"
    },
    "transition_frames": 0
  }
}
```

Replace both placeholders with returned annotation asset IDs. Use the existing
`music plan`, `music show`, and `music execute` commands. The frozen plan binds
both annotation/audio references, the full replacement region and the Core
resolution. Changed replacement identity or coordinates cannot reuse the old
execution identity. Inspect both converted windows and their rounding errors.

The two complete regions must have the same **resolved frame count**, sample
rate and channel count. Equal bar counts do not establish this, especially with
different tempo grids. A longer replacement is rejected rather than implicitly
cropped; a shorter one is not padded. Choose compatible audio. To use a smaller
part, create a new annotation that explicitly marks the desired range.

`transition_frames` is explicit: zero performs direct replacement; a positive
value blends inside both ends of the destination window and must fit without
overlap. Core's one-frame transition retains the base sample at that edge.
The output keeps the base's duration and copies all PCM outside the window
exactly. This guarantee does not mean that the musical join will sound natural.

An optional `target.protection` refers to the **base** audio's session policy.
Overlapping protected PCM is rejected before rendering; successful results
include Core's actual change and transition evidence. The replacement asset is
an input, not a new current selection. Results retain both annotation references,
but neither source annotation is automatically attached to the new audio.

This route does not infer beats, stretch time, follow variable tempo, inherit
themes, judge loop naturalness or approve musical quality. Respect deferred
listening and keep structural validation separate from listening evidence.
