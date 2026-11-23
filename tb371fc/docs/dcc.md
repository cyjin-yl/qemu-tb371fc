# Qualcomm DCC v2 SRAM capture subset

The DCC model implements software-triggered **read descriptor capture into
SRAM**, including actual guest physical memory/MMIO transactions. It is not
a complete Data Capture and Compare, CoreSight or trace implementation.
Its input address map comes from this TB371FC's captured device tree; its
identity/geometry is explicitly a reference configuration.

## Evidence and configuration

The native DT node `/soc/dcc_v2@1023000` supplies:

| Property | Captured value |
| --- | --- |
| Compatible | `qcom,dcc-v2` |
| Register aperture | `0x01023000 + 0x1000` |
| SRAM aperture | `0x0103a000 + 0x6000` |
| SRAM internal word-address offset | `dcc-ram-offset = 0x1a000` bytes |
| DT-configured lists | 3, 6 and 7 |
| Interrupt property | Absent |

A read-only live check found the platform driver bound at
`/sys/bus/platform/devices/1023000.dcc_v2`, with `ready=0`,
`enable=1` and `curr_list=7`. No trigger, configuration or ownership
attribute was written. Those sysfs attributes do not expose the hardware
revision, HW_INFO, register map selector or exact number of lists.
The running tablet exposes no `/dev/mem` or `/dev/kmem` node.

The emulator returns reference `HW_INFO=0x7f`, selecting the driver's map2
and fixed loop-offset indication, `LL_NUM_INFO=8`, and an explicitly
unspecified revision `HW_VERSION=0`. **These are not values read from the
tablet.** SRAM contents and list ownership begin empty; the production
driver programs them from its unchanged DT. The model does not seed the
tablet's existing capture contents or pretend to reproduce its prior state.

The supplied production Image independently confirms the HW_INFO selection
logic at `dcc_probe`: low six bits equal to `0x3f` select map2 and a read of
`LL_NUM_INFO`; otherwise it selects eight old-map lists. Bit 6 selects a
16-bit loop offset. The private disassembly and input Image remain private;
this does not establish identity of the full community driver source.

## Implemented behavior

Eight independent list banks contain ownership, configuration, descriptor
and destination pointers, timeout programming and software trigger state.
The captured SRAM aperture supports ordinary byte, halfword, word and
doubleword accesses, reset retention and QEMU RAM migration.

A trigger schedules work on QEMU virtual time. The default 1000 ns delay is
a testable ordering parameter, **not measured silicon timing**. The complete
descriptor sequence is decoded and bounded before any source transaction:

- ADDRESS selects the physical base and resets the relative word cursor.
- LINK contains one or two 15-bit relative-offset/length pairs.
- The first pair advances from the preceding word, and a second nonempty
  pair continues from the last word read by the first.
- The driver's single-pair padding is accepted.
- An all-zero LINK payload terminates the sequence.
- Every source word is read through the linked QEMU address space and its
  bytes are written to the actual SRAM region. AHB/APB address selectors use
  the same physical map; this does not model distinct bus timing.

The model rejects unterminated lists, escaped pointers, destination
overflow, capture/descriptor overlap and missing address descriptors. A
source transaction failure stops the transfer; already copied words remain
visible. Unsupported or invalid requests remain busy and produce a
diagnostic. No fabricated zero payload, successful completion or guessed
silicon error-code taxonomy is returned.

Running list bases and mode changes are protected against reprogramming.
Linux's trigger-gating bit changes remain valid around a software trigger.
Releasing list ownership cancels pending work in this functional model.
Reset cancels pending work and clears the register banks while retaining
SRAM. These lifecycle choices are explicit model contracts and still need
physical reset/ownership differential validation.

## Unsupported modes

CRC, ATB output, CTI/hardware triggers, loops, write descriptors and
read-modify-write descriptors are not executed. They cannot silently pass
as read captures. A CRC/ATB trigger or unsupported descriptor remains busy
until canceled/reset. Stored CTI bits alone do not cause a capture.

The cited drivers clear interrupt-status bits 0–2 and use bit 1 for CRC
mismatch, but they do not establish a complete capture-completion interrupt
contract. This version therefore does **not** invent a completion IRQ.
The actual native DT has no DCC IRQ route. Nonzero interrupt enables are
retained and explicitly diagnosed as unsupported delivery. Captured cursor,
CRC and undocumented identification/status fields also remain diagnosed
unimplemented accesses.

The DT's real list 3 includes operations and peripheral addresses outside
this subset, and lists 6/7 capture currently unmodeled devices. Successful
native driver probe does not prove those complete lists execute correctly.

## Tests and migration

```sh
python3 tb371fc/tests/test_dcc.py \
  --qemu /absolute/path/qemu-system-aarch64 --out /work/dcc-new-result
```

The suite uses real QTest, actual device MMIO/RAM, virtual timer transitions,
failure cases and two separate QEMU processes for pending-capture migration.
It never mocks a register handler. Its machine disables UFS; it cannot
establish full-board migration with in-flight storage.

The RAM region migrates through QEMU's normal RAM mechanism. Register banks,
busy/stalled state and deadlines migrate through VMState. The migration
stream checks reference geometry and capture-delay configuration; the
destination reschedules pending work and continues from the retained SRAM.
The test receipt, when run, identifies the exact QEMU and test hashes.
The existence of this test or document is not a passing receipt.

## Primary and corroborating sources

- [Qualcomm DCC v2 driver, Google kernel/msm snapshot
  ead04c98fd162cf2e0059f6fc69b33206ab36493](https://android.googlesource.com/kernel/msm/+/ead04c98fd162cf2e0059f6fc69b33206ab36493/drivers/soc/qcom/dcc_v2.c)
  establishes the v2 descriptor encoding, software trigger protocol, list
  ownership, SRAM word addressing and polling completion.
  SHA256: `4593e9ff29ac43e7f3552ed38cd8c20eaee7df9ab9f52d0ded43d1a3222eb91f`.
- [Lenovo community driver candidate at
  9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/dcc_v2.c)
  provides map2 register offsets and HW_INFO detection. It is a
  corroborating source, not a proven complete source match to the installed
  production kernel.
  SHA256: `b6caa16d9b6aef305536465122b54216ab4839ac197e691560930aba06f791cb`.
- [Qualcomm map2 driver published through Google's kernel/msm](https://android.googlesource.com/kernel/msm.git/+/8e58a0d71f888104533e379c22c36a58841673c1/drivers/soc/qcom/dcc_v2.c)
  independently exposes the same map2 offsets and identification logic.


## Executed validation (2026-11-23)

Frozen binary SHA256
`fe71b5c9a65761910294e6cf690f615ca3c8c0da132207860dc4550b5f28668d`
passed **17/17** real checks with process exit zero. The
[result](../evidence/dcc-20261123/result.json) includes a two-process
migration with capture still pending at 400 virtual ns, no output after
another 599 ns, and actual source bytes copied at the final 1 ns boundary.
The destination's independent QTest clock was explicitly aligned before
loading VMState; this is a component migration test, not a physical timing
measurement or whole-board migration.

The initial [paused-harness result](../evidence/dcc-20261123/harness-paused-result.json)
failed because the inherited fixture left QEMU in `-S`, disabling virtual
timer dispatch. After adding the normal QMP `cont` transition, the
[second result](../evidence/dcc-20261123/harness-clock-result.json) passed
16/17 and identified the remaining synthetic-clock alignment requirement.
The final complete run used the **same** binary; no model assertion or
capture validation was weakened. These failed receipts remain as history.

The supported actual read-DMA cases pass, and unknown source registers
propagate a failed memory transaction rather than a successful zero word.
The negative test retains a nonzero destination sentinel and requires the
request to remain incomplete.
