# Kona firmware devfreq: explicit L3 request reference

This device implements the finite firmware-handoff register interface consumed
by the pinned Kona devfreq driver. It provides a declared synthetic frequency
table and a mutable performance **request**. It does not implement firmware,
a cache clock, cache execution timing, voltage control, bandwidth, thermal
limiting, or hardware vote aggregation.

The default is unknown. Select the reference explicitly:

~~~sh
-global qcom-devfreq-fw.enabled-reference=on
# Optional known initial request, default 0; legal values 0..3:
-global qcom-devfreq-fw.reference-boot-perf-index=0
~~~

The board always creates /machine/devfreq-l3 as a SysBus device. Keeping it
present while unknown permits both directions of same-implementation profile
mismatch to be rejected during migration. It does not fill a surrounding
4 KiB aperture with memory.

## Primary source and actual device tree

The controller source is
[OnePlusOSS/android_kernel_oneplus_sm8250, commit
94304e12a3509eac7b3716bf574ce0b1fcaacbae,
drivers/devfreq/devfreq_qcom_fw.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/devfreq/devfreq_qcom_fw.c).

- Source bytes: 7517.
- SHA256: 745457c2a20c90521772ef29c5f572570f481541d01ef427707d3da35bc5eb16.
- Git blob: 87e58971df652c021e11c2b54ef2da5a70204df7.

The bounded, read-only capture of the exact production DT and matching kernel
symbols is [Git blob
777468d5612057ebd2394bd80b021d79762e9365](https://api.github.com/repos/cyjin-yl/qemu-tb371fc/git/blobs/777468d5612057ebd2394bd80b021d79762e9365).
Its 6381 bytes hash to
3b4d663de9e5ea33f5a86fc7d9f49f26223a6217657594423691d59f0ded124b.
The input DTB is 973052 bytes, SHA256
5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207.
The capture is distinct from the input DTB and contains no read of the actual
hardware LUT, ENABLE, or PERF state. The independent
[source/DT review](https://api.github.com/repos/cyjin-yl/qemu-tb371fc/git/blobs/0e12d22da7a6eb3afc4876f5d450c47f95a690eb)
records these evidence boundaries.

The DT has one /soc/qcom,devfreq-l3 parent with compatible qcom,devfreq-fw,
one address cell and one size cell:

| Resource / sysbus index | Physical range, half-open | Size | Access |
| --- | --- | ---: | --- |
| en-base / 0 | 0x18590000–0x18590004 | 4 | Read-only 32-bit ENABLE |
| ftbl-base / 1 | 0x18590100–0x185901a0 | 160 | Forty read-only 32-bit LUT rows |
| perf-base / 2 | 0x18590320–0x18590324 | 4 | Read/write 32-bit request index |

There are exactly **3 windows, 42 registers, 168 mapped bytes**. The final
table row is at 0x1859019c. Holes between these resources remain unassigned.
The nearest CPUFreq domain begins at 0x18591000 and is not shadowed or reused.

The node has no table-stride override, clocks, interrupts or power properties.
The driver defaults its file-level table stride to four bytes. Its four
qcom,devfreq-fw-voter children have no register resources:
CPU0, CPU4, CPU7 and CDSP use phandles 0x56/0x59/0x5a/0x8e.

The production symbol capture identifies devfreq_qcom_fw_driver_probe at
0xffffff8008cffe50; the observed exception PC 0xffffff8008cffee4 is offset
0x94. The directly logged failed physical read is 0x18590000, matching the
first ENABLE read. This source correlation and resource geometry are not a
measurement of the firmware's register contents.

## What the driver actually consumes

The pinned source establishes these software-visible operations:

- Lines 148–198 map the named enable resource and read bit 0. The driver
  expects firmware to have enabled the interface; it does not write that bit.
- Lines 102–145 read up to 40 table rows. Bits 31:30 select the formula:
  any nonzero source uses the literal 19,200,000 times low8; source zero uses
  the literal 300,000,000. These are driver constants, not a measured Clock
  input.
- The table ends at the first adjacent repeated **frequency**. The source
  comment mentions core counts, but the actual condition at line 135 does not
  read or compare any core-count bits. CPUFreq's different termination rule
  and voltage table do not apply here.
- Lines 41–80 write a whole performance index after finding the requested OPP.
  Voter children compute a maximum across software list entries, then issue
  one parent PERF write. There is no per-voter hardware register.
- Lines 83–100 read PERF and clamp the index in software before indexing the
  frequency table. That clamp is not evidence for hardware saturation of an
  invalid MMIO write.
- Lines 280–286 populate the children and skip direct devfreq registration
  when children exist. No IRQ, counter, completion handshake or delay
  register is part of this interface.

## Explicit reference data and state

Enabling the model declares a synthetic firmware handoff: ENABLE reads 1.
It is not a claimed firmware execution result or a measured bootloader state.
The table has four legal request states, all using source 1:

| PERF index | LUT word | L | Requested nominal Hz |
| ---: | ---: | ---: | ---: |
| 0 | 0x40000010 | 16 | 307200000 |
| 1 | 0x40000020 | 32 | 614400000 |
| 2 | 0x40000040 | 64 | 1228800000 |
| 3 | 0x40000060 | 96 | 1843200000 |

Rows 4 through 39 repeat row 3. The driver therefore sees row 4 as its first
terminal duplicate. These trailing physical rows are readable but are not
legal PERF selections. There are no voltage or CPU core-count fields in the
reference words. The identity tag is 0x44463031 (DF01).

reference-boot-perf-index accepts 0 through 3. In the unknown profile it must
be zero. A reference model starts with that exact request. A system reset
restores it; it does not perform a firmware boot sequence.

A legal PERF write replaces the whole stored request immediately, including
writes to a lower index. No hidden maximum is retained. A guest may perform
its documented software-voter calculation and write the final index, but
that does not create four additional hardware voters in the model.

There is no QEMU Clock input or output. requested-frequency-hz is the driver's
nominal table formula; it cannot be interpreted as an effective cache
frequency, TCG execution rate, measured XO source or achieved bandwidth. The
driver has a source-zero formula, but this finite reference ROM contains no
source-zero row and does not claim to emulate its physical parent source.

## Access and observation boundaries

Only aligned 32-bit device transactions are supported. ENABLE and the entire
table are read-only. PERF writes outside 0..3, including a terminal index or
reserved high bits, return a memory transaction error without changing the
request. The unknown profile rejects all MMIO and runtime QOM observations.
Geometry and configuration metadata remain readable while unknown.

QEMU's memory core diagnoses narrow/misaligned accesses before these device
callbacks when its size/alignment rules apply. Its address-space interface
may split a wider host request into multiple transactions. Atomic rejection
applies to one device transaction, not rollback of already-completed words
before a later access to a hole.

The three memory-region names are qcom-devfreq-fw-enable,
qcom-devfreq-fw-frequency-table and qcom-devfreq-fw-performance.

The device uses no test-only injection property. Normal TCG and QTest expose
the same pure, read-only observations:

| QOM names | Type / meaning |
| --- | --- |
| instance-id | uint32, fixed 0 |
| enable-address, frequency-table-address, performance-address | uint64, actual DT addresses |
| enable-resource-size, frequency-table-resource-size, performance-resource-size | uint32, actual DT sizes |
| lut-row-size, lut-max-entries, valid-perf-count | uint32, 4 / 40 / 4 |
| reference-lut-identity, reference-nominal-xo-hz | uint32, DF01 / 19200000 |
| mapped-window-count, mapped-register-count, mapped-bytes | uint32, 3 / 42 / 168 |
| firmware-enabled | bool, reference ENABLE value only |
| performance-index, requested-lval | uint32, current stored request |
| requested-frequency-hz | uint64, nominal software table interpretation |
| lutN-frequency-word, lutN-nominal-frequency-hz, N=0..39 | uint32 raw word / uint64 nominal rate |

The usual qdev properties enabled-reference and reference-boot-perf-index
cannot be changed after realization. None of these getters advances time,
changes a request, or triggers a guest transaction.

## Reset and migration

The SysBus attachment registers the device in the system reset tree. Reset
changes only PERF, returning it to the declared boot index (zero while
unknown). No timer, Clock, IRQ, guest firmware state, or hidden completion
state exists.

VMState is qcom-devfreq-fw, version 1, minimum 1. Its complete payload is
**72 bytes**, packed as big-endian >II3Q10I:

| Byte offset | Size | Field / rule |
| ---: | ---: | --- |
| 0 | 4 | profile marker, EQUAL |
| 4 | 4 | instance 0, EQUAL |
| 8 / 16 / 24 | 8 each | enable / table / performance bases, EQUAL |
| 32 / 36 / 40 | 4 each | enable / table / performance sizes, EQUAL |
| 44 | 4 | row stride, EQUAL |
| 48 | 4 | reference boot PERF index, EQUAL |
| 52 | 4 | LUT identity, EQUAL |
| 56 | 4 | table entries, EQUAL |
| 60 | 4 | valid states, EQUAL |
| 64 | 4 | nominal XO constant, EQUAL |
| 68 | 4 | current PERF index, validated after load |

The first 68 bytes are configuration equality checks, not incoming replacement
configuration. Both reference/unknown mismatch directions and differing boot
requests are rejected. A runtime index >=4 is rejected with the exact message
"qcom-devfreq-fw: invalid migrated performance index". A nonzero runtime PERF
in the unknown profile is rejected with
"qcom-devfreq-fw: invalid migrated unknown state".

There are no deadlines, input epochs or outputs to reconstruct after load.
The first QOM or MMIO read sees the restored request without a repair side
effect. Compatibility is limited to this device's VMState 1 contract; older
binaries without this device and full-board migration with unrelated devices
are not covered.

## Acceptance boundary

The independent component test is to exercise every real ROM word, all legal
request changes including decreases, read-only/unknown/illegal accesses,
reset, normal TCG observation and real file migrations. Source/target state
must be read before target MMIO can affect it, and negative transfers must
identify the specific configuration or state violation.

This document describes the implementation contract. It is not a declaration
that a new CI build or production-kernel run has passed. The root evidence
ledger separately binds actual binary hashes, unchanged production Image/DT,
source-clean receipts, migration bytes and subsequent native outcomes.
Successful request register traffic alone does not establish firmware,
cache operation, native userspace, Android or whole-tablet equivalence.
