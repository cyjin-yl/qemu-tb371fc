# SM8250 AOP DDR statistics offset reference

This opt-in device exposes one read-only **reference offset word** at
`0x0c3f001c`. An enabled, aligned 32-bit read returns `0x000f00b8`. The DDR
statistics payload at `0x0c3f00b8` remains unknown and unmapped.

The type is `qcom-sm8250-ddr-stats-offset` and the board child is
`/machine/ddr-stats-offset`. Select the reference explicitly with:

```text
-global qcom-sm8250-ddr-stats-offset.enabled-reference=on
```

**By default, this device's four-byte region is disabled and absent from the
active address space.** Its QOM observations and migration state still exist.
This preserves the previous RPM model's boundary contract, which observes
`0x0c3f001c` as an unassigned hole when no DDR reference is selected. The
existing RPM model, tests, generated data and source pins remain unchanged.

The reference asserts no firmware execution, initialization, measured runtime
word, silicon reset value, low-power transition or DDR statistics availability.

## Original-kernel frontier

The preceding qualified-source native diagnostic used
`66d9e20a9541b503ed4f5cac5c79c8a5b4a0809b`. It progressed beyond the prior
RPM offset fault and stopped at `ddr_stats_probe+0x88/0x180`, instruction
`b9400018` (`LDR W24, [X0]`). The reported load address was
`ffffff800b18d01c`, and the corresponding physical offset resource is
`0x0c3f001c`. The run ended in `kernel-failure`; normal QMP cleanup and QEMU
exit zero did not establish Android userspace, UI or boot completion.
The [bounded frontier receipt](../evidence/ddr-stats-offset-20261125-6a5/native-frontier-before-ddr-stats-offset.json)
links the immutable preceding native evidence.

The two adjacent rejected-read diagnostics, `0x0c3f001c` and `0x1c`, are
explained by the exact QEMU source's inner physical-address and outer
subpage-relative validity checks for the recorded DDR load. They do not
establish two guest instructions or a separate physical read at `0x1c`.
The independent native review retains an earlier, different Data Abort;
this is not a claim that the entire run contained only one abort.

The original DT and full 384-byte probe were independently checked:

| Field | Actual input |
| --- | --- |
| Node | `/soc/qcom,ddr-stats@c3f0000` |
| Compatible | `qcom,ddr-stats` |
| `phys_addr_base` | `0x0c300000`, size `0x1000` |
| `offset_addr` | `0x0c3f001c`, size `4` |
| Parent address/size cells | `1` / `1` |
| Probe | `ffffff80086b23a8`, 384 bytes |
| Probe SHA256 | `3580ed24a60df64d576acd46f629efe21d43a828fa56e6c4e96b202f49e44423` |

The actual instruction sequence maps four bytes, reads one unsigned 32-bit
offset, unmaps it, rejects a zero value with `-ENODEV`, and otherwise adds
the named resource base. It then creates the read-only sysfs attribute
whose callback is the actual `ddr_stats_show`. It does not dereference the
payload during cold probe. An unmapped bus access faults at the load before
the zero-value guard can run.

The [bounded DT/probe evidence](../evidence/ddr-stats-offset-20261125-6a5/actual-dt-and-ddrstats-probe.json)
records the original input identities and the relevant semantic comparison.
It contains no raw kernel slice. This is a bounded match to the public
consumer, not a claim that the complete production kernel was built from
the public vendor revision.

## Public reference derivation

Exactly four complete public files determine the reference word:

| Source | Immutable revision | Relevant fact |
| --- | --- | --- |
| [Vendor `ddr_stats.c`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/soc/qcom/ddr_stats.c) | `98719830b0de5584f5588bbe9f4c8304d97a64c6` | One four-byte offset read, zero guard, then base addition |
| [Vendor `kona-pm.dtsi`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/arch/arm64/boot/dts/vendor/qcom/kona-pm.dtsi) | Same vendor revision | Base `0x0c300000` and offset resource `0x0c3f001c/4` |
| [Upstream `qcom_stats.c`](https://github.com/torvalds/linux/blob/75f2c0b3690702c90863c2e138cb5520670845ea/drivers/soc/qcom/qcom_stats.c) | `75f2c0b3690702c90863c2e138cb5520670845ea` | Matching `rpmh_data.ddr_stats_offset = 0xb8` |
| [Upstream `sm8250.dtsi`](https://github.com/torvalds/linux/blob/75f2c0b3690702c90863c2e138cb5520670845ea/arch/arm64/boot/dts/qcom/sm8250.dtsi) | Same upstream revision | `qcom,rpmh-stats` region at `0x0c3f0000` |

The generator verifies each entire file's repository, immutable revision,
length, Git blob identity and SHA256 before parsing. It binds the upstream
compatible to its actual configuration, checks literal cell geometry and
resource names, and rejects a different profile or consumer access pattern.

```text
Reference payload address = 0x0c3f0000 + 0xb8 = 0x0c3f00b8
Vendor relative offset    = 0x0c3f00b8 - 0x0c300000 = 0x000f00b8
Little-endian word bytes  = b8 00 0f 00
```

Both consumers describe an eight-byte DDR header and 16-byte records, with
a maximum of 20 entries. The resulting **maximum ABI span** is 328 bytes;
it is not an observed entry count or proof of an initialized 328-byte
payload. The source magic is `0xa1157a75`. No magic, count, names, durations
or records are implemented here. The upstream `0x400` resource and maximum
span are consistency checks and do not enlarge the four-byte model.

The upstream debugfs driver reads the payload magic during probe. The actual
vendor-style sysfs consumer reads its offset during probe and accesses the
payload only through the later sysfs callback. Those access patterns remain
distinct. This model implements the evidenced original offset-word access.

### Private static producer corroboration

The [bounded original-AOP analysis](../evidence/ddr-stats-offset-20261125-6a5/original-aop-static-layout.json)
independently identifies a store to the selected channel's `+0x1c` word.
Using its initialized channel table, the static writer computes
`(0x013f0000 + 0xfed000b8) & 0xffffffff = 0x000f00b8`, agreeing with the
public derivation. The receipt contains image and range hashes and the
arithmetic, not firmware bytes or a full disassembly.

This evidence neither establishes a generic AOP-to-APSS address alias nor
observes the producer at runtime. It does not certify a pristine factory
image, a silicon reset value, DDR payload validity or firmware execution in
QEMU. Private firmware is not required by the generator or public CI.

## MMIO and profile behavior

| Condition | Result |
| --- | --- |
| Default/off profile | No active DDR region; reads and writes reach an unassigned hole |
| Enabled, aligned 32-bit read at `0x0c3f001c` | `MEMTX_OK`, value `0x000f00b8` |
| Enabled write of any delivered width | `MEMTX_ERROR`; no state change |
| Enabled delivered 8-, 16- or 64-bit read | `MEMTX_ERROR` |
| Enabled delivered unaligned 32-bit read | `MEMTX_ERROR` |
| Other pointer words or neighboring bytes | Outside this DDR device |
| DDR payload at `0x0c3f00b8` | Unmapped and unsupported |
| Enabled reset | Fixed reference value retained; active region stays four bytes |
| Disabled reset | Internal unknown placeholder stays zero; no active region appears |

The region is enabled or disabled once in realize, from the configured
`enabled-reference` property, before board mapping. Reset and post-load do
not alter that mapping. The destination's configured profile is authoritative
for migration; a stream cannot turn a disabled destination on.

The enabled callbacks accept delivered sizes through eight bytes and reject
unsupported shapes explicitly. A delivered eight-byte transaction is not
silently implemented as successful word reads. A raw buffer crossing the
decoder boundary can complete the valid four-byte prefix before its
unassigned suffix fails. The default/off profile uses ordinary unassigned
memory dispatch, whose access splitting and diagnostics are checked separately.
A transport reply or an error-result numeric placeholder is not proof of a
successful MMIO access or a measured zero word.

APSS writes are rejected at this consumer boundary. This does not assert that
the real producer's shared SRAM is immutable. The model has no IRQ, clock,
timer, firmware READY state, QMP action, mailbox behavior, DDR transition,
counter or pending operation.

## Observation and migration

There are 17 pure observations: eleven integer getters, `offset-known`, four
false scope getters, and the `enabled-reference` configuration property.
The false getters are `stats-payload-modeled`, `rpm-offset-modeled`,
`firmware-modeled` and `ready-modeled`. The integer getters expose the profile,
identity, physical base, configured window, offset, VMState byte count,
register count and zero IRQ/clock/timer counts. The configured window is four
bytes even when the active off-profile mapping is absent. `offset-known` is
true only for the enabled reference.

VMState version 1 contains five fields totaling 24 bytes:

| Byte offset | Width | Field |
| ---: | ---: | --- |
| 0 | 4 | Profile `0` or `1`, required to match the destination |
| 4 | 4 | Distinct `DSO1` identity, `0x44534f31` |
| 8 | 8 | Physical base `0x000000000c3f001c` |
| 16 | 4 | Configured window size `4` |
| 20 | 4 | Enabled reference `0x000f00b8`, or disabled unknown placeholder `0` |

Fields use QEMU's big-endian migration encoding. Post-load rejects profile,
identity, base, window and fixed-value mismatches. It performs no state repair
or region enabling. The compiled tests require two completed same-profile
migrations, two profile mismatch rejections, and six one-bit corruption
rejections. Their incoming prewarm changes RAM only; both 17-getter destination
snapshots precede device MMIO and guest continuation. Runtime receipts are
required before any compiled-test success is claimed.

## Reproduction and verification

The generated include and [register manifest](ddr-stats-offset-sm8250-registers.json)
are reproduced from the four pinned files beneath `primary-r11/` and
`primary-upstream/`:

```bash
python3 tb371fc/scripts/gen_ddr_stats_offset.py \
  --source-root /path/to/sources --output-root /path/to/qemu

python3 tb371fc/scripts/verify_ddr_stats_offset_generation.py \
  --source-root /path/to/sources --out /path/to/new-verification-directory
```

Omit `--source-root` from the verifier to fetch the exact pinned public inputs.
The existing shared `source_download.read_source` helper remains unchanged.
Its bounded transport retry policy does not retry complete content with the
wrong identity, and no alternate cache, revision or source is accepted.

The [local source validation](../evidence/ddr-stats-offset-20261125-6a5/source-validation.json)
records positive four-input/two-output reproduction, four independent one-bit
whole-source rejections, 32 unsupported parser vectors, one complete-response
identity failure without retry, and two one-bit committed-output rejections.
These checks execute no QEMU, firmware or device operation. The generation
gate is `ddr-stats-offset-source-generation`, result schema
`tb371fc-ddr-stats-offset-generation-v1`.

Compiled CI, actual artifact audits and any subsequent original-input native
diagnostic require separate evidence. No Android boot or display result is
implied by source reproduction or this reference word.
