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

## Executable observer

Use the bounded diagnostic runner with the original matching kernel and
kallsyms files. Include `nokaslr` in the kernel command line and select
the SMEM and mutex reference profiles explicitly:

```sh
python3 tb371fc/scripts/diagnose_kernel.py \
  --qemu /absolute/path/to/qemu-system-aarch64 \
  --machine tb371fc,smem-profile=reference-empty \
  --global-property qcom-tcsr-mutex.boot-unlocked-reference=on \
  --kernel /private/path/to/original-Image \
  --initrd /private/path/to/diagnostic-initramfs.cpio.gz \
  --dtb /private/path/to/original.dtb \
  --append 'nokaslr console=ttyMSM0' \
  --legacy-printk-symbols /private/path/to/matching-kallsyms \
  --observe-smem-probe --seconds 30 --out /private/path/to/new-diagnostic
```

Other device profiles may be needed for a particular boot experiment; record
them with its input hashes. The command above does not guarantee that this
kernel reaches SMEM probing.

The observer is deliberately limited to kernel SHA256
`ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f`.
It verifies the three expected symbol addresses and the 16-byte publication
tail in that exact image, confirms the stopped guest and reference profiles,
then performs bounded virtual-memory reads of the published driver object,
directory, partition and header. It records 23 consistency checks on a complete
successful observation. A changed kernel or unresolved address is inconclusive.

Read `smem_probe` in the resulting `diagnostic.json`:

- `production-driver-success-path-observed` means the publication and
  checked live object were observed.
- `success-path-not-observed` means the published pointer was zero.
- `observation-inconclusive` means an input, profile, read or consistency
  check failed.
- `observation-not-performed` means the diagnostic did not reach its
  stopped observation point.

The diagnostic process exit code is a transport/tool result, not a successful
driver or Android boot result. The observer never writes guest memory and keeps
`native_sbl_captured` and `android_boot_passed` false. A prior
observation belongs to its recorded binary and input hashes; it is not a result
for a subsequently rebuilt QEMU.

## Observer regression gate

```sh
python3 tb371fc/scripts/test_observe_smem.py
```

The public `smem-observer-unit` gate tests the observer without private
kernel inputs or guest execution. Its explicitly synthetic fixture substitutes
a test-only hash; the production observer retains the fixed kernel hash above.

The cases cover a published live object, initialized shared memory without
publication, altered kernel or publication instructions, missing/shifted/duplicate
symbols, running or malformed status, absent or nonboolean reference profiles,
read errors, wrong returned addresses, oversized fields, invalid pointers,
inconsistent driver/header fields, valid allocated heaps, crossed allocation
frontiers, and diagnostic argument rejection before launching QEMU. Passing
this unit gate validates the observer's decisions; it is not a native probe
receipt.
