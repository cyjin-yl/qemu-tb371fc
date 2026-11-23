# GPU CX GDSC digital controller

`qcom-gpu-cx-gdsc` models the normal software/votable control path for the
TB371FC GPU CX power domain. A software enable request, an aggregate external
vote, modeled parent availability, and completed feedback are separate state.
Neither a nonzero RPMh request nor this device's feedback is a measurement of
physical voltage.

## Captured topology and source evidence

The captured device tree identifies:

| Item | Captured value |
| --- | --- |
| Node | `/soc/qcom,gdsc@3d9106c` |
| Compatible / regulator | `qcom,gdsc` / `gpu_cx_gdsc` |
| GDSCR resource | `0x03d9106c`, four bytes |
| Status syscon | phandle 113, `/soc/syscon@3d91540`, four bytes |
| GDSC phandle | 379 |
| Both parent supply references | phandle 102, RPMh `pm8150_s3_level` (CX) |
| Clock-disable wait field | 8 |
| Driver polling timeout | 500 microseconds |
| Flags present | `qcom,no-status-check-on-disable`, `qcom,retain-regs` |

Its seven direct supply consumers are the KGSL GPU, GMU, KGSL SMMU, one
CoreSight funnel, one TPDM, and two CTIs. The capture's GPUCC aperture at
`0x03d90000/0x9000` and GMU aperture at `0x03d6a000/0x30000` overlap
the two registers. This is one physical controller; future GPUCC/GMU register
views must use the same device state.

The native production-kernel failure reached `gdsc_probe` immediately
after linking the regulator parent and attempted its initial GDSCR read.
The physical address comes from the captured resource, independently of the
fault's virtual address. Private kernel logs remain private.

The [official Android driver][official] defines GDSCR power feedback at bit 31,
HLOS collapse at bit 0, hardware control/override at bits 1/2, retention request
at bit 11, and clock-disable wait at bits 15:12. Enabling clears collapse and
polls the separate status syscon when present. The driver explicitly checks
the HLOS vote independently because a different voter can hold the domain on.
Its polling loop does not guarantee a silicon transition duration.

The [official Kona DT][kona] corroborates the two addresses and CX parent.
The [upstream SM8250 GPUCC driver][gpucc] declares the CX GDSC as votable, with
offsets `0x106c` and `0x1540` within GPUCC. These sources do not
establish reset register contents or external firmware vote state.

A [fixed third-party mirror][mirror] was also compared with the official
driver. Its SHA-256 is
`e2f0e4269e1a81b2680deb15cc2c3b11ed55611831640b0432d3772293d777e7`;
the official downloaded file is
`1018185ce92e333ac7b94d1058f5411a73635a4270446ba6a38dea2524e6696b`.
The mirror adds debugging and changes some skip-disable/mode handling; the
register definitions and normal CX control path agree. It is not represented
as the verified exact source of the production kernel.

This CI checkpoint carries a [reduced topology summary][ci-topology] with
the retained, checked geometry and source hashes.
`full_allowlisted_capture_included` is explicitly false. The complete
51,611-byte topology file has SHA-256
`b307c39863d0c8eb3b6c09e422c8229cebd435edff9436490212de67853e2ba1`
and remains saved on doesvm at
`/work/gdsc-next-draft-20261123/patch/tb371fc/evidence/expansion-20261123/gpu-cx-gdsc-topology.json`.
The original allowlisted extraction is
`/work/gdsc-next-draft-20261123/dt-selected.json`, SHA-256
`ba145ab155d846bff87d0d4d6590dc8c7d20c0ac3ceb3de12339e26ca77777c0`.
Both complete originals await later archival when the device connection
resumes; this summary does not reproduce the omitted capture payload.

The checkpoint keeps the C model unchanged and includes the two-helper
test correction: known input changes preserve validity, while an explicit
`None` drives validity low. CI results must provide their own actual outcome;
the checkpoint and this summary do not assert a complete native boot or a
fully passing test run.

[ci-topology]: ../evidence/expansion-20261123/gpu-cx-gdsc-ci-topology-summary.json

## Register behavior

The device exports two independent four-byte MMIO regions:

| MMIO index / physical address | Supported behavior |
| --- | --- |
| 0 / `0x03d9106c` | GDSCR request/configuration plus the completed power feedback bit |
| 1 / `0x03d91540` | Read-only completed feedback at bit 31; other modeled reserved bits read zero |

GDSCR accepts bit 0 and fields 11, 15:12. Its bit 31 is read-only: the driver
may carry it back through a read-modify-write, but that write cannot force a
power-on indication. Unsupported mode bits 1/2 and unknown configuration bits
reject the entire device access before modifying a request, deadline, or
output. The status window rejects writes.

The retention and wait fields are latched configuration requests. The
current device does not contain GPU register banks or memories whose physical
retention could be established, and does not infer detailed sequencer timing
from the wait field. Normal collapse preserves the controller's configuration.

Only aligned 32-bit little-endian accesses are supported. Subword accesses
within either window are diagnosed and rejected. A wider transaction crossing
a four-byte region can already have been split by the system memory layer;
no whole-transaction rejection or atomicity is claimed across the boundary.

A known control request remains programmable and readable when the modeled
parent is off, provided feedback is known. Parent availability is not used to
invent an MMIO bus gate. If the complete requested register contains an
unknown component, the read returns `MEMTX_ERROR` with a diagnostic.
The separate status window can be known even when the control configuration
is still unknown.

## Three-valued inputs and actual feedback

Named one-line inputs are:

- `parent-known` and `parent-on`: validity and modeled parent
  resource availability.
- `external-known` and `external-vote`: validity and the aggregate
  non-HLOS request. This is an integration adapter, not an implementation of
  TZ/HYP firmware or proof that every physical voter is modeled.

The HLOS request is the inverse of SW_COLLAPSE when the control configuration
is known. The desired state is computed with three-valued logic:

```text
request = HLOS request OR aggregate external vote
desired = modeled parent availability AND request
```

A known true input resolves OR to true; OR is false only when both inputs are
known false. A known false input resolves AND to false; AND is true only when
both inputs are known true. The remaining combinations are unknown.
Consequently, a known-off parent makes the desired state off even if requests
are unknown. Two known absent requests also make it off even when the parent
is unknown. A known HLOS request makes an unknown external vote irrelevant
to the OR result, but does not make an unknown parent available.

Known changes schedule digital feedback after `transition-ns`, default
1000 ns. This is an explicit reference delay, not measured SM8250 timing.
Before the deadline, an old known state remains observable; an initially
unknown status stays unknown until completion. At completion both MMIO
feedback bits and the GPIO output change together.

Repeating an unchanged request or changing only supported configuration does
not postpone an existing deadline. Reversing a request cancels the old
transition. Unknown desired state cancels pending completion and invalidates
feedback. Every timer callback rechecks the current inputs before completing,
so an old enable timer cannot report success after its prerequisites change.

Outputs are `power-on` and `state-known`. Consumers must honor validity.
When invalidating, the device lowers validity first; when establishing known
feedback, it drives the result before asserting validity. These are digital
signals, not analog power-good.

Read-only QOM properties expose the inputs, `control`, `control-known`,
`power-on`, `state-known`, `transition-pending`,
`transition-deadline-ns`, and the summary `state`. A raw control
property is meaningful only when `control-known` is true.

## Unknown reset and explicit reference policy

The default `boot-collapsed-reference=off` leaves control configuration
unknown. The device does not substitute a guessed native reset value.

Explicit `boot-collapsed-reference=on` selects SW_COLLAPSE=1 and zero
supported configuration fields. At realization only, when no external input
has been driven, it also selects the documented reference assumption of no
external vote. It does not assert or make known a parent-on input.

```sh
qemu-system-aarch64 ... \
  -global qcom-gpu-cx-gdsc.boot-collapsed-reference=on
```

This option is a reference environment for diagnosis. It is not a captured
firmware handoff or cold-reset state. A direct supported guest write can
establish the control request in the default profile, but cannot invent
unknown external votes or parent availability.

Reset cancels pending feedback, restores the selected HLOS request policy,
invalidates the old feedback, and recomputes from the retained input levels.
It does not erase a driven external vote or parent state. A held external
vote can therefore bring the model back on after reset even with HLOS
collapsed. The initial reference assumption is never reapplied over a driven
runtime input.

Changes to modeled parent availability preserve the stored controller request.
This is a digital interlock contract, not a claim about register retention
after a physical rail brownout.

## RPMh integration

The published RPMh model has a named `power` output array. At the reviewed
commit `5f8bca77a89e025161f332667dabd6d3cfa0485a`, resource index 1 is
`cx.lvl`. Its output changes after a valid typed command is applied by
the RSC command timer, and represents the maximum of the four modeled voter
requests being nonzero. It is not a measured voltage.

The board connects that output to `parent-on`. It initializes
`parent-known` from the parsed resource's
`res.cx.lvl.present` property and synchronizes the current
`res.cx.lvl.enabled` value after connecting. Resource presence is labeled
as availability of the modeled resource, not independent analog feedback.
If RPMh is absent or the resource is missing, validity remains unknown.

The reviewed board had no existing consumer of RPMh `power[1]`. A later
integration must preserve any existing consumer with a fanout/split rather
than replacing the line. The current explicit RPMh boot configuration seeds
an XO vote only; this device does not silently create a CX boot vote.

Both register windows belong to one device at `/machine/gpu-cx-gdsc`.
Future broader GPUCC/GMU mappings must route these addresses into those same
MemoryRegions. Creating a second independent status or control copy would
break the physical sharing shown in the captured DT.

## Migration and actual test contract

VMState version 1 stores the request and validity, parent/external inputs,
completed feedback, pending target and absolute deadline. The source reset
profile and reference delay are also serialized separately and must equal
the destination configuration. Unknown state, inconsistent pending state,
or invalid control bits are not normalized into success.

Incoming loading removes the destination's startup timer before restoring
state. A pending transfer restores the source deadline instead of starting a
new full delay. Later upstream GPIO restoration can recompute the desired
state, and the timer checks again at completion. Tests exercise the actual
RPMh-to-GDSC wiring and this board's load order.

```sh
python3 tb371fc/tests/test_gpu_cx_gdsc.py \
  --qemu /absolute/path/qemu-system-aarch64 \
  --out /fresh/output/gpu-cx-gdsc
```

The output directory must not exist. The suite uses eight fixed board CPUs,
128 MiB RAM, QTest, and `ufs-enabled=off`. It reuses the already exercised
socket transport in `test_refgen.py`; the receipt hashes both files.
The VM event loop is explicitly continued so virtual timers really dispatch.
GPIO observations drain through an unrelated RAM read without adding a
clock step to a deadline test. The initial low level follows QTest's observer
contract; subsequent raise/lower transitions are verified from real events.

The plan has 26 top-level checks: 18 behavior checks and eight migration
scenarios. The behavior tests include all 27 three-valued combinations,
independent register views, exact reference deadlines, repeated/reversed
requests, loss or uncertainty of prerequisites, external-only votes, both
reset policies, supported configuration, and negative accesses. One test
uses real APPS and display RSC commands and the shared CX output, without
intercepting that connection.

The eight migration scenarios require **nine actual outgoing migration
requests**: six completed process transfers and three destination rejections.
The coupled scenario first transfers an in-flight RPMh command and then
transfers its resulting pending GDSC feedback into a third process. The other
positive scenarios cover unknown state, pending on, pending off, and an
external-only held vote. The negative scenarios check both reset-profile
directions and a different transition delay.

Each actual QMP migration request is recorded at execution time in
`migration_attempts`, including request acceptance, sender status,
receiver outcome, URI, and the relevant diagnostic. These are not generated
from the planned test count. Successful receipts require:

- `expected_checks=26` and 26 passing `checks`;
- `migration_expected_checks=8` and eight passing `migrations` scenarios;
- `expected_migration_attempts=9` and nine actual request records;
- six completed receiver transfers and three specific configuration rejections;
- literal `passed`, `all_passed`, and `success` equal to true.

A sender reporting completion alone does not prove destination acceptance.
The receiver must restore the expected state or reject the intended
configuration with a nonzero exit and the precise diagnostic. Source time is
advanced positively and destination alignment never requests a zero step.

Startup/protocol deadlines, file-backed stderr, owned-process termination,
and retained socket directories follow the existing transport contract.
No test conditions use Python `assert`. QTest does not expose
`MemTxResult` in its memory replies, so rejected-access checks require
diagnostics and unchanged model state; they do not call a zero-filled reply
a successful register read.

This plan does not claim an unperformed run. Actual binary/test hashes and
outcomes are produced by the executable suite. Component migration with
UFS disabled does not establish full-board migration.

## Explicit exclusions

This device does not implement hardware-trigger mode, software override,
GX clamp/AON/reset sequencing, other GDSCs, the debug CFG/CFG2 registers,
complete GPUCC clocks, GPU/GMU execution, external firmware voting policies,
physical memory retention, analog rails, or silicon-accurate sequencing.
Unimplemented mode/configuration writes are diagnosed rather than accepted
as successful operations.

[official]: https://android.googlesource.com/kernel/msm/+/b6410c3f75259c0ca2df45a01838d2e46272723e/drivers/clk/qcom/gdsc-regulator.c
[kona]: https://android.googlesource.com/kernel/msm-extra/devicetree/+/refs/heads/android-msm-bramble-4.19-android11-qpr1/qcom/kona.dtsi
[gpucc]: https://linux.googlesource.com/linux/kernel/git/torvalds/linux/+/aaed66fadba2d2de8fe0daa0aa3eac827d2076b9/drivers/clk/qcom/gpucc-sm8250.c
[mirror]: https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/clk/qcom/gdsc-regulator.c
