# Experimental bounded storage batch v3

This candidate adds an explicitly requested **one or four new chunks per attempt** to the TB371FC read-only storage continuation protocol. Production chunks remain **64 MiB**; a four-chunk attempt can commit at most **256 MiB**. The complete preparation-to-receipt bundle includes the RAM source controller and both preparation builders. It has **not been deployed or used to read the tablet**.

The frozen one-chunk runtime is unchanged. New filenames, a new wire protocol and request schema v2 prevent accidental mixed-version use. Existing API defaults remain one chunk. The new preparer continues the same authorized owner namespace and consecutive attempt sequence; it cannot bootstrap a new namespace or invent a prior release.

## Components

| Files in candidate/ | Purpose |
| --- | --- |
| acquire_block_ranges_batch_v3.py | Read-only sender and verified receiver; request-bound one-or-four chunks |
| storage_supervision_batch_v3.py | Original admission, locks, deadlines, FIFO supervision and receipts |
| host_supervisor_batch_v3.py, ssh_stdio_relay_batch_v3.py | Pinned host entry points |
| tablet_sender_batch_v3.py | Source identity/geometry/plan checks, physical-source lock and bounded source call |
| ram_source_controller_batch_v3.py | Guarded tmpfs controller, worker wait and overdue evidence |
| prepare_ram_launch_batch_v3.py | Generates pinned probe/capture launch and status commands |
| prepare_batch_attempt_v3.py | Validates prior evidence and generates the complete next-attempt packet |
| interface-contract-batch-v3.json | Explicit protocol, schema, bounds and adoption contract |

Tests and proof/ contain portable synthetic fixtures, frozen invariants, file pins and validation summaries.

## Integrity and lifecycle

The max_new_chunks field must be an integer exactly equal to 1 or 4. Booleans, missing budgets, other numbers and mixed wire versions fail. The budget is bound through preparation, request, RAM controller, sender payload, hello, receiver and source/host receipts. A pause adds exactly that budget; a final attempt may add a shorter remainder. A zero-new completed resume preserves the existing manifest.

Every resume still rereads all prior source chunks and rehashes/decompresses all prior receiver chunks. New chunks retain the before/stream/after source hashes, transport hash, receiver file hash and decoded-content hash. Partial files are retained, commits use no-replace semantics, and the source is opened O_RDONLY. Source, capture and supervisor locks are unchanged.

Original foreground agent-heavy admission remains required. Source/host deadlines remain 240/260 seconds, with the original RAM-controller 250-second overdue boundary. No automatic retry is added. A clean ACK alone does not release the attempt: real EOF/drain accounting, host termination, exact source/SSH/controller termination observations and explicit owner review remain required.

The final manifest remains tb371fc-readonly-range-stream-v1 and snapshot_consistent stays false. Capturing bytes does not establish factory authenticity, a coherent live snapshot or a successful Android boot.

## Owner adoption: outstanding requirements

The active capture owner must perform adoption after the current attempt has ended. None of these steps is claimed complete.

1. **Review the freeze and current inventory.** Verify proof/runtime-and-test-pins.json and the latest complete endpoint observations. Preserve the same owner namespace, attempt sequence and established tmux seat. Explicitly review and release that actual prior attempt.
2. **Install new files exclusively.** Put the collector, supervision module and two host entry points under the current namespace's code/ directory with their exact new basenames. Put the interface at bindings/interface-contract-batch-v3.json. Preserve private ownership/modes and do not overwrite existing files. Keep the source caller and three preparation/controller modules together in the reviewed control bundle.
3. **Qualify the fresh RAM root.** Its fixed path is /dev/tb371fc-support-f74b9-batch-v3-f8e0-20261125-001. Generate a separate probe with prepare_ram_launch_batch_v3.py --mode probe, the pinned controller, an unused probe ID and new local output directory. Execute the generated launch/status commands; verify the existing tmpfs/mount/boot guards and that the probe survives tool return and is reaped. Synthetic tests do not replace this live gate.
4. **Prepare one explicit next attempt.** The preparer reads evidence and writes commands; it does not execute them. A fresh tail job needs a distinct capture directory, resume false, prefix 0 and no copy claim. A continuation needs its exact current prefix and evidence.
5. **Execute the separately guarded stages.** Review and run host preparation, host launch and tablet RAM launch, then observe both endpoints. Host preparation refuses changed prior receipt pins, changed retained-file inventories, live prior identities or an existing/unexpected next attempt. Bindings and launch records use exclusive creation.
6. **Qualify one real bounded batch.** A four-chunk source-to-HDD transfer at unchanged production deadlines remains necessary. Verify actual new count, every hash/receipt, EOF accounting, controller/worker/SSH termination, retained partials and lock release. Unit tests do not qualify 256 MiB production timing.
7. **Use explicit assembly mapping.** Supply completed new captures to the reviewed mapped six-LUN assembler with their own request/outer/termination/release provenance. Do not overwrite an old capture or combine independent prefixes. Whole-storage completion requires complete coverage and final readback.

The preparer preserves the current source boot, geometry and original job/source-binding digests. Changed boot, mount, source, runtime or authorization needs a separately reviewed version.

### Preparation interface

    python3 -I -S -B candidate/prepare_batch_attempt_v3.py prepare --spec /absolute/spec.json --out /absolute/new-directory

The spec uses schema tb371fc-storage-batch-attempt-preparation-spec-v3 and these exact top-level fields:

- previous: paths for request, complete outer_result, source_termination, and host_termination. The last is the full wrapper containing evidence (the original pin) and result (complete host observation).
- review: explicit outcome (clean or abnormal), reason, and retained_files_reviewed true.
- next: consecutive attempt_id, approved job_name, explicit capture_name, resume, expected_completed_chunks, copy_preparation, max_new_chunks (1 or 4), source_deadline_seconds 240, host_timeout_seconds 260.
- tmux: the existing supported owner socket/pane pinned by the preparer.
- inputs: absolute paths to original pinned jobs_plan/source_binding and exact candidate caller/collector.
- schema: the value above.

Both request-v1 and request-v2 previous evidence chains use their exact runtime profiles. Request-v2 requires RAM source-result-v2 and controller/worker proof; a downgraded source result or mismatched budget fails.

After the attempt, the observe subcommand with --packet, --outer and a new --out directory generates two read-only observation commands. It does not execute them or assert termination in advance.

## Tests and publication boundary

On Linux with Python 3.13, provide an absolute, nonexistent test directory:

    BATCH_TEST_EVIDENCE=/absolute/new-test-directory python3 -I -S -B test_storage_batch_v3.py

Use the project's original heavy gate in the shared project environment. Small bounded tmpfs fixtures are suitable for protocol tests. The 31 tests use synthetic regular files, socketpairs, actual named FIFOs and owned test children. No tablet, SSH connection, private key, historical checkout or retained production receipt is required.

Production admission is explicitly substituted only in the supervisor fixture. The FIFO fixture additionally accepts a small regular-file plan. Neither substitution exists in candidate code. Production 64 MiB bounds are tested separately without opening a block device.

Proof/baseline-invariants.json records unchanged frozen function/template hashes; historical source copies are excluded. An optional private preflight against retained attempt11 metadata also passed. Its summary is included, but private inputs and generated packets are excluded. See VALIDATION.md for results, retained failures and limits. The clean source bundle contains runtime, tests and proof metadata only.
