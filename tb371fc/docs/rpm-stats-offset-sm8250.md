# SM8250 AOP RPM statistics offset reference

This device implements one opt-in, read-only **reference pointer word** at
`0x0c3f0004`. A legal aligned 32-bit read returns `0x000f0048`.
The statistics records to which that word points remain unknown and unmapped.

The device type is `qcom-sm8250-rpm-stats-offset`; the board child is
`/machine/rpm-stats-offset`. Its `enabled-reference` property defaults to false.
Select the public reference layout explicitly with:

```text
-global qcom-sm8250-rpm-stats-offset.enabled-reference=on
```

This reference does not assert that AOP firmware executed, completed
initialization, entered a low-power mode, or supplied a measured runtime value.
It does not establish a silicon reset value.

## Why this word is needed

The preceding original-input diagnostic used source
`54ec7443bc75460baa35a0c0b0502ae6cb4d8fa7` and the exact qualified QEMU artifact.
It advanced past the earlier SPSS mask write and stopped at an unmapped
four-byte read from `0x0c3f0004`. The original kernel reported
`msm_rpmstats_probe+0x84/0x1c8`, with instruction `b9400017`
(`LDR W23, [X0]`). The kernel, ramdisk, full DT and bootargs retained their
recorded identities. The run attached zero original LUNs and ended in
`kernel-failure`; Android userspace, display and boot completion were not
observed. See the [bounded frontier evidence](../evidence/rpm-stats-offset-20261125-6a5/native-frontier-before-rpm-stats-offset.json)
and its pinned earlier independent review.

The original DT declares:

| Field | Value |
| --- | --- |
| Node | `/soc/qcom,rpm-stats@c3f0004` |
| Compatible | `qcom,rpm-stats` |
| Resource `phys_addr_base` | `0x0c300000`, size `0x1000` |
| Resource `offset_addr` | `0x0c3f0004`, size `4` |
| `qcom,num-records` | `3` |

The exact 456-byte original probe maps the offset resource for four bytes,
loads the unsigned 32-bit word, unmaps it, adds the declared resource base,
and stores the resulting pointer in platform data. It does not dereference
the statistics records during this probe.

The pinned OnePlus consumer has the same lookup/read/add contract. Its later
debugfs and global-data extensions differ from the original kernel's
kobject/sysfs path. This implementation does not claim that the complete
production driver is identical to that public source.

## Deriving the reference layout

Four complete public sources determine the exposed word:

| Source | Pinned revision | Relevant fact |
| --- | --- | --- |
| [Vendor `rpm_stats.c`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/soc/qcom/rpm_stats.c) | `98719830b0de5584f5588bbe9f4c8304d97a64c6` | One `readl_relaxed`, then addition to the named base |
| [Vendor `kona-pm.dtsi`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/arch/arm64/boot/dts/vendor/qcom/kona-pm.dtsi) | Same vendor revision | Base `0x0c300000`, offset word `0x0c3f0004/4`, three records |
| [Upstream `qcom_stats.c`](https://github.com/torvalds/linux/blob/adc218676eef25575469234709c2d87185ca223a/drivers/soc/qcom/qcom_stats.c) | `adc218676eef25575469234709c2d87185ca223a` | Matching `rpmh_data`: fixed offset `0x48`, three 32-byte records |
| [Upstream `sm8250.dtsi`](https://github.com/torvalds/linux/blob/adc218676eef25575469234709c2d87185ca223a/arch/arm64/boot/dts/qcom/sm8250.dtsi) | Same upstream revision | `qcom,rpmh-stats` region at `0x0c3f0000` |

The generator binds the upstream compatible to its actual `rpmh_data`
configuration. It rejects the older RPM dynamic-offset profile and the
two-record SDM845 profile. It verifies the parent address/size cells, the
vendor resource names and four-byte aperture, the single consumer read,
the fixed layout use, and the statistics structure's field widths.

The resulting arithmetic is:

```text
Reference payload address = 0x0c3f0000 + 0x48 = 0x0c3f0048
Vendor relative offset    = 0x0c3f0048 - 0x0c300000 = 0x000f0048
Little-endian word bytes  = 48 00 0f 00
```

The upstream region size `0x400` and reference record span of `0x60` bytes
are provenance and consistency checks. They do not enlarge this device's
four-byte MMIO window.

### Original AOP corroboration

A separate static review of the backed-up active AOP partition independently
found producer arithmetic yielding `0x000f0048`. Its indexed channel table,
writer, getter and three-record initializer agree with the public reference
layout. The [bounded corroboration](../evidence/rpm-stats-offset-20261125-6a5/original-aop-static-layout.json)
records the original image and code-range hashes, address arithmetic and
limits. The firmware image and broad private inventory are not published or
required by public CI.

This is static producer evidence. It is not a live read from the tablet,
a pristine-factory-image certification, full reset-to-runtime reachability
proof, or evidence that the producer ran in QEMU. No generic alias between
AOP and APSS address spaces is assumed: the APSS word is independently
identified by the actual DT and kernel probe, and the reference payload by
the pinned upstream SM8250 layout.

## MMIO, reset and unknown state

| Access or condition | Result |
| --- | --- |
| Enabled, aligned 32-bit read at `0x0c3f0004` | `MEMTX_OK`, value `0x000f0048` |
| Any read while disabled | `MEMTX_ERROR` |
| Any write | `MEMTX_ERROR`, no state change |
| Delivered 8-, 16- or 64-bit read | `MEMTX_ERROR` |
| Delivered unaligned 32-bit read | `MEMTX_ERROR` |
| Adjacent pointer fields, including `0x0c3f0000`, `0x0c3f0008`, `0x0c3f001c` | Outside this model |
| Statistics payload `0x0c3f0048..0x0c3f00a7` | Outside this model |
| Reset, enabled | Fixed reference offset restored; no telemetry initialized |
| Reset, disabled | Internal value zero, `offset-known=false`; MMIO still rejects |

APSS writes are rejected because this device exposes only the evidenced
consumer view. This is not a claim that the real shared SRAM is immutable
to its firmware producer.

The callbacks accept delivered transaction sizes through eight bytes and
explicitly reject unsupported sizes, so an eight-byte transaction delivered
to the device is not silently implemented as two successful reads. A raw
memory-buffer request crossing a decoder boundary is a separate transport
case: a legal first four-byte subaccess may complete before a following
unmapped byte fails. Neither a QTest transport `OK` nor a zero error-result
placeholder means an unsupported MMIO access succeeded.

Repeated valid reads have no side effects. The model does not synthesize
record names, sleep counts, entry/exit timestamps, accumulated durations,
firmware readiness, interrupts, clocks, timers, power transitions, DDR
statistics or master statistics. The existing mailbox SRAM and QMP peer
keep their separate aperture and behavior.

## QOM observation and migration

The eleven integer observations describe the configured profile, reference
identity, base, window size, offset, VMState size, register count and zero
IRQ/clock/timer counts. `offset-known` is true only for the enabled reference.
`stats-payload-modeled`, `ddr-offset-modeled`, `firmware-modeled` and
`ready-modeled` remain false. The configuration property
`enabled-reference` completes the 17-property observation inventory.

VMState version 1 carries 24 bytes, in QEMU's big-endian migration encoding:

| Byte offset | Width | Field |
| ---: | ---: | --- |
| 0 | 4 | Enabled profile, exactly `0` or `1` matching the destination |
| 4 | 4 | Reference identity `0x52534f31` |
| 8 | 8 | Physical base `0x000000000c3f0004` |
| 16 | 4 | Window size `4` |
| 20 | 4 | Offset: `0x000f0048` enabled, unknown placeholder `0` disabled |

Post-load checks reject profile, identity, geometry and fixed-value
mismatches. There are no mutable counters, pending operations, output
signals or deadlines to reconstruct. Same-profile migration preserves
the configured reference before any destination guest MMIO.

## Reproducible source verification

The generated include and [register manifest](rpm-stats-offset-sm8250-registers.json)
come from exactly four inputs and two output files:

```bash
python3 tb371fc/scripts/gen_rpm_stats_offset.py \
  --source-root /path/to/sources \
  --output-root /path/to/qemu

python3 tb371fc/scripts/verify_rpm_stats_offset_generation.py \
  --out /path/to/new-verification-directory
```

For an offline verification, add `--source-root /path/to/sources` to the
verifier. That directory must contain the pinned files beneath
`primary-r11/` and `primary-upstream/`.

Before parsing, every complete file must match its repository, immutable
revision, byte length, Git blob identity and SHA-256. The verifier regenerates
both outputs and compares their complete bytes with the committed files.
The network path uses the existing `source_download.read_source` helper:
only its allowlisted transient transport failures may retry, with at most
three requests and a 30-second aggregate retry-delay allowance. Identity
validation happens outside that loop. A complete response with the wrong
identity fails without another source, cache fallback or retry.

The [local source validation](../evidence/rpm-stats-offset-20261125-6a5/source-validation.json)
records positive reproduction, four independent one-bit whole-source
rejections, 22 parser rejection vectors, a complete-response identity failure
with one injected transport call, and two one-bit committed-output
rejections. These checks execute no QEMU or firmware and make no device or
network calls. The public generation gate is
`rpm-stats-offset-source-generation`, with result schema
`tb371fc-rpm-stats-offset-generation-v1`.

Compiled component CI and any subsequent original-input native diagnostic
have separate receipts. Source reproduction, component success, kernel
execution progress and Android boot completion remain distinct claims.
