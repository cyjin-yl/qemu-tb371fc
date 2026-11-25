# Validation record

## Final results

The runtime and tests are frozen in proof/runtime-and-test-pins.json. Final checks ran in the isolated development container under the unchanged original host agent-heavy gate, from the dedicated qemu-storage-batch-f74b9 tmux session.

| Run | Scope | Result |
| --- | --- | --- |
| 004 | Self-contained protocol, prefix, partial-retention, request, FIFO, lock/deadline and preparation tests | 31/31 passed in 1.978 seconds |
| 005 | Preparation-only preflight using private immutable copies of retained attempt11 metadata, plus synthetic v2 negative cases | 12/12 passed in 0.210 seconds |

Runtime: Linux, CPython 3.13.5. Fixtures were bounded regular files on isolated tmpfs. Results, logs and evidence archives are persisted in the private work directory. The public proof directory includes only the two small result summaries and public synthetic test log.

Checks cover exact four-chunk pause, short final remainder, default one-chunk API behavior, interruption after two commits with retention/resume, both source and receiver prefix corruption, wire/budget refusal before capture access, typed bounds, zero-new complete resume preserving manifest identity, request/source identities, 64 MiB production geometry, lock conflicts/release, deadline failure, operator release, real named-FIFO EOF/drain counts/hashes, complete preparation bindings, changed receipt/existing-next-ID refusal and no-overwrite.

Both approved runtime profiles are checked. Request-v2 cannot downgrade source-result to omit controller/worker evidence. Frozen proofs cover 36 unchanged collector, supervisor and RAM-controller functions and the host preparation/release templates.

## Earlier failed runs retained

No failed run was deleted or represented as successful.

- 001: 18/19 passed. The positive synthetic release fixture omitted relay evidence; runtime correctly refused it. The fixture was completed.
- 002: 26/31 passed. One extracted-host-guard fixture lacked its pincheck binding. Four protocol/FIFO cases exceeded short fixture timing windows on the shared persistent volume. The FIFO trace reached a valid four-chunk ACK at 6.266 seconds, but its receipt stayed partial; the relay reached the eight-second fixture deadline with source EOF but no receiver-response EOF. All forward/response counts and hashes matched, undelivered counts were zero, and the runtime refused clean drain. Exact test child identities were later confirmed absent.
- 003: 30/31 passed in 2.045 seconds with the same protocol fixtures on tmpfs. The sole error was an obsolete portability-test reference to the removed historical-module constant. The real default-one-chunk exchange already passed; the obsolete assertion was removed.

No candidate EOF, hash, count, identity, release or production deadline check was relaxed for the final passes. Fixture-context repair and removal of private test dependencies changed test code only. Run002 identifies the stalled stage; it does not prove a single underlying system cause. Fast tmpfs results do not establish persistent-volume latency or crash durability.

## Limits

No physical source, tablet, production SSH relay or production RAM controller was run by this candidate. No current capture, old artifact, source lock or owner attempt was changed. Production admission was mocked only in the synthetic supervisor fixture; the outer test process used the original real heavy gate.

The complete bundle is reviewable and deployable by the coordinated active owner, but is not production-qualified. Stable latest prior evidence/release, exclusive runtime installation, the live RAM probe gate and one real four-chunk transfer at source240/host260 remain outstanding. Each later attempt still requires exact endpoint termination and explicit review. Full six-LUN assembly and readback remain downstream requirements.
