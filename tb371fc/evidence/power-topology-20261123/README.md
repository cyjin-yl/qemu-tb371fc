# Native power and GPIO topology evidence — 2026-11-23

These files contain read-only native PMIC identity exports, SPMI bindings,
GPIO chip counts, and selected hardware DT properties. No firmware payload,
raw kernel log, input event, account data, unique ID, boot argument or running
MMIO/register dump is included.

| File | Source and interpretation |
| --- | --- |
| `android-pmic-identity.json` | Native sysfs identities and driver/of-node metadata, 06:57:11 UTC |
| `android-spmi-gpio-bindings.json` | SPMI driver links and GPIO label/base/count metadata, 06:58:55 UTC; no pin values/events |
| `android-pmic-boot-identity.json` | Filtered dmesg identity extraction at 07:00:03 UTC; exit 0, no matching identity line |
| `power-device-tree.json` | Allowlisted SPMI, TLMM and PDC topology from the private live DTB, including allowed bytes, BE cells and hashes |
| `ufs-reset-device-tree.json` | Exact UFS reset pinctrl references, dedicated-pad states and separate GCC reset specifier |
| `tlmm-map-source.json` | Register map derived from pinned Linux v6.12.114, distinct from native measurements |
| `collection-provenance.json` | Collector/source hashes and publication interpretation |
| `SHA256SUMS` | Hashes of immutable topology artifacts and collector sources |

Private live DTB SHA256:
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
The original DTB is not included.

## Observations and remaining unknowns

Native socinfo reports model 65566 (0x1001e) and die revision 196608 (0x30000).
The upstream [socinfo decoder](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/soc/qcom/socinfo.c?h=v6.12.114)
maps model low halfword 0x1e to PM8150 and revision halfwords to 3.0.
This is **firmware-reported PM8150 revision 3.0**, not a fresh PMIC register
read. Other PMIC revisions and the arbiter VERSION register remain unknown.

Native driver `pmic-spmi` binds DT-created SIDs 0,1,2,3,4,5,8,9,a,b.
Binding does not independently prove every DT-named chip identity or all
peripheral capabilities.

| GPIO controller | Exported ordinary pins | Physical pin interpretation |
| --- | ---: | --- |
| TLMM | 180 | GPIO 0–179; UFS reset is a separate output-only pad |
| PM8150 | 6 | Allowed physical pins 1,3,6,7,9,10 |
| PM8150B | 9 | Allowed pins 1,2,5,6,7,8,9,10,12 |
| PM8150L | 9 | Allowed pins 1,3,4,5,6,7,8,10,11 |
| LPASS | 14 | Count only, without an audio emulation claim |

The sparse sets come from native `qcom,gpios-disallowed` lists. They must
not become the first six or nine contiguous physical pins. GPIO chip base
numbers are Linux allocation metadata, not physical register addresses.

## Address and interrupt interpretation

| Controller | Native CPU physical region(s) | Interrupt |
| --- | --- | --- |
| TLMM | 0x0f000000 + 0x01000000 | GIC SPI 208, level high |
| SPMI core | 0x0c440000 + 0x1100 | PDC input 1, level high |
| SPMI channels | 0x0c600000 + 0x02000000 | Per-APID channels; native ownership table unread |
| SPMI observer | 0x0e600000 + 0x100000 | Read channels |
| SPMI interrupt | 0x0e700000 + 0x0a0000 | Arbiter interrupt region |
| SPMI configuration | 0x0c40a000 + 0x26000 | Configuration region |
| PDC | 0x0b220000 + 0x30000; 0x17c000f0 + 0x60 | Native ranges map input 1 to GIC SPI 481 |

SPMI child `reg` cells encode SID/address-type or PMIC-local peripheral
addresses; they are **not CPU physical MMIO**. The inventory labels them
accordingly. Native PDC ranges are <0 480 94>, <94 609 31>, <125 63 1>,
<126 716 12>. The SPMI parent is the PDC phandle, so its input 1 must not be
treated as GIC SPI 1. PDC version and complete wakeup behavior remain unknown.

SPMI aperture geometry is consistent with the upstream v5 layout. That is
a source-based inference, not a measured version or a complete PMIC model.
Primary sources are
[spmi-pmic-arb.c](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/spmi/spmi-pmic-arb.c?h=v6.12.114),
[qcom-spmi-pmic.c](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/mfd/qcom-spmi-pmic.c?h=v6.12.114),
[pinctrl-spmi-gpio.c](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/pinctrl/qcom/pinctrl-spmi-gpio.c?h=v6.12.114)
and [qcom-pdc.c](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/irqchip/qcom-pdc.c?h=v6.12.114).
Exact hashes are in the provenance file.

Native UFS pinctrl selects `ufs_reset` low to assert and high to deassert,
both with pull-down and 8 mA. It has separate GCC core-reset metadata.
Pin declarations alone do not establish pulse timing or device reset behavior.

## Collection and publication

Collectors only read named identity attributes, driver/of-node links,
attribute names, GPIO labels/counts and allowlisted DT properties. The dmesg
collector extracts only PMIC/arbiter identity fields and discards every other
line. No matching line was found; this is not evidence of chip absence.
No mount, security configuration, GPIO, clock or regulator state was changed.

The bindings collector originally ran as an inline Python script. Its exact
retained text is saved as `collect_spmi_gpio_bindings_20261123.py` and
hashed separately, without changing the capture. Published JSON formatting
preserves captured values without claiming byte identity with Android sysfs.

The DT collector uses explicit property allowlists and records omitted
property names. Original private assets remain private. Hardware reads and
binding observations establish topology and evidence; they do not establish
behavior equivalence.
