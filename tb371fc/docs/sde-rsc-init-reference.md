# SDE RSC v3 initialization reference

The machine option `sde-rsc-init-reference=on` adds a **passive initialization
reference**, with profile ID `0x52534301`. The default is off. Enabling the option
requires an existing RPMh controller (`rpmh-profile=reference` or a valid file
profile); it is immutable after machine creation. The device is not independently
user-creatable. This option is not a claim of a complete display resource
controller, measured silicon configuration, or a working display.

The reference stores 63 explicitly listed configuration/status words and 17
sequencer words. It does not execute those sequencer instructions, run a solver,
advance a local timer, generate VSYNC, perform a PDC transaction, signal a new
interrupt, collapse power, or acknowledge bandwidth changes. Active requests
return `MEMTX_ERROR` without changing any register. The accepted value subsets
and all-zero reset state are implementation choices. Unsupported values are
rejected; they are not described as silicon reserved bits or RAZ/WI behavior.

## Evidence and initial failure

The fixed primary reference is
[OnePlus SM8250 kernel 98719830b0de5584f5588bbe9f4c8304d97a64c6](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/tree/98719830b0de5584f5588bbe9f4c8304d97a64c6),
specifically `techpack/display/msm/`:

| Source | Git blob |
|---|---|
| `sde_rsc_hw.h` | `719962a3f627dd8d20059b8e23540681e4d591e1` |
| `sde_rsc_hw_v3.c` | `d3a589ccf301837d39a485fe5ca0f8087af0dbad` |
| `sde_rsc_hw.c` | `c624c868eeb6bb2f9464d9be3bebbb5cb7b053a4` |
| `sde_rsc.c` | `b6397a96508fc43449d39cd45eac0cb841d54961` |
| `sde_rsc_priv.h` | `4779506a28143a9e5e074ab9da6b25698701244f` |
| `sde_io_util.c` | `09649c59819a7bda5e20de9bd09c6415628caf77` |

The preserved original DTB has SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
Its `/soc/qcom,sde_rscc@af20000` node selects `qcom,sde-rsc`, version 3,
two DRAM channels, and these resources:

| Resource | Physical base | Size |
|---|---:|---:|
| `drv` | `0x0af20000` | `0x3c50` |
| `wrapper` | `0x0af30000` | `0x3fd4` |

The independent `/soc/rsc@af20000` node describes the existing RPMh display DRV
at the same `0x0af20000` base, size `0x10000`, with TCS offset `0x1c00`.
These are shared views of the DRV address space, not two independent banks.

The completed native009 diagnostic used source
`cf5281fe8734d2c97b904191576865034396cc02`, tree
`428aa0e7a62dae8c27d055f26513e1be13ce5bcb`, and merge
`6aedaa76ba6207a23c9b739b387860f3b096bf86` (PR58). Its original kernel,
DTB, initramfs and boot arguments were preserved. It passed the previous TPDM
read failure, then stopped at guest time 6.268751 seconds with exactly one
QEMU invalid-memory diagnostic: a 4-byte write to `0x0af31fe0`.

The actual original Image contains `dss_reg_w` at `0xffffff8008f35490`;
offset `+0x38` is `STR W2,[X9]` (`0xb9000122`). The call from
`rsc_hw_init_v3+0x44` supplies offset `0x1fe0` and value `0xffffffff`,
matching `SDE_RSCC_QTMR_AC_HW_FRAME_SEL_1`. The recorded LR is the return
address `rsc_hw_init_v3+0x48`. The original Image's full SHA-256 is
`ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f`.

The native diagnostic exited with runner status 2 and QEMU status 0 after an
acknowledged QMP quit. It did not reach Android userspace. Its artifact was
consumed once; this implementation does not reinterpret that failure as a
successful run or authorize reusing the consumed artifact. The original OEM
PBL/XBL/ABL boot chain and physical tablet were not executed or changed.

## Address-space ownership

When the option is off, no SDE RSC child or new MMIO region is created. When it
is on, the wrapper has exactly its original `0x3fd4` size. Ten small DRV
subregions overlay only previously unsupported RPMh offsets:

| DRV offset | Size | Contents |
|---:|---:|---|
| `0x020` | `0x10` | PDC route configuration |
| `0x404` | `0x14` | Inactive sequencer status and branch addresses |
| `0x600` | `0x44` | 17 sequence storage words |
| `0xc00` | `0x7c` | Solver configuration and inactive status, with holes rejected |
| `0x1000` | `0x10` | Four time-slot configuration words |
| `0x1080` | `0x04` | Mode priority for slot 0 |
| `0x1100` | `0x04` | Mode priority for slot 1, priority 0 |
| `0x110c` | `0x04` | Mode priority for slot 1, priority 3 |
| `0x1180` | `0x04` | Mode priority for slot 2, priority 0 |
| `0x118c` | `0x04` | Mode priority for slot 2, priority 3 |

DRV identity (`0`, `4`, `0xc`), wake configuration (`0x38`, `0x40`), IRQ
registers, both display TCS banks and all APPS DRVs remain owned by the existing
RPMh device. Its reference ID `0x00020700` remains unchanged. In particular,
ID register 4 still advertises no autonomous solver. The standard branch
offsets `0x410` and `0x414` correspond to that existing reference ID; this model
does not advertise either revision requiring the vendor's `+0xf0` workaround.

Every access to a new region must be aligned and exactly 32 bits. One-byte,
two-byte, eight-byte and unaligned requests are rejected before any state
change. Both the QEMU valid and implementation width ranges preserve the
original malformed transaction for this check. An address inside a window is
not sufficient: it must also occur in the following allowlist or the bounded
sequence store. All holes fail.

## Register contract

All offsets below are relative to their named window. Every implemented word
resets to zero. A readable configuration word returns the stored value. No
mask silently discards an unsupported request.

| Window | Offsets | Accepted writes and behavior |
|---|---|---|
| Wrapper | `0x000`, `0x020`, `0x040`, `0x048` | Zero only. Nonzero CTRL, force-power/event, override-source or bandwidth requests are unsupported. |
| Wrapper | `0x004` | `0` or `0x100` only; inactive double-buffer configuration. Active override bits fail. |
| Wrapper | `0x008`, `0x00c`, `0x044` | Passive 32-bit static-wakeup/threshold configuration. |
| Wrapper | `0x010`, `0x018`, `0x01c`, `0x024`, `0x050` | Read-only zero: no debug completion, VSYNC timestamp, power-collapse completion or bandwidth ACK. Writes fail, including writes of zero. |
| Wrapper | `0x1040`, `0x1044` | Access configuration `0` or `1`; no other access policy is implemented. |
| Wrapper | `0x1fe0`, `0x1ff0` | Reset `0` or initializer's `0xffffffff` frame selection. |
| Wrapper | `0x2020`, `0x2024`, `0x3020`, `0x3024` | Two low/high compare pairs; arbitrary 32-bit storage while the corresponding CTL is zero. If CTL is one, only an unchanged all-ones pair is accepted. |
| Wrapper | `0x202c`, `0x302c` | `0`, or `1` only when both corresponding compare words equal `0xffffffff`. No counter, interrupt or expiry is implemented, even for this sentinel. Other CTL bits fail. |
| DRV | `0x020`, `0x024`, `0x028` | Aligned 32-bit PDC target-address configuration; no PDC transfer. |
| DRV | `0x02c` | PDC slave configuration `0` or `1`. |
| DRV | `0x404`, `0x408`, `0xc24`, `0xc28`, `0xc2c` | Read-only zero, reflecting no sequence or solver execution. All writes fail. |
| DRV | `0x410`, `0x414`, `0xc30`, `0xc4c`, `0xc68` | Aligned byte addresses `0..0x40` within the 17-word sequence store. |
| DRV | `0x600..0x640`, every 4 bytes | 17 independent 32-bit storage words. They are never interpreted or executed. |
| DRV | `0xc00` | Passive considered-timer selection `0` or `1`. |
| DRV | `0xc04`, `0xc08`, `0xc0c`, `0xc1c` | Passive 32-bit wake, duration and idle-time configuration. |
| DRV | `0xc14` | Zero only; an active solver override is unsupported. |
| DRV | `0xc18`, `0x1080`, `0x1100`, `0x110c`, `0x1180`, `0x118c` | Passive mode/priority values `0..2`. |
| DRV | `0xc20` | Passive mode bitmap `0..7`; storing it does not activate a solver. |
| DRV | `0xc34`, `0xc50`, `0xc6c` | Mode parameter 1: reset `0` or initializer's `0x80000000`. |
| DRV | `0xc38`, `0xc40`, `0xc54`, `0xc5c`, `0xc70`, `0xc78` | Passive 32-bit backoff parameters. |
| DRV | `0x1000`, `0x1004`, `0x1008`, `0x100c` | Passive 32-bit time-slot parameters. |

The enable-with-finite-compare guard applies equally to changing either half
of an enabled compare and enabling an already finite compare. Validation uses
a proposed copy of the complete configuration before committing the one write,
so a rejected transition leaves the entire state unchanged. Ordinary migration
stores the profile identity, all 63 words and all 17 sequence words. The loader
validates the same supported values and cross-register timer condition; status
words cannot be injected as nonzero through migration.

## Validation scope

`tb371fc/tests/test_sde_rsc.py` uses the existing real QMP/QTest transport. It
has 12 required scenarios and four separately reaped QEMU children, including
an actual expected realize failure with RPMh disabled. It checks default-off
behavior, immutable QOM properties, exact flat memory maps, the fixed vendor
initialization order, state preservation after rejected transactions, the timer
boundary, reset, and one unmodified process checkpoint with subsequent writes.

QTest acknowledges a memory command with `OK`; its protocol does not expose
the underlying `MemTxResult`. Each of the 60 rejected accesses is established
by the exact device diagnostic and unchanged state, together with the fixed
source path that returns `MEMTX_ERROR`. The test does not claim that it directly
received a bus-error code through QTest.

The isolation check executes an ordinary display RPMh TCS request using the
existing resource service. It observes the original 1000 ns completion delay,
command status and IRQ-status clear while SDE configuration is present. It
does not claim to test a new SDE IRQ output. Advancing virtual time produces no
new SDE status, timer expiry, program-counter movement, ACK or power transition.

The three QOM writes in the test are deliberately rejected attempts to mutate
the machine option or read-only profile ID. All device configuration changes
use real MMIO. No malformed migration payload is generated by this fixture;
the migration negative-state guard is a source-level property, separately from
the actual ordinary migration coverage.

The workflow adds an independent step,
`Check opt-in SDE RSC initialization reference and checkpoints`, without
changing the original 111 component and five Linux gates. Result, process,
QMP/QTest protocol, standard-output/error and memory-map evidence are uploaded;
the raw migration stream is excluded. A test definition or successful source
review is not a completed test run. Actual build, CI and future native results
must be reported from their own receipts and source/artifact identities.

A subsequent original-input native diagnostic must select the new option
explicitly, use a new artifact, and record the updated model configuration.
This document does not assert that such a run has happened or that the display
or Android userspace works.
