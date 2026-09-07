---
hide:
  - navigation
  - toc
---

<section class="ma-hero">
  <div class="ma-hero__copy">
    <p class="ma-eyebrow">MATTER AUDIO CORE · 0.6.0</p>
    <h1>Edit audio.<br><span>Keep the original.</span></h1>
    <p class="ma-lede">Local audio tools for developers and coding agents. Shape a sound, protect the parts that matter, and keep an exact record of every result.</p>
    <div class="ma-actions">
      <a class="ma-button ma-button--primary" href="getting-started/">Make your first edit <span aria-hidden="true">→</span></a>
      <a class="ma-button ma-button--secondary" href="https://github.com/andyandymike/matter-audio-core">Explore the source</a>
    </div>
    <p class="ma-caption">Python 3.10+ · Windows &amp; Linux · MIT</p>
  </div>
  <div class="ma-signal" role="img" aria-label="Illustrated edit flow: an immutable source becomes a new candidate, with the protected intro kept identical.">
    <div class="ma-signal__heading"><span>ILLUSTRATED EDIT FLOW</span><span class="ma-dot"></span></div>
    <div class="ma-signal__label"><span>01 / SOURCE</span><span>immutable snapshot</span></div>
    <div class="ma-wave" aria-hidden="true">
      <i style="--h:22%"></i><i style="--h:40%"></i><i style="--h:72%"></i><i style="--h:50%"></i><i style="--h:32%"></i><i style="--h:62%"></i><i style="--h:88%"></i><i style="--h:55%"></i><i style="--h:38%"></i><i style="--h:75%"></i><i style="--h:92%"></i><i style="--h:58%"></i><i style="--h:35%"></i><i style="--h:67%"></i><i style="--h:45%"></i><i style="--h:20%"></i>
    </div>
    <div class="ma-signal__route"><span>Snapshot</span><b aria-hidden="true">→</b><span>Edit</span><b aria-hidden="true">→</b><span>Verify</span></div>
    <div class="ma-signal__label"><span>02 / CANDIDATE</span><span>new audio asset</span></div>
    <div class="ma-wave ma-wave--edited" aria-hidden="true">
      <i style="--h:22%"></i><i style="--h:40%"></i><i style="--h:72%"></i><i style="--h:50%"></i><i style="--h:32%"></i><i style="--h:62%"></i><i style="--h:64%"></i><i style="--h:40%"></i><i style="--h:27%"></i><i style="--h:54%"></i><i style="--h:65%"></i><i style="--h:41%"></i><i style="--h:25%"></i><i style="--h:47%"></i><i style="--h:32%"></i><i style="--h:14%"></i>
    </div>
    <div class="ma-signal__legend"><span>Protected intro · identical PCM</span><span>Edited region</span></div>
    <div class="ma-signal__footer">Your source stays intact. Your result gets its own record.</div>
  </div>
</section>

<div class="ma-principles">
  <div><strong>Local by default</strong><span>Core edits need no model service.</span></div>
  <div><strong>Explicit operations</strong><span>Typed JSON requests and measured results.</span></div>
  <div><strong>Traceable delivery</strong><span>Saved selections and exact WAV exports.</span></div>
</div>

## From one edit to a finished cue set

Start with a WAV snapshot. Add only the workflow you need.

<div class="ma-workflows" markdown>

### Shape a sound

Inspect levels, trim a region, change gain, add fades, or layer existing audio.
Every operation produces a new asset with recorded lineage.

[Start editing →](getting-started.md)

### Keep an edit session

Save selections and feedback, protect exact PCM regions, and resume managed jobs
after interruption. Compare versions when you are ready to listen.

[Work with sessions →](sessions.md)

### Assemble and deliver

Build overlap loops and finite scene timelines. Organize cue variants, search
measured features, and export the exact saved WAV files.

[Build a cue package →](production.md)

</div>

## A shared core, three ways in

Use the standalone CLI for local WAV editing, or enter through a product adapter.

| Entry point | What it brings |
| --- | --- |
| **Matter Audio Core** | Portable PCM operations, sessions, jobs, comparisons and cue packages. |
| **SonicMatter** | Registered recording snapshots and its existing fused Q15 processing profile. |
| **ScoreMatter** | BGM authoring and an optional, already configured local SA3 inpainting adapter. |

[Connect a product](products.md){ .md-button }
[Use from Codex](codex.md){ .md-button }

!!! note "Early development, clear boundaries"
    Core 0.6.0 works with PCM16 WAV. Model weights and hosted generation services
    are not bundled. RMS matching is not LUFS, feature distance is not semantic
    understanding, and a passing measurement is not a listening verdict.
    [Read the validation boundaries](validation.md).
