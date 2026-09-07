# Local model adapters and process ownership

The core package contains no audio model or weights. A product registers an
`Operation` and supplies its parameter resolver and executor. Built-in operations
continue to use zero audio-model calls. Core 0.5 provides shared assembly,
process ownership and durable attempt evidence for an optional model adapter.

## Adapter contract

An operation can specify `input_count=(minimum, maximum)`, `realization` and
`availability`. A `(1,1)` executor receives a PCM object as before; multi-input
operations receive an ordered PCM list. The first input defines the protected
timeline. The adapter's profile and effective parameters enter the resolution
digest. Model configuration and component identity belong in those parameters;
resolve/capability queries must not run inference.

Executors may return the existing `(pcm, observation)` pair, or `OperationOutput`
with a final PCM, findings and additional `AudioAttachment` proposals. The final
audio is output index 0. Proposal attachments have a distinct role and context
parents; they cannot masquerade as the protected assembled output. Publication is
all-or-nothing, and failures discard unpublished audio outputs.

Record these separate concepts for a local edit:

| Field | Meaning |
| --- | --- |
| `context_read` | Actual source frames supplied to the model |
| `requested_edit` | User-requested interval |
| `model_mask` | Backend's quantized latent/sample mask and rounding convention |
| `allowed_write` | Final PCM frames the program may change |
| `transition` | Crossfades wholly inside the final write interval |
| `observed_changes` | Measured final changes, including outside-write counts |

Model mask preservation is not an exact PCM guarantee. Assemble the proposal
with the shared splice, check all final write bounds, and preserve the raw
proposal separately when useful. Format adaptation must be explicit in findings;
never silently resample and then claim original PCM preservation.

## ScoreMatter integration

A compatible ScoreMatter authoring checkout exposes `score.sa3_inpaint/v1` using
its existing local SA3 Medium / SAME-L fp32 / LiteRT runtime. Check the product's
`audio capabilities` first; the core CLI does not register that operation itself.
The adapter and model installation are maintained in the ScoreMatter checkout,
not included in this wheel. A missing runtime is reported as unavailable.

The adapter accepts 1–120 seconds of 44.1 kHz stereo PCM16. Parameters include
prompt, seed, start/end frames, transition frames, steps, threads, cfg and timeout.
The model duration rounds up to whole seconds; the final proposal is explicitly
cropped back to the input length before assembly. Mask coordinates reproduce the
configured driver's 4096-sample latent grid and ties-to-even rounding. Empty
quantized masks and negative prompts ignored by `cfg=1` are rejected.

Its resolution records SHA-256 snapshots of the model components, driver/source
files, interpreter launcher/config and installed distribution metadata. Hashes
are computed on first use in each CLI process and cached only while file identity,
size and timestamps remain unchanged. This binds a trusted local installation;
it is not a hermetic environment or protection against hostile runtime writers.
The adapter does not import model libraries into the core process, download
weights, read API keys, play audio, or perform automatic inference retries.
One OS lock per configured runtime prevents overlapping edits across workspaces.

## Owned process trees

`processes.run_process(argv, cwd=..., audio_model=...)` is an API for trusted
registered adapters, not a CLI accepting arbitrary commands. It uses a launch
gate: the model cannot start until the supervisor has established ownership.
Windows assigns the gate to an unnamed Job Object with kill-on-close before
releasing it. The stdlib-only gate uses the base interpreter to avoid an extra
virtual-environment launcher, and its actual PID/job membership is verified
before any backend may start. Children inherit ownership; breakaway permissions are not granted.
This follows [Microsoft's Job Object semantics](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

Linux starts a process group with a parent-pipe watchdog. Cancellation, timeout
and normal exit clean up remaining descendants; parent death also stops the
group. Backends that deliberately detach/create unrelated sessions are unsupported
on Linux. Linux checks `/proc` for active group members after termination; dead
zombies awaiting the system reaper are not running backends.

Cancellation remains requested until the owned tree has stopped. A shutdown that
cannot be confirmed becomes an error, not a cancellation acknowledgement. Standard
output/error are bounded by a configured limit (4 MiB each by default), and a
bounded stderr tail and elapsed wall time are recorded. Model processes use their
own interpreter; the shared core's dependency graph stays lightweight.

## Attempts and recovery evidence

Before launch, after an actual backend acknowledgement and after shutdown, the
store publishes immutable journal entries under the existing request claim.
Each entry binds its claim, sequence and digest. Result publication includes the
same execution evidence; `job show` also reads it independently. It therefore
survives a crash or failed audio publication and remains attached to its attempt.

`audio_model_calls` counts acknowledged backend launches, including attempts that
fail or are cancelled. A prelaunch failure is zero. A crash between launch intent
and acknowledgement is uncertain: the count is `null`, with separate known-call
and uncertain-launch counts. It must not be described as zero. Wall time measures
the supervised invocation, not model-only kernel time or billing. A successful
process still needs valid output, boundary checks and publication to become a
successful edit. No completed inference or musical suitability is inferred from
a launch count.

Managed job recovery does not rerun inference. Explicit retries retain earlier
attempt evidence. Local runtime locks and process-group behavior have the same
local-filesystem/trusted-writer boundary as the rest of the store.
