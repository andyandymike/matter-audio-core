# Layering and bounded replacement

Core 0.5 adds `mix/v1` and `splice/v1`. Inputs must share the same sample rate and
channel count. There is no implicit resampling or channel conversion. Up to 16
input references are allowed, with at most 64 MiB of combined WAV input bytes.
Existing single-input operations still reject additional inputs.

## Mix an immutable recipe

The first input is the fixed-length base. Every additional input is used by one
layer entry; repeat its asset reference if a clip is needed in multiple places.
All frame coordinates are on the shared sample-rate timeline.

```json
{
  "schema": "matter-action/v1",
  "request_id": "paper-layers-1",
  "operation": "mix/v1",
  "inputs": ["<base-asset>", "<layer-asset>"],
  "parameters": {
    "layers": [{
      "input_index": 1,
      "source_start_frame": 0,
      "source_end_frame": 2000,
      "offset_frame": 4000,
      "db": -12,
      "fade_in_frames": 100,
      "fade_out_frames": 200
    }],
    "base_db": 0,
    "clip": "reject"
  }
}
```

Source windows and destination windows must fit; the output length never grows
silently. Fades are linear amplitude, with the same endpoint convention as
`fade/v1`: 0 frames means no fade, 1 frame is the zero endpoint, and longer fades
include zero and unity endpoints. A layer's fades may not overlap.

`pcm16-layer-sum-q48/v1` quantizes gain and fade factors to Q24, multiplies them
without intermediate sample rounding, sums all layers with the base, and rounds
the final Q48 sum once, ties away from zero. Default overflow handling rejects
the output; explicit `saturate` records the number of out-of-range samples before
clamping. This profile does not replace SonicMatter's frozen fused-Q15 operation.

The resolution stores the complete recipe and every input digest. Outputs record
one `source` parent for the base and `layer` parents for additional inputs.
Editing an independent layer means re-rendering this immutable recipe with only
that layer's parameters changed. It does not mean adding the new layer on top of
an already mixed WAV. Outside the changed layer's destination window, the two
rendered PCM outputs should match exactly when the remaining recipe is unchanged.

When a locked session has already selected a mix, a recipe re-render can use a
direct action bound to the historical revision that selected its base. The
current lock policy must still match that bound policy. Select the new candidate
against the **current** head revision. Managed jobs bind the current selection;
they do not implicitly change it back to an earlier recipe base. A changed lock
policy requires an explicit updated workflow; it cannot be silently dropped.

SonicMatter obtains these inputs from its registered recording catalog and
explicit decoder. The shared core does not decide recording eligibility.

## Replace one window

`splice/v1` accepts exactly two inputs: base and replacement. Parameters are
`start_frame`, `end_frame`, optional `replacement_start_frame` (defaults to the
base start), and `transition_frames` (defaults to 0). The replacement segment has
exactly the target window's length. Set `replacement_start_frame: 0` when using
a separate short clip. Both source and destination windows must fit.

The output has the base's frame count, rate and channels. Outside `[start,end)`,
PCM is copied exactly. Equal-length linear transitions lie **inside** both ends
of the write window and cannot overlap. Each transition's outer sample is the
base; its inner sample is the replacement. A one-frame transition keeps the base
sample. The Q24 weighted sum is rounded once, ties away from zero. This is a
bounded replacement splice, not an arbitrary-duration concatenation operation.

`pcm16-splice-linear-q24/v1` is also available to product adapters as `splice()`
with a plan validated by `resolve_splice()`.

## Region evidence

Both operations declare conservative write ranges and identity time mapping.
Protected overlaps fail at resolution, before rendering. Execution verifies
actual locked PCM before publication and selection checks the resulting lineage.

`observed_changes` compares the complete base/output PCM. It reports changed
sample/frame counts, changes outside allowed writes, source/output PCM digests,
and up to 64 contiguous change ranges with the total range count and a truncation
flag. Splices also report transition coordinates and boundary jumps in integer
PCM sample units. These measurements do not establish that a splice is inaudible.

Run `python examples/composition_workflow.py` for two layer revisions, exact
unchanged-region assertions, persistent comparison and selected-version export.
