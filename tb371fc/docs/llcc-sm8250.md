# SM8250 LLCC v2 control reference

This device supplies the source-defined LLCC slice-control transactions needed
after the original kernel reaches `qcom_llcc_probe`. It is disabled by default.
The reference models configuration, explicit programming credit, activation
commands, finite virtual-time transitions and migration of that control state.
It does not implement cache lines, coherency, allocation traffic, performance
counters, physical ECC faults or a memory controller.

The source tables are generated from eight complete, immutable inputs. At this
document's source-validation checkpoint, both generated files reproduce
byte-for-byte and all eight one-bit source modifications are rejected. That
checkpoint does not establish a compiled-device pass or a native LLCC probe
pass. Those require their own exact-build CI and original-input run receipts.

## Original evidence and source identity

The original `ed43749` native run passed both DSI registrations, continued
through DP probe, networking and ramdisk handling, and then stopped at
`qcom_llcc_probe+0x13c`. Its first fatal MMIO was a 32-bit read at
`0x0923000c`, at guest time `3.277153`. This is
`llcc_base + LLCC_COMMON_STATUS0`, not a cache-data read.

The original DT controller node is `/soc/cache-controller@9200000`:

| Property | Original value |
| --- | --- |
| Compatible | `qcom,llcc-v2` |
| `llcc_base` | `0x09200000`, size `0x001d0000` |
| `llcc_broadcast_base` | `0x09600000`, size `0x00050000` |
| Capacity/power flag | `cap-based-alloc-and-pwr-collapse` present |
| Bank-count property | Absent |
| Clocks, IRQ and controller children | Absent |

The same node appears in the fixed vendor
[`kona.dtsi`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/arch/arm64/boot/dts/vendor/qcom/kona.dtsi).
The source driver reads bank count from `COMMON_STATUS0[31:28]`, then uses
`bank * 0x80000` offsets. The tablet's actual register value was not obtained:
the fatal read happened before a value was returned.

Four banks are therefore an explicitly selected **source-bound reference
geometry**, supported independently by
[`sm8250.dtsi` at the immutable Linux v6.12 commit](https://github.com/torvalds/linux/blob/adc218676eef25575469234709c2d87185ca223a/arch/arm64/boot/dts/qcom/sm8250.dtsi).
That source names `llcc0_base` through `llcc3_base` at
`0x09200000`, `0x09280000`, `0x09300000` and `0x09380000`,
each with a `0x50000` resource, followed by the broadcast resource at
`0x09600000`. It supports the geometry; it is not a physical status-register
measurement and does not replace the original DT.

The primary programming source is
[`OnePlusOSS/android_kernel_oneplus_sm8250@98719830b0de5584f5588bbe9f4c8304d97a64c6`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/tree/98719830b0de5584f5588bbe9f4c8304d97a64c6):

| Input | Purpose |
| --- | --- |
| `drivers/soc/qcom/llcc-slice.c` | Probe, bank geometry, slice programming and activation/poll protocol |
| `drivers/soc/qcom/llcc-kona.c` | The sixteen Kona SCT rows and legacy v2 compatible |
| `include/linux/soc/qcom/llcc-qcom.h` | Usecase IDs and SCT field declarations |
| `drivers/soc/qcom/llcc_perfmon.c` | Instantiated perfmon child and its exact probe accesses |
| `drivers/soc/qcom/llcc_perfmon.h` | Common identity offsets and bank/MC bit fields |
| `drivers/edac/qcom_edac.c` | No-IRQ polling path and its two per-bank status words |
| `arch/arm64/boot/dts/vendor/qcom/kona.dtsi` | Legacy resources, compatible and capacity/power property |
| Upstream `arch/arm64/boot/dts/qcom/sm8250.dtsi` | Explicit four-bank SM8250 geometry |

Every input has a complete-byte SHA-256, Git blob identity and size in
[`llcc-sm8250-registers.json`](llcc-sm8250-registers.json). The vendor tree's
`Documentation/devicetree/bindings` is a symlink to
`../../arch/arm64/boot/dts/vendor/bindings`; that target is absent in the
fixed repository. This work does not claim to have validated a vendor binding
document that the source snapshot does not contain.

The original kernel also provides a stronger, narrowly scoped correspondence:
its Kona probe points to sixteen 40-byte SCT rows at
`ffffff800a2abd98`. All **640 bytes, including padding**, equal the fixed
vendor table and header IDs when encoded as `<IIIIB3xIIII4B`. The bytes hash to
`1ae851c29537ec405b15db1bd151710c25ad0f063a74970253f9d16754dc5d55`.
This proves the observed table correspondence, not that the entire original
firmware was built from this public commit.

Original DT, instruction and table evidence is kept under
[`evidence/llcc-20261124-6a5/`](../evidence/llcc-20261124-6a5/).
The original full DT and Image remain unchanged.

## Source programming contract

The source programs each row in its original table order. It writes ATTR1
before ATTR0, then cumulatively writes WRSC, capacity-disable and retention
masks. The v2 compatible enables the WRSC path; the original DT boolean enables
the latter two paths. Even zero values are explicit source writes.

ATTR0 combines reserved ways in bits 11:0 and bonus ways in bits 27:16.
ATTR1 combines cache mode, probe-target, fixed-size and priority with the maximum
capacity expressed in cache lines **per bank**:

```text
per_bank_cachelines = max_capacity_KiB * 1024 / 4 / 64
ATTR1 = cache_mode | probe_target << 2 | fixed_size << 3
        | priority << 4 | per_bank_cachelines << 16
```

The table's normal slices use `ATTR0 = 0x0fff0000`; APTCM uses
`0x00000003`. The source initializes slices **1, 13 and 31** through ordinary
activation transactions. Reset does not preactivate them. Usecase IDs and
hardware slice IDs are distinct: the final `LLCC_CVPFW` row has usecase ID
**32** and hardware SID **17**, and remains last in the generated source order.

| Source row | SID | Capacity, KiB | ATTR1 |
| --- | ---: | ---: | --- |
| CPUSS | 1 | 3072 | `0x30000018` |
| VIDSC0 | 2 | 512 | `0x08000038` |
| AUDIO | 6 | 1024 | `0x10000010` |
| CMPT | 10 | 1024 | `0x10000010` |
| GPUHTW | 11 | 1024 | `0x10000018` |
| GPU | 12 | 1024 | `0x10000010` |
| MMUHWT | 13 | 1024 | `0x10000018` |
| CMPTDMA | 15 | 1024 | `0x10000010` |
| DISP | 16 | 3072 | `0x30000018` |
| AUDHW | 22 | 1024 | `0x10000018` |
| NPU | 23 | 3072 | `0x30000018` |
| WLNHW | 24 | 1024 | `0x10000010` |
| CVP | 28 | 256 | `0x04000038` |
| APTCM | 30 | 128 | `0x02000031` |
| WRTCH | 31 | 256 | `0x04000018` |
| CVPFW | 17 | 512 | `0x08000010` |

Activation writes CTRL `3` then `2`; deactivation writes `5` then `4`.
The driver polls until the opposite state bit clears, with a **100 us source
polling budget**. The reference uses a finite **1000 ns virtual-time policy**;
that delay is not a measured silicon latency. Polling reads do not advance
time or complete a transaction by themselves.

Activation requires explicit programming credit for both per-slice attributes
and all three global configuration words, matching this slice's source recipe.
A command captures its attributes and slice-specific global flags. A matching
trigger-clear edge starts the deadline; missing edges, invalid recipes or
changed pending configuration cannot manufacture completion. Deactivation is
available even when the activation recipe is not valid.

Pending transactions retain the previous stable active/inactive state. A
configuration change cancels an in-flight transition while retaining the
independent stable state. Consequently, `active` can remain true while the
current recipe is invalid; neither state means that cache data or coherency
has been simulated.

## Exact MMIO ownership

The generated contract contains **71 canonical register descriptors**:
four words for each of sixteen slices, three global configuration words,
two common identity words and two EDAC status words.

| View | Exposed words | Leaves | Behavior |
| --- | ---: | ---: | --- |
| Each of four banks | 70 | 28 | Read-only view; excludes HW_INFO |
| Broadcast | 69 | 27 | Canonical configuration/control writes; excludes the two bank ECC status words |
| Total | **349** | **139** | **1396 mapped bytes** |

The original two resource spans remain transparent containers. Only the
generated contiguous word ranges are leaves. Gaps, unknown SIDs and unrelated
addresses retain the surrounding unassigned behavior. Bank views share the
canonical broadcast control state; separate per-bank programming is not
implemented.

Accepted callbacks are aligned 32-bit accesses. Narrow, wide or unaligned
callbacks are rejected, as are bank writes and writes to status/identity
words. This describes callback validation, not a blanket transaction-wide
rollback guarantee for accesses that the QEMU address-space layer may split.

The CTRL storage mask is `0x7`, with only `0, 2, 3, 4, 5` accepted.
ATTR0 accepts `0x0fff0fff`; ATTR1 accepts `0xffff003d`; global masks cover
only the sixteen supported SIDs. These are the supported reference domains,
not claims that every other bit is reserved on physical hardware.

## Child drivers and intentionally unknown behavior

`qcom_llcc_probe` instantiates both `qcom_llcc_edac` and
`qcom_llcc_perfmon` after SCT programming; they do not need DT child nodes.

The original controller has no IRQ. In the pinned EDAC driver a negative
`ecc_irq` selects polling and skips the IRQ-enable/configuration routine.
The default 5000 ms poll reads DRP status at bank offset `0x41000` and TRP
status at `0x20480`. Without an asserted error it does not read syndrome
arrays or clear error counters. The two modeled status words therefore report
no modeled error events. Cache/ECC data, syndrome generation, IRQ routing and
error injection remain unimplemented.

Perfmon probe reads only broadcast `COMMON_STATUS0` at `0x3000c` and
`COMMON_HW_INFO` at `0x30000`. It creates sysfs attributes and event-port
operations and initializes a timer with `expires = 0`; it does not start
counting. The model exposes four reference banks in bits 31:28, while the
memory-controller count field in bits 11:10 remains zero and explicitly
unknown. The guest's zero-to-one software fallback does not prove a physical
memory-controller count.

HW_INFO likewise remains zero with an explicit unknown flag. The guest can
classify that as its REV_0 fallback; the model does not claim that this is the
tablet's hardware revision. It does not invent the source constants for REV_1
or REV_2 merely because the DT compatible contains `v2`.

Runtime perfmon counter configuration, filtering, timed dumps and occupancy
are outside this profile. In particular, the optional
`perfmon_scid_status` sysfs reader traverses all 32 SIDs, whereas this
reference supports the sixteen source table SIDs. Status exposes only the
stable active/deactive bits; current-capacity bits 29:16 are not modeled.
The separate DT `qcom,llcc-pmu-ver2` resource at `0x09095000/0x300`,
bandwidth monitors and unrelated cache-data addresses are not owned by this
controller model.

## Generation and validation

The generator and verifier are:

- [`generate_llcc_sm8250.py`](../scripts/generate_llcc_sm8250.py)
- [`verify_llcc_generation.py`](../scripts/verify_llcc_generation.py)

The verifier fetches each immutable repository/revision/path separately, or
accepts a local source root with `primary-r11/` and `primary-upstream/`.
It checks every whole input's size, SHA-256 and Git blob identity, regenerates
both outputs and compares their bytes with the committed copies. Its report
schema is `tb371fc-llcc-generation-v1`, with exactly **8 inputs and 2 output
checks**. Geometry and descriptor counts are separate contract fields.

```sh
python3 -B tb371fc/scripts/verify_llcc_generation.py --out /tmp/llcc-generation

# The equivalent offline check, using a prepared exact-source directory:
python3 -B tb371fc/scripts/verify_llcc_generation.py \
  --source-root /path/to/llcc-review-6a5 --out /tmp/llcc-generation-local
```

The output path must be new. Complete source pins are verified before parsing
any table; a one-bit change to any of the eight inputs is rejected. The
[source-validation receipt](../evidence/llcc-20261124-6a5/source-validation.json)
records deterministic generation, the source/production table comparison and
tamper results. It explicitly separates those results from compiled-device
acceptance and a future original-kernel run.

The control model declares VMState v1 with a **783-byte** payload. Review and
compiled tests must verify all identity, storage, programming-credit,
transition, capture, deadline and stable-state fields, including pending
transitions before the target's first MMIO or continuation. A valid payload
does not establish hardware cache contents, Android userspace or an Android
interface.
