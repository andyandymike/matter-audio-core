# BGM Direction, Revision And Listening

Use this reference for musical direction comparisons, seed variants and bounded BGM revisions. It complements the installed audio operations; it does not create a generation capability or start a model by being read. Ordinary PCM editing and sound-effect work can use their existing routes directly.

## Project And Cue Context

Resolve the consuming `project_root` using the main skill's workspace rules. Read only that project's optional `.agents/skill-context/matter-audio.md` when present; its paths are project-root-relative. Use the current request and selected cue when no adapter exists. Do not look in another project for style memory, recipes or defaults.

Separate creative constraints by their actual scope:

| Kind | Meaning |
|---|---|
| `LOCKED` | A musical property the user or current project brief has already chosen. Project-wide properties apply across cues; cue-local properties stay with their cue. |
| `OPEN` | A property the current request allows changing, such as instrumentation, rhythmic motion, harmonic character, density or production space. |
| `NEGATIVE_EVIDENCE` | A rejected result, defect or ineffective approach, with the exact cue/version and rejection scope retained. |

The newest explicit instruction wins, followed by a relevant cue-local exception and applicable project rules. Interpret each locked aesthetic property as concrete musical intent, not just a keyword. Do not infer a preference from a prompt, successful generation, filename, technical measurement or silence. Ambiguous feedback remains an open question.

Creative locks describe musical intent; they are not sample-preservation guarantees. For exact retained PCM, use the supported constraints in [protected editing](regions.md). A cue-local rejection does not reject the source direction or become a project-wide prohibition unless the user says so.

## Choose The Comparison

| Request | Hold fixed | Vary |
|---|---|---|
| Direction discovery | Applicable locked axes, model/settings, duration and seed where supported | Substantially different prompts or musical concepts, usually a small set of two or three. |
| Seed refinement | The exact selected prompt and model/settings | Seeds, to compare realizations of one direction. |
| Bounded revision | Every applicable property not reopened by the request | Only the named musical axes. |
| Audio-to-audio, when available | An already musically useful guide and requested preserved properties | The authorized orchestration/timbre/style change. A weak rhythmic or harmonic guide is not repaired merely by giving it a new timbre. |
| Regional inpainting, when available | A worthwhile source and sufficient surrounding musical context | The bounded defective region, respecting actual PCM protection and the model's edit semantics. |

For direction discovery, change a meaningful musical concept, such as instrument roles plus rhythmic engine or harmonic language, rather than cosmetic wording. Write whole-track intent: use, lead/support roles, phrasing, energy, space and foreground occupancy. Avoid contradictory demands. Textual section names, chord prose, exact tempo grids and “seamless loop” are requests, not verified controls unless the chosen backend explicitly supports them.

If seed variants all sound similar to the user, more seeds may not open a new direction. If fixed-seed prompt variants sound similar, investigate weak prompt separation or the model's control limits. Suggest the appropriate prompt-level change, while honoring a requested seed batch under one clearly identified frozen prompt. Do not silently multiply an `N`-seed request across several prompts. Explain material compute/time/storage implications when they affect the requested batch; do not insert another confirmation when its scope is already clear.

## Execution Uses A Verified Route

Use the configured product launcher and live capabilities described by the main skill. [Comparison, layers and local models](comparison-and-layers.md) covers comparison and supported local editing; [managed jobs](jobs.md) covers execution and recovery when that operation is registered there.

Text-to-audio generation is a distinct capability. Use [the ScoreMatter generation launcher](score-generation.md) when configured, or another verified route explicitly selected for the task. The existence of an inpainting operation does not imply a text-generation operation. Do not invent an operation name, fall back to a different model, copy a historical shell command blindly or install missing weights. If a required route is absent, finish the requested brief or comparison prompts where useful and identify the exact execution gap.

Derive model family, precision, sampler, steps, guidance, conditioning and duration from the selected route and task. This reference supplies no universal model preset. Keep those settings fixed for a controlled comparison and record the values actually used. If the backend cannot hold a requested variable fixed, state that limitation instead of calling the comparison controlled.

Execute only the requested candidate set. Preserve successful outputs through local import, publication or record failures; do not regenerate them to hide a technical failure. Use the recovery behavior of the actual route: shared actions/jobs have IDs; native generation uses its exact output, records and process evidence and has no shared job ID. Diagnosis or planning alone does not imply a model run.

## Preserve Sources And Explain Evidence

Keep raw model output unchanged. Give trimmed, normalized, looped, resampled or encoded auditions distinct identities and record their transformations. Use the current project's audio workspace and requested output location; the product checkout is a tool location, not a home for unrelated project assets.

Use the route's existing provenance records rather than inventing a second ledger. Retain enough to reproduce the comparison: cue purpose, source style record when used, inherited/open axes, applied negative evidence, exact prompts/seeds, actual model/settings, source and output paths or asset IDs, duration, execution timing and content hashes. Historical model snapshots remain historical evidence, not proof of the current installation.

Screen raw WAVs for readable structure, frame count/duration, sample rate/channels, finite samples, peak/near-full-scale samples and material DC offset. See [production authoring](production.md) for available measured analysis. Do not silently normalize away a warning. Preserve it on the raw source even if a separate derivative avoids the affected region.

Measurements can identify defects or support level matching; they do not rank musical coherence, suitability, eeriness, beauty or repeat fatigue. Recurrence, spectral similarity and loudness are not substitutes for listening. Present the verified playback files using the main skill's audio delivery convention, or respect an instruction to defer listening. Describe intended differences without asserting that the model achieved them or that the agent heard the audio.

## Feedback, Selection And Memory

Bind feedback to the exact cue and version the user evaluated. In an existing session workflow, record the user's actual wording as `source: agent_relay`; use `source: agent` for hypotheses. Never submit inferred feedback as `user_ui` or `user_cli`. Follow [session requests](sessions.md) for revisions, current feedback, branch-origin attribution and explicit selection; do not relabel feedback on another version as feedback on the current one.

Keep these judgments separate: a useful direction, a preferred realization, successful preservation, an acceptable loop and acceptable in-context playback. A general “these are okay” can support that comparison's usefulness without selecting a final cue. A complaint that a prompt omitted a required property is process feedback, not necessarily a rejection of the unheard audio. Rejecting a derivative's tail does not automatically reject its underlying source.

Session feedback records retain version-specific observations within the authorized audio task. Promoting an observation into lasting project-wide style memory is a separate scope decision: requests such as “remember this,” “use this for future cues,” or an explicit preference-maintenance task authorize the corresponding update. A selected version is saved through the session's selection operation; it does not silently establish a general aesthetic rule.

When updating authorized project style memory, preserve the user's wording and source version, mark superseded rules, and retain only supported locked/open/negative axes at the stated scope. Keep exact prompts, hashes and immutable history in their existing run/session records. Do not copy project style memory into this shared package, another project or the personal Codex memory store. A brief critique can remain in the current conversation when no persistent audio workflow was requested.

## Looping And The Real Consumer

Follow the requested scope rather than adding stages. “Use A, make a loop and integrate it” already supplies selection and the subsequent work; do not ask again. Candidate generation or selection alone does not imply normalization, looping, catalog changes or game integration.

For a requested loop, preserve the raw master and create a separate derivative. Choose a musically plausible phrase boundary, retain an intro when useful, and expose several consecutive repeats for audition—normally five. An overlap crossfade may remove a waveform discontinuity while shortening the period; use the operation's actual output coordinates from [production authoring](production.md). It cannot repair unresolved harmony, a missing beat or an incomplete phrase.

Keep technical seam evidence separate from the user's musical seam judgment. After selection, dialogue/foreground occupancy, long-repeat fatigue and transitions still need their own relevant listening evidence; a prior direction preference does not prove them. Report pending listening directly without converting it into an approval ceremony for unrelated work.

Game integration remains a consumer-project task. If requested, locate the live audio consumer, catalog/resource, bus, import/loop settings and ordinary player path. Verify actual cue switching and playback through that path. Copying an asset or producing a cue package does not establish that the game uses it. If a consumer is missing, name the concrete gap rather than inventing a debug-only player. Report implementation and listening outcomes separately.
