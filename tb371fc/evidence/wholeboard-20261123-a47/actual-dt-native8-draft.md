# Actual full-DT userspace fixture consumer — preparation only

This directory contains an unexecuted native adapter. It consumes the separately
reviewed `tb371fc-full-dtb-user-fixture-v1` build artifact from the same source as
the accepted QEMU binary. It does not compile, relink, download, run a component
test, start a diagnostic, or select a new source on its own.

The current source `3b31e83dc412962826ada872e361a1d32ea4e463` passed 72 component
gates and four Linux gates, but its original-Image/full-DT diagnostic stopped at
an RPMh error and PID1 panic. No execution of this adapter against that source
is planned. Its lack of a userspace receipt does not prove that no user-mode
instruction ever executed. The next source and exact CI binary must be audited
and its primary diagnostic considered separately before root invokes a fixture.

## Identity chain and root integration

1. Root's existing worker verifies the exact workflow source, runner SHA, gate
   order, clean tree, two passing suite receipts, actual QEMU bytes and ZIP SHA.
   Keep the existing `*-artifact.json` receipt unchanged. This adapter consumes
   it by exact file SHA; it does not re-download or reinterpret an unaccepted ZIP.
2. Once the build-only fixture step succeeds, stage the eight members below
   from that same ZIP into a fresh directory, preserving the member-relative
   path. Reject duplicate ZIP names, directories/symlinks for those members,
   unexpected path components and any manifest/file identity discrepancy.
   Hash each actual member read from the ZIP. Produce the small stage receipt
   described in `stage-receipt-template.json`, tying it to the same artifact and
   source. Its two true booleans describe the already completed outer ZIP and
   source/binary audit, not a userspace or ELF execution result.
3. Stage the five fixed helper source files, the existing native probe receipt,
   and the actual-DT model receipt from their reviewed Git blobs. These are
   source dependencies, **not files currently uploaded in the fixture ZIP**.
   Their exact SHA256 pins are enforced by the adapter. Use a fresh helper
   directory without `__pycache__` or `.pyc` files. Imports do not invoke any
   helper's `main()`.
4. Complete the original-Image/full-DT primary diagnostic on that exact binary.
   Supply its actual `diagnostic.json`, `*-summary.json`, `*-extra.json` and
   their SHA256s; retain the complete `kernel-printk.log` beside diagnostic.json.
   The summary's source, binary, kernel, DT and diagnostic digest must match.
   The extra receipt must bind the exact diagnostic bytes/hash and complete
   kernel log bytes/hash. The adapter checks its complete milestone list and
   the complete hash-bound kernel log for `Kernel panic`, `Internal error` and
   `Unable to handle`; it does not rely on the 16 KiB tail. Either occurrence
   prevents launch. Observer errors must be absent/None. Nonfatal device-error
   counts, including the known SPMI warnings, are not treated as a fatal gate.
   A host diagnostic exit or stopped snapshot is not a boot success criterion.
   This adapter has no automatic fallback/launch trigger based on that receipt;
   root decides whether and when to invoke it.
5. Fill `config-template.json` with actual values. The source, runner, artifact,
   ZIP, manifest and binary hashes are explicit; none is inferred from a branch
   name or downloaded latest build. The template is deliberately not executable
   as-is. Copy the exact primary `-M` value into `expected_primary_machine`.
   A fresh, short output path is required for the Unix sockets.

The expected fixture ZIP members are exactly:

| Member below `tb371fc-results/full-dtb-user-fixture/` | Consumer use |
| --- | --- |
| `manifest.json` | Actual successful build, source/input/ABI/ELF identities |
| `init` | Read-only ELF header, sections, symbol table and RET-byte inspection |
| `initramfs.cpio.gz` | Hash, bounded in-memory gzip decoding and exact seven-entry comparison |
| `arch-probe` | Exact 2,264-byte native-tested ELF identity |
| `init.nm.txt` | Independent comparison to the actual raw ELF symbols |
| `init.readelf.txt` | Actual ET_EXEC/AArch64/no-INTERP build evidence |
| `init.checkpoint.objdump.txt` | Actual one-instruction checkpoint disassembly |
| `source-commit.txt` | Exact fixture build source commit |

The consumer does not use absolute CI paths embedded in the manifest. It opens
only the exact stage-relative member paths and checks their size and SHA256.
Additional build logs can remain archived in the original ZIP; they are not
needed at runtime. No source toolchain executable is required by the consumer.

### Pure ZIP staging helper

`stage-actual-dt-fixture.py` defines only
`stage_actual_dt_fixture(z, accepted_artifact, expected, stage_dir, receipt_path)`.
Root can extract that one function by AST into its existing worker; all required
imports, constants and pure validators are inside it. It imports only stdlib,
does not load the consumer or any supplied helper source, and has no entry point
or subprocess call.

Call it in the existing `with ZipFile(...) as z` block **after** all formal-suite
and component-stream audits and **before** binary installation or primary boot:

```python
fixture_stage = stage_actual_dt_fixture(
    z, receipt,
    {
        "source_commit": cfg["source"],
        "workflow_run": cfg["run"],
        "artifact_id": cfg["artifact"],
        "artifact_sha256": cfg["digest"],
        "qemu_sha256": qhash,
        "runner_sha256": cfg["runner_sha256"],
        "expected_gate_order": cfg["gate_order"],
    },
    p / (cfg["label"] + "-full-dtb-fixture-stage"),
    p / (cfg["label"] + "-full-dtb-fixture-stage.json"),
)
```

The helper rechecks the supplied accepted artifact context, fixed eight member
names, uniqueness, regular-file metadata, paths, per-member/total size bounds,
actual source file, manifest hashes/ABI, raw ELF symbols/RET and exact archive.
It reads and validates all members before creating the fresh stage. It writes
only those member bytes and the separate receipt, then verifies its host writes.
All staged files are mode `0444`; the receipt contains member-relative paths so
root may copy the stage to CT without changing the receipt. It returns actual
manifest/receipt hashes and the independently decoded ELF proof for the worker
to archive. The whole-ZIP digest and detailed binary/component audit are reused
from the enclosing worker and are not misreported as repeated by this helper.

Do not call the consumer from this staging helper. Root first copies the accepted
stage and fixed support sources, runs and reviews the same-binary primary,
then fills the consumer configuration. A failed build or missing stage member
therefore stops before installation; a later primary fatal stops the consumer
before QEMU launch. These are distinct checks.

### Required helper and evidence files

| Staged dependency | SHA256 |
| --- | --- |
| `helpers/boot_smoke_gdb.py` | `93e046b915b71cd74198e8dd518564c4c0e7ea85b75a29966f08c42a8897b737` |
| `helpers/boot_smoke.py` | `4889f2cdd2e3fd3331cc57f3bf1a6b6ed8894f87503283c7f404766919540f7d` |
| `helpers/boot_qmp.py` | `b0d95776470110fd682371d393ffda4e2996fb295b1d3c81b41c46980be7a54c` |
| `helpers/boot_verdict.py` | `e0505903c5f52986a8650ffa38933db183501bca416e9876fac4560b08537cdf` |
| `helpers/boot_verdict_actual_dt.py` | `2e1a7d8d7e60abbede54385cba11ed1e50067b5f209ab119d15739e537b989ef` |
| `android-arch-probe.json` | `69c592e097069a779cc54eb38b823192b7de3ef18465eb49a3da35f4d496edf8` |
| `actual-dt-model-3b31e8.json` | `d7c70cfcdc7ac0d19df25feb22c3431d047129a85d03b4f306e97f5bd1f08e28` |

The old four helpers are used only for `GDB`, `QMP`, `connect`,
`has_guest_shutdown`, the pure `cpio_entry` encoder and the probe contract.
The record decoder and verdict come **only** from `boot_verdict_actual_dt`.
`boot_smoke_gdb.main()` and `boot_smoke.main()` are never called; those older
entry points build their own fixtures and are inappropriate here.

## Execution configuration guards

The adapter preserves the primary command's option order, machine options,
production Image, complete DT, 8 GiB memory, eight CPUs, TCG multithreading,
`rdinit=/init nokaslr panic=0`, log mask and reference-global ordering. It rejects
extra command-line devices, drives, block backends, monitor commands or flags.
It also rejects duplicate machine options and any disk/LU machine property.

The unchanged base whitelist contains 17 globals. Configuration must explicitly
provide `additional_reference_globals`, with exactly one of these values:

```json
[]
```

or, for a separately audited source/primary that actually used the new option:

```json
["qcom-sm8250-rpmh.reference-wait-mask-reserved=on"]
```

No default, inference from a panic, arbitrary extra option, or automatic enabling
is allowed. The actual primary's full ordered global list must equal the 17
fixed entries followed by that explicit list. Supplying the new value cannot
adapt a 17-global primary receipt. Source and binary checks also prevent mixing
an earlier failed primary with a later binary.

Only these command changes are made:

- Replace the primary diagnostic's older initrd with the actual CI-built fixture.
- Replace the QMP socket and QEMU log destination with this fresh run's paths.
- Remove `-no-shutdown`, permitting the guest's own PSCI poweroff to exit QEMU.
- Append the local GDB socket. QEMU remains initially stopped through `-S`.

The older primary initrd's SHA is checked as part of the primary configuration
but it is not executed by this adapter. No UFS LU, disk, guest memory write,
register write or replacement PC is introduced.

## Actual ELF and archive checks

The consumer requires the manifest's exact original and variant source hashes,
the fixed builder hash and the strict eight-check ABI. It independently decodes
the staged init ELF, including its SHT_SYMTAB and linked string tables. Required
symbols must be unique, defined GLOBAL `STT_FUNC`/`STT_OBJECT` with sizes 4/3120.
The result object can be in PROGBITS or NOBITS and must fit a unique writable
PT_LOAD. Its actual address comes from this ELF, agrees with this manifest/nm,
and is never taken from the old `0x004a0040` fixture.

The checkpoint must be at `0x08000000`, with a four-byte allocated executable
section. The covering executable PT_LOAD must yield the same file offset as
that section, and the bytes must be `c0035fd6` (AArch64 RET). The manifest and
actual objdump evidence must agree. Thus neither an old fixed file offset nor
an unrelated RET elsewhere in the ELF can satisfy the check.

The initrd is decompressed only in memory, with a 32 MiB bound, one complete gzip
stream and mtime zero. It must equal the pinned encoder's exact seven entries:
init, dev, dev/console (5:1), proc, sys, the native-tested arch-probe, and trailer,
in their original order/modes/inodes. This does not extract or rewrite an image.

## Observation sequence and separate outcomes

The future execution uses the pinned transport classes and these operations:

1. QMP capabilities and `query-cpus-fast` confirm eight CPUs.
2. GDB feature queries, `Z0` at the actual ELF checkpoint and `c` with no address.
3. The actual breakpoint reply, optional stopped-thread `Hg`, `p20` PC and `p21`
   PSTATE must demonstrate that checkpoint at EL0. QMP must report stopped.
4. Read exactly 3120 bytes from this ELF's result symbol using bounded `m`
   requests. Decode the unmodified record and enforce all eight checks, exact
   QRD model, pass mask `ff`, fail mask zero, timer/scheduler interval, CPU set,
   and native-tested counter probe. Check QMP remains stopped afterward.
5. Save `guest-result.bin`, its hash, decoded fields, errors and checkpoint
   evidence **before** attempting lifecycle completion.
6. Remove the breakpoint with `z0`, issue `c` without a PC, and observe the real
   QMP `SHUTDOWN` with `guest=true` and `reason=guest-shutdown` after resume.
   Require QEMU exit zero without observer termination.

The GDB transcript is checked against this bounded packet set. There are no
`M`, `X`, `G`, `P`, memory-injection or PC-replacement commands. TCG `Z0/z0`
debug breakpoints and deliberate debug stop/resume are explicitly disclosed.
QMP never sends `quit` or a guest power command. Its helper returns `[]` for a
timeout/partial frame and `None` for EOF; the consumer preserves that distinction.

Output evidence is deliberately separate:

- `prepared-inputs.json`: actual acceptance/manifest/ELF hashes and full commands.
- `guest-result.bin`, `arch-probe.stdout`, `userspace-checkpoint.json`: checkpoint
  and strict eight-check evidence, retained even if shutdown later fails.
- `gdb-transcript.json`, `qmp-commands.json`, `qmp-messages.json`: actual transport.
- `lifecycle.json`: breakpoint removal, resume, guest shutdown and process exit.
- `result.json`: combined result and before/after input hashes; true only if the
  strict record, EL0 witness, lifecycle and input immutability all pass.

This fixture is a validation PID1, not Android init. Even a future combined pass
would not establish Android boot, AVB acceptance, GPU rendering, storage service
or complete hardware behavior.

## Invocation and current review state

Only after root fills and approves the exact later run configuration:

```sh
python3 native-userspace8-actual-dt-draft.py CONFIG.json
```

Current preparation performed only source reads, file creation, AST parsing and
Python compilation to a code object. The adapter was not imported or invoked;
no QEMU, QTest, guest build or remote command was started. The earlier legacy
17-global consumer and the actual-DT fixture builder/validator had independent
reviews. This new consumer is undergoing its own static review; it has no
actual runtime result yet.
