# Validation ledger — 2026-11-23 (Asia/Shanghai)

The implemented CPU/GIC/timer/RAM/GENI subset boots an independent Linux
6.12.114 kernel to userspace and executes the same architectural-counter ELF
that ran on the physical TB371FC. **This is an experimental functional subset,
not complete TB371FC or stock-Android emulation.**

## Recorded checks

| Layer | Observed result | Evidence and meaning |
| --- | --- | --- |
| Live hardware reference and parser | 24 captured DT nodes; parser 21/21 tests | [Raw hardware capture](android-board.json), [derived profile](board-profile.json), [parser tests](../scripts/test_board_profile.py). Hardware configuration and decoding, not peripheral behavior. |
| Running QEMU DT/QOM | 20/20 required checks; 3 explicit deviations | [Machine result](machine-profile-validation.json), [exported DT](tb371fc-generated.dts). Queries the running binary, exports its DT, and compares with the independently captured profile. |
| GENI FIFO and interrupts | 13/13 | [QTest/chardev result](geni-qtest.json). Includes TX/RX, partial words, FIFO pressure, masking/W1C, cancellation and reset. |
| GENI migration | 6/6 | [Migration result](geni-migration.json). Actual two-process QMP migration preserves pending state and resumes UART traffic. |
| Runtime-validator regression | 19/19 tests | [Validator tests](../scripts/test_validate_machine.py). Includes negative map/IRQ/counter cases and real dtc/fdtget handling of a DTB containing legal FDT_NOP tags. |
| Native Android EL0 probe | 6/6 consistency checks from one execution | [Native result](android-arch-probe.json), [probe source](../tests/arch-probe.S). Exit 0, 19.2 MHz counter and forward progress on the actual tablet. |
| Linux guest | 8/8 guest checks; exit 0 | [Boot result](linux-smoke.json), [unaltered serial log](linux-smoke.log). Procfs, sysfs, AArch64, eight online CPUs through PSCI, timer/scheduler progress, fork/wait, board identity and the identical counter probe. |

The two Python test counts are command results observed during development;
their links identify the test implementations. No persistent raw unit-test log
is included here, and no CI run is claimed by this ledger. These heterogeneous
counts must not be added into a hardware-completion percentage.

## Linux input and console handoff

The canonical Linux run started at **2026-11-22 22:47:01 UTC** (06:47:01 in
Asia/Shanghai) and completed in 3.554 seconds. Its command omits `keep_bootcon`.
The serial log records the early GENI boot console being disabled before PID1
and all eight guest checks continue through the full `ttyMSM0` console.
The native and guest ELF hashes are identical. Both report **19,200,000 Hz**
and a progressing counter; their tick deltas are not expected to match.

The kernel comes from the independently obtained Linux 6.12.114 source archive,
with no patches applied by the clean build script. The exact source/archive,
compiler and image hashes are in [kernel build metadata](linux-smoke-kernel-build.json).
The [configuration fragment](linux-smoke-kernel-fragment.config) and
[effective configuration](linux-smoke-kernel.config) are retained. This is a
purpose-built smoke-test kernel, not a tablet installation image.

An earlier research-kernel attempt timed out before the guest checks. Its
[failure result](linux-research-kernel-failure.json),
[serial log](linux-research-kernel-failure.log) and
[source manifest](linux-research-kernel-source-manifest.json) remain intact.
The clean-kernel success establishes a working baseline; it does not by itself
identify the exact cause of that earlier failure.

## Limits that remain

- CPU0–3 use upstream Cortex-A55; CPU4–7 use Cortex-A76 as an explicit substitute
  for the real A77-derived cores. MIDRs, microarchitecture and timing differ.
  The Linux log also enumerates secondary logical CPU numbers in DT node order,
  not the Android logical numbering; the MPIDR values themselves are checked.
- PSCI is QEMU's service. The DT/QOM gate does not attest Qualcomm secure
  firmware, all chip registers or physical carveout semantics. The observed
  69 MiB RAM gap is reproduced as an OS-visible exclusion.
- The upstream GENI compatible and timer's legacy CPU-mask flag normalization
  are explicit deviations in the machine report. UART tests cover this FIFO
  model, not silicon timing or a complete GENI DMA engine.
- No full Android boot, Adreno GPU, DPU/display, UFS, Wi-Fi/Bluetooth, touch,
  pen, keyboard, audio, camera, PMIC or suspend/resume equivalence is established.
  No new kernel was booted or flashed on the physical tablet during these checks.

## Publication integrity

The canonical Linux result/log and kernel metadata/configuration files were
reviewed and copied **byte for byte**, including the serial log's line endings.
They contain test commands, generic container paths and build identities, not
credentials, private input events or device-specific identifiers. No redaction
or rewriting was applied to those files. Earlier QMP/QTest reports list their
own limited publication transformations in their `publication` fields.

The machine, GENI, migration and Linux results identify the same QEMU binary
SHA-256: `baea240b28c1428dfd42a27ad9248455dc3095f18ddb0ab0e4c7261085282a5e`.
The running package string was `v11.1.3-1-gfea2412-dirty`; it is preserved rather
than presented as a later committed build. Use [SHA256SUMS](SHA256SUMS) to check
the published files; kernel/initramfs/probe binary hashes remain in the run
records even where those binaries are not committed.
