# Observing the production SMEM driver's successful probe

An initialized SMEM image in physical RAM proves that the loader seeded the
reference profile. It does not prove that Linux reached or accepted the SMEM
device. Absence of an error message is also insufficient: another earlier
failure or deferred probing may prevent the driver from running.

## Success-path observation

In the [official OnePlus SM8250 source at fixed commit
94304e12a3509eac7b3716bf574ce0b1fcaacbae](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/soc/qcom/smem.c),
`qcom_smem_probe` stores its allocated driver object in the static global
`__smem` only after all header/partition checks and successful
`hwspin_lock_request_specific`. The next statement returns success, with no
further operation that can fail. The remove callback clears this pointer.
The [pinned TB371FC source mirror](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/smem.c)
has the same final assignment and relevant structure layout.

Consequently, a nonzero `__smem` pointing to a consistent live driver object
establishes that this success path executed. The reference handoff does not
initialize that pointer or object. This is a driver-probe result, not evidence
of remote processor services, native SBL state or Android userspace.

Before using this observation on a production binary:

1. Use kallsyms from that exact kernel image and record both input hashes. Find
   `qcom_smem_probe` and `__smem`; do not substitute addresses from a source
   build. Resolve the actual store target from the probe's disassembly if a
   symbol name is ambiguous.
2. Verify the probe's final store to `__smem` and immediate successful-return
   path. Also verify the field offsets below against its object accesses and
   those in `qcom_smem_set_global_partition` and `qcom_smem_map_toc`. The
   source layout is a decoding hypothesis until the production instructions
   confirm it. KASLR must be disabled or its actual slide resolved.
3. Stop the guest through QMP and confirm the stopped state before taking the
   snapshot. Read guest *virtual* addresses with HMP `x`, using a CPU with the
   active kernel MMU context. HMP `xp` reads physical addresses and cannot
   directly follow kernel object or ioremap pointers. A translation/read error
   is an inconclusive observation, not a zero pointer.
4. Read `__smem` as one 64-bit pointer. If zero, report probe success as
   unobserved; possible causes include an earlier stop, deferred probe, a
   probe failure, or removal. If nonzero, validate the live object and its
   pointed-to headers as described below. Keep reads bounded to these
   structures, without scanning arbitrary guest memory.

## ARM64 layout hypothesis for this vendor driver

This table assumes the cited vendor layout: 64-bit pointers and `size_t`,
32-bit `u32`, `SMEM_HOST_COUNT == 11`, a 24-byte `smem_partition_desc`, and a
24-byte `smem_region`. Mainline Linux v6.12 has a different private object
layout despite using the same shared-memory format. Do not apply this table
to another driver version without verification.

Offsets are relative to the pointer loaded from `__smem`.

| Field | Offset | Read width | Expected for the explicit empty reference |
| --- | --- | --- | --- |
| `dev` | `0x0` | 8 | Nonzero kernel pointer |
| `hwlock` | `0x8` | 8 | Nonzero kernel pointer |
| `item_count` | `0x10` | 4 | 512 |
| `ptable_base` | `0x18` | 8 | Nonzero ioremap pointer to `$TOC` |
| `global_partition_desc.virt_base` | `0x20` | 8 | Nonzero ioremap pointer to `$PRT` |
| `global_partition_desc.phys_base` | `0x28` | 4 | `0x80903000` |
| `global_partition_desc.cacheline` | `0x2c` | 4 | 64 |
| `global_partition_desc.size` | `0x30` | 4 | `0x1fc000` |
| `num_regions` | `0x140` | 4 | 1, or 2 if the supplied SMEM node has `qcom,rpm-msg-ram` |
| `regions[0].aux_base` | `0x148` | 4 | `0x80900000` |
| `regions[0].virt_base` | `0x150` | 8 | Nonzero ioremap pointer to the legacy/header page |
| `regions[0].size` | `0x158` | 8 | `0x200000` |

The global descriptor contains padding after its `size` field. Private
descriptors start at `0x38` with stride 24. Their base pointers should remain
zero for this profile because it contains no local/remote private partitions.
The fixed portion of the object ends at `0x148`; the region array follows it.

Follow the checked pointers and decode little-endian fields:

| Pointed-to object | Offset | Width | Expected |
| --- | --- | --- | --- |
| `ptable_base` | `0x0` | 4 | `$TOC`, numeric `0x434f5424` |
| `ptable_base` | `0x4` | 4 | Directory version 1 |
| `ptable_base` | `0x8` | 4 | One directory entry |
| `global_partition_desc.virt_base` | `0x0` | 4 | `$PRT`, numeric `0x54525024` |
| `global_partition_desc.virt_base` | `0x4` | 4 | Host pair `0xfffefffe` |
| `global_partition_desc.virt_base` | `0x8` | 4 | Size `0x1fc000` |
| `global_partition_desc.virt_base` | `0xc` | 4 | Uncached free offset, at least 32 |
| `global_partition_desc.virt_base` | `0x10` | 4 | Cached free offset, no larger than partition size |
| `regions[0].virt_base` | `0x5c` | 4 | Version `12 << 16` |
| `regions[0].virt_base` | `0xc0` | 4 | Initialized value 1 |
| `regions[0].virt_base` | `0xcc` | 4 | Reserved word zero |

Check `32 <= uncached <= cached <= size`. Do not require the allocation
frontiers to remain at their initial empty values after the guest runs; Linux
clients may have allocated legitimate items. The driver stores no separate
`version` field in `struct qcom_smem`; read it through the header-page pointer.

Record the selected machine/mutex profiles, compiled QEMU hash, original
kernel and DT hashes, symbol-resolution method, verified disassembly ranges,
stop state, bounded raw observations and individual validation results. Keep
the final verdict explicit: success path observed, not observed, or decoding
inconclusive. A later kernel exception does not erase an earlier successful
SMEM probe, but it must remain visible as the next boot blocker.

## Recorded production observation: 408d82a

The [allowlisted native diagnostic](../evidence/gdsc-smem-20261123/native-ci-408.json) now records
execution of the production driver's successful publication path under the
explicit empty reference handoff. It used QEMU SHA256
`0190794d7890fcc34caeff3c7f30d00218a6f0027bddc4500c34f79c45051a93`
from the public 408d82a board artifact. The downloaded ZIP and executable
were checked separately; the executable matched the binary hashes recorded
by both the successful 53-component and four-Linux CI suite invocations.
It remained unchanged after the native diagnostic.

The kernel Image was unmodified, SHA256
`ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f`.
The complete DT input SHA256 was
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`;
the board's explicit loader memory fixups still apply at runtime.

| Observation | Recorded outcome |
| --- | --- |
| Production publication instructions | Verified before interpreting the live pointer |
| Published `__smem` | Nonzero, with valid live driver-object pointers |
| Live-state consistency | All 23 checks passed: pointers/lock, item count, directory, partition, region geometry, allocation frontiers and header |
| Snapshot context | Guest paused; virtual-memory reads through CPU 0 |
| Observer writes to guest memory | None (`guest_memory_written=false`) |
| Production allocator operations exercised by this observation | No |
| Native SBL capture or remote firmware | No |
| Complete native-DT or Android boot | Failed later; not established by the probe result |

The selected configurations were SMEM reference-empty, explicit TCSR
unlocked reference, GDSC reference-off, SPMI reference, REFGEN disabled
reference and a partial RPMh command-db file. Loader initialization of
ordinary SMEM RAM is part of that declared profile; the read-only observer
did not create the published kernel pointer or alter driver state.

The previous GDSC abort was absent, and CX/GX supply registration messages
were present. The same run then took a synchronous external abort reading
physical `0x00148198` in GCC's branch-halt polling path:
`gcc_kona_probe` through `clk_branch2_check_halt` to
`regmap_mmio_read32le+0x8`. The diagnostic was stopped by timeout after
25.143 seconds. QEMU/tool exit 0 describes cleanup, not a successful
native-DT or Android boot.

The 23 checks above are a native probe observation. They are separate from
the 22-group QTest handoff/allocator-fixture gate, which executes no guest
CPU. Neither the probe observation nor its successful pointer checks prove
real guest allocations, firmware-populated items, SBL execution, remote
services or GPU operation.

The subsequent GCC implementation tested at 0a617bab adds binding 143 halt
status and binding 18 parent support. Its [public CI](../evidence/gdsc-smem-20261123/ci-0a617ba.json)
and [downloaded-artifact verification](../evidence/gdsc-smem-20261123/artifact-0a617ba.json)
passed, using the distinct QEMU SHA256
`a2e19b188f6253928ad47630896e0967fb99f55abbddaf98212f312e8194ee1b`.
The earlier SMEM success and `0x00148198` abort above remain specific to
408d82a.

## Recorded production observation: 0a617bab

A [separate 0a617bab production receipt](../evidence/gdsc-smem-20261123/native-ci-0a.json)
now records the newer executable with the same unmodified Image and full
DT input. Its 21.166-second diagnostic again observed
`success_path_observed=true`, passed all 23 state checks and recorded
`guest_memory_written=false`. It passed the previous GCC abort, then
panicked on an unsupported read at GCC offset `0xf060`, physical
`0x0010f060`, through `mux_get_parent`, `clk_register`,
`qcom_cc_really_probe` and `gcc_kona_probe`, with ESR `0x96000210`.
The fixed official driver names this the USB3 primary PIPE source mux;
its [production selector decoding and external parents](gcc-sm8250.md#next-observed-boundary-usb3-primary-pipe-source-mux)
remain a separate implementation task.
This independently confirms the SMEM success path on the corrected GCC
build; it does not establish later guest allocation operations, full
native-DT userspace PID 1, Android userspace or secure-firmware services.
