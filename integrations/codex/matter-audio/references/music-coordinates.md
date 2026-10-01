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

## Assemble a new sequential structure

When music capabilities expose `arrange`, use existing named source regions to
build an ordered sequence. For example, intro, theme twice, then outro:

```json
{
  "schema": "score-music-arrange/v1",
  "request_id": "intro-theme-outro-001",
  "segments": [
    {
      "id": "opening",
      "annotation_id": "INTRO_ANNOTATION_ID",
      "region_id": "intro",
      "repeat": 1
    },
    {
      "id": "theme",
      "annotation_id": "THEME_ANNOTATION_ID",
      "region_id": "theme",
      "repeat": 2
    },
    {
      "id": "ending",
      "annotation_id": "OUTRO_ANNOTATION_ID",
      "region_id": "outro",
      "repeat": 1
    }
  ]
}
```

Substitute actual annotation IDs; several segments can use the same annotation
or source audio. Segment IDs must be distinct, and each segment explicitly names
its positive integer repeat count.

```text
python <skill-dir>/scripts/run_audio.py --product score -- music arrange --request arrangement.json
python <skill-dir>/scripts/run_audio.py --product score -- music show <plan-asset-id>
python <skill-dir>/scripts/run_audio.py --product score -- music execute <plan-asset-id>
```

`arrange` only publishes a plan. It freezes all source annotations, exact audio
identities, complete source regions and a `scene/v1` request. The expanded
timeline records every occurrence's segment reference, zero-based repeat index
and output start/end frames. Use the referenced segment's saved source range and
coordinate errors to inspect the mapping. Equal audio bytes with different asset
IDs remain distinct inputs. Repetition uses actual source frames, including a
previously constructed loop's shortened period if that output is the source.

In v1 the first segment starts at output frame zero. Each occurrence follows the
previous one without gaps or overlap; total duration is the sum of the actual
source lengths times their repeat counts. No level adjustment, implicit fade,
crossfade, padding, tempo fitting or resampling is applied. Sample rate and
channels must match. A sequence can use free-time regions or different source
grids, but this does not establish one global beat grid or a natural musical join.

Check installed limits before planning a large sequence. Unsupported fields,
format mismatches and capacity excesses fail before publication. Changed order,
repetitions or source identities require a new request ID. Completed requests
replay their saved receipts; interrupted claims stay `recovery_pending`.

This output has a new timeline. Source annotations and PCM locks are not
inherited, and protection/session parameters are explicitly unsupported rather
than ignored. Do not use arrangement to bypass a user's preservation requirement.
Source audio, sessions and feedback remain unchanged. The new WAV is a candidate;
select it explicitly and establish new annotations or protection where needed.

This route does not infer beats, stretch time, follow variable tempo, inherit
themes, judge loop naturalness or approve musical quality. Respect deferred
listening and keep structural validation separate from listening evidence.

## Request explicit transitions

When capabilities advertise `score-music-arrange/v2`, use the same `segments`
array with schema `score-music-arrange/v2`, a new request ID and a required
`transitions` array. An empty array requests hard cuts. Each entry names the
occurrence **after which** to blend into the next occurrence, for example:

```json
{
  "after_segment_id": "theme",
  "after_repeat_index": 1,
  "crossfade_frames": 12000
}
```

This entry blends the end of the second `theme` occurrence into the next
occurrence. At 48 kHz, 12000 frames is 250 ms; use the actual source sample rate
when translating a user's duration. Repeat indices start at zero. No entry means
no transition at that boundary, including boundaries between repetitions.
The final occurrence has no following boundary. Transition lengths are explicit
integers of at least two frames; duplicate or missing boundaries are rejected.

The renderer overlaps the existing tail and head and applies linear fades.
It uses no extra handles, implicit padding, time stretching or beat alignment.
The total output length is the sum of all occurrence lengths minus the requested
overlaps. Inspect the plan's resolved transitions and occurrence ranges before
execution. Each occurrence's incoming and outgoing overlaps must fit together
within its source length; there is no three-way overlap. Compiling different
occurrence fades can require more Core event definitions than a simple repeat.
Core's 128-definition/1024-occurrence limits and the complete receipt size budget
still apply, and excesses fail before a plan is claimed.

Use the existing `music show` and `music execute` commands. This is a distinct
versioned plan; v1 requests and previously saved plans retain their identities
and hard-cut behavior. Exact processing does not establish a natural musical
transition or listening approval.

## Mark the completed arrangement and continue editing

Request new annotations only after the arrangement's audio has completed.
For example, save this as `arrangement-marks.json`:

```json
{
  "schema": "score-music-annotate-arrangement/v1",
  "request_id": "arrangement-marks-001",
  "plan_id": "ARRANGEMENT_PLAN_ASSET_ID",
  "source": "agent",
  "regions": [
    {
      "id": "theme-second-body",
      "segment_id": "theme",
      "repeat_index": 1,
      "range": "body"
    },
    {
      "id": "outro-full",
      "segment_id": "ending",
      "repeat_index": 0,
      "range": "full"
    }
  ]
}
```

```text
python <skill-dir>/scripts/run_audio.py --product score -- music annotate-arrangement --request arrangement-marks.json
python <skill-dir>/scripts/run_audio.py --product score -- music show <new-annotation-asset-id>
```

Replace the plan ID and segment names with exact saved values. `source` describes
who supplies these new labels; do not represent agent-created labels as user
feedback. Each request selects 1–128 distinct occurrences with unique region IDs.
Larger arrangements can be marked in separate explicit requests. `full` includes
the entire output interval of that occurrence, including any blended neighbors.
`body` excludes its complete incoming and outgoing overlaps. An empty body is
rejected. In a v1 arrangement the two choices name the same interval.

This command verifies the saved plan, completed action and exact output audio,
then publishes new metadata without rendering audio or changing any session.
Missing or interrupted execution must be handled through the existing action
workflow, never by silently running it from the annotation command. The new
annotation retains provenance and source-to-output mapping and uses frame
coordinates with unknown musical timing. Do not copy a source BPM/grid or PCM
lock onto the new timeline.

The new annotation is usable by existing `music plan` and `music arrange`.
For a complete continued-editing workflow:

1. Register the existing candidates and annotate their source regions.
2. Plan the sequence and explicit transitions, inspect it, and execute it.
3. Mark the completed output's requested occurrences using `full` or `body`.
4. Create a new session and explicitly select that output using its observed
   revision. Establish new locks only for the user's preservation requirements.
5. Plan a named-region splice on the new annotation with that protection
   reference and an equal-frame replacement; inspect and execute it. Full-range
   edits include mixed transitions, whereas body-range edits leave them outside
   the write window. Verify the promised unchanged PCM and retained locks.
6. Explicitly select the accepted candidate, then export the exact selected
   version. Record any actual listening feedback separately when it occurs.

Re-read context before session mutations. A changed selection or lock policy is
not permission to drop preservation requirements. This workflow does not transfer
assets or state between the Sonic, Score and standalone Core workspaces.
