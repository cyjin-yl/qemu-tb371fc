# Full-production-DT 8-check adapter — draft checkpoint

Status: AST/compile inspection only. No adapter invocation, helper invocation,
QEMU, build, test or diagnostic was run. This draft is not an execution receipt.
Execution remains deferred. Root must first accept the exact formal CI binary
and the main native diagnostic progress, then separately decide whether this
userspace fixture is appropriate. This preparation does not schedule that run.

Code: `native-userspace8-draft.py`, Git blob
`2616520547387f00fb6548d97c1c6fb22cff3b4a`, 14823 bytes,
SHA256 `357d98b1e3a004ac31a3cd22f37a5b4c837f8600bd7b930cab453e700043536c`.
The prior 14-global version received a complete independent static review from
board_gaps with no blocking issue. This copy adds only the two Devfreq entries
listed below. Its AST and compile-only inspection passed; neither the adapter
nor its helpers were invoked.

## Exactly two added globals

The following entries were added immediately after the two CPUFreq entries in
`GLOBALS`, preserving exact order and every existing configuration guard:

```python
    "qcom-devfreq-fw.enabled-reference=on",
    "qcom-devfreq-fw.reference-boot-perf-index=0",
```

The count changes from 14 to 16 only to match the current Devfreq reference
native configuration. The synthetic handoff and boot request are explicit;
these arguments do not establish firmware execution or applied cache speed.
Any future baseline parameter change still requires a reviewed update; the
strict ordered `observed_globals == list(GLOBALS)` guard is unchanged.

The old code is preserved at
`/run/tb371fc-native8-adapter-a47/native-userspace8-draft.py`, Git blob
`e870c095fdd542e0b3c2e4d935a3d5d4d363df7f` (14728 bytes).
The two new lines total exactly 95 bytes. Removing that exact two-line block
from this 14823-byte copy reproduces every original byte and SHA256
`f17c0673bc0ea91a9182eb674d058d724542867b7f87f6f6f1bc91248b8184ab`.
This byte-for-byte check and AST inspection were performed without executing
either module. All other runtime code, fixed ELF/initrd identity, 3120-byte ABI,
eight checks, GDB operations and separate guest-shutdown verdict are unchanged.

## Reused inputs and helpers

The fixed fixture and protocol are from
`530909a61f59ac6bf4a644ac0d13f4d1fc9c95ea:tb371fc/evidence/wholeboard-20261123-a47/native-userspace-fixture-contract.json`
(blob `b0e9d96fd80fa2ad3b6535dcd1ffda90182c1b75`).

- Initrd: `/work/gdb-smoke/exact-production-1/initramfs.cpio.gz`,
  SHA256 `88c075fc15487f3d6dc8e7cb1b61a35aa74a8f25ec6001a7d883c9623f86d7f6`.
- Existing ELF: same directory, `init`,
  SHA256 `5c62d22ab5c7115506733e5a2bed8e8c0aa49b3b22e9ef9b9f5b8d7ec3389293`.
- Production Image:
  `ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f`.
- Complete production DT:
  `5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
- The adapter imports only the pinned helper implementations in
  `boot_smoke_gdb.py`, `boot_smoke.py`, `boot_qmp.py`, and
  `boot_verdict.py` after checking their exact SHA256. It does not call
  `boot_smoke_gdb.main`, `symbols`, a compiler, or archive construction.
- The previously measured arch-probe reference JSON is hash-pinned;
  the fixed initrd hash binds its embedded arch-probe ELF.

## Invocation after a separate execution decision

```sh
python3 native-userspace8-draft.py CONFIG.json
```

Required JSON keys:

```json
{
  "source_commit": "REPLACE_WITH_ACCEPTED_FUTURE_CI_SOURCE",
  "qemu": "/work/wholeboard-a47/EXACT_ACCEPTED_BINARY",
  "qemu_sha256": "REPLACE_WITH_VERIFIED_CI_BINARY_SHA256",
  "baseline_diagnostic": "/work/wholeboard-a47/SAME_BINARY_NATIVE/diagnostic.json",
  "baseline_diagnostic_sha256": "REPLACE_WITH_EXACT_DIAGNOSTIC_SHA256",
  "helpers": "/work/wholeboard-a47/PINNED_530909_HELPERS",
  "native_probe_reference": "/work/wholeboard-a47/android-arch-probe.json",
  "out": "/work/wholeboard-a47/NEW_USERSPACE8_RUN",
  "timeout_seconds": 120
}
```

The caller supplies the CI source provenance; the adapter checks the supplied
binary digest against both the executable and the same-binary diagnostic.
The baseline diagnostic command supplies the complete machine, kernel, DT,
append, accelerator, RAM, CPU and all sixteen reference globals. Their values
are retained. No drive, blockdev or UFS LU argument is accepted. The captured
partial command DB is hash-checked too.

Changes from that diagnostic command are explicit: replace the UFS initrd with
the fixed 8-check initrd; redirect QMP/debug logs to the fresh run; add the GDB
socket; remove `-no-shutdown` so actual guest poweroff can terminate QEMU.
`-no-reboot`, `-S`, all board/reference parameters and the append string
remain. No initramfs or ELF is rebuilt.

## Required execution evidence

The adapter uses the existing GDB client to register `Z0,8000000,4`, resume
with bare `c`, observe a T05/S05 stop, select the reported CPU when available,
and read PC/PSTATE. It requires PC `0x08000000` and EL0, plus stopped QMP
status, then reads exactly 3120 bytes at VA `0x004a0040`. Fixed ELF identity
and the inspected RET bytes pin the ABI; this is not an invented runtime symbol
measurement.

Existing strict decode/validation checks magic/version/size, complete and
pass/fail masks, all eight named checks, strings, scheduler interval and the
complete counter-probe JSON. Raw RAM and GDB/QMP evidence are retained.
`userspace-checkpoint.json` is written before removing the breakpoint and
resuming, so a later shutdown failure does not discard valid EL0/8-check data.

Shutdown separately requires QMP `SHUTDOWN` with `guest=true` and
`reason=guest-shutdown`, process exit zero, removed breakpoint and actual
resume. Host termination cannot pass lifecycle. The overall verdict requires
both stages and unchanged input hashes. No guest RAM/register/PC write packet
or PC-bearing continue is sent. Debug breakpoint and stop/resume control is
explicitly disclosed. This is validation PID1, not Android init.

## Current review and execution status

The prior adapter and pinned helpers were fully inspected by board_gaps without
finding a static blocker. This update is limited to the two explicit globals;
its inverse byte comparison proves the previously reviewed runtime logic is
unchanged. No new scenario or looser acceptance condition was introduced.

This remains an unexecuted draft, not a userspace result or a lifecycle receipt.
The existing helpers supply transport, RAM decode and verdict logic. No UFS
adapter, disk creation, remote command, QEMU process, build or test was run for
this update. The exact CI binary/source and same-binary diagnostic must still
be supplied by root at any later execution decision.
