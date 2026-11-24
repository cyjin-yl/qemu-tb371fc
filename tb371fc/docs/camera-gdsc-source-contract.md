# Camera GDSC source and DT contract

This contract explains the next bounded increment after the original full-DT
run of QEMU source `09c638d6a6d4fa4a4e4e332a7b39aa7bf7f5418e`.
It separates direct observations, driver declarations, existing QEMU behavior,
and explicit reference choices. The implementation protocol is documented in
[camera-gdsc-reference.md](camera-gdsc-reference.md).

## Triggering observation and its limits

The exact formal CI binary had SHA256
`7211873740226d0509e354cfbf1c23bb4eb0459b6eca470187ea3dfd3a2e04ca`.
The original Image and complete original DT reached a read32 at physical
`0x0ad07004`. The MMIO diagnostic identified an unassigned region; the kernel
reported `gdsc ad07004.qcom,gdsc`, bus client `bps_gdsc_ahb`, and a call chain
through `regmap_mmio_read32le` to `gdsc_probe+0x770/0x9d0`. The exception was in
CPU7's PID60 deferred-probe worker, not a PID1 panic. It still prevents the
strict full-DT user-space fixture from being run under the current acceptance
policy. It does not prove that no user instruction executed elsewhere.

The associated native extra record was 165410 bytes, SHA256
`d53e71a3f0c3712e5f6fdcaf85615474745751bff3d791ea789866ca84ecf4d7`.
Its RPMh WAIT compatibility option was enabled but its reserved-bit mask count
was zero, so this run does not establish a causal benefit from masking. The
SBM device recorded three accepted CLR MMIO requests, last mask 4, and no SET
request. Those CLR requests may be idempotent; they do not prove a drained
camera interconnect or a disconnect/reconnect workload.

## Fixed primary sources

Vendor files are pinned to OnePlusOSS commit
`94304e12a3509eac7b3716bf574ce0b1fcaacbae`. Existing QEMU files are pinned to the
triggering source above. These are the exact bytes inspected for this contract.

| Primary file | Bytes | SHA256 |
| --- | ---: | --- |
| [drivers/clk/qcom/gdsc-regulator.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gdsc-regulator.c) | 24838 | `41acd7aa326c656db5b9dbe57c5fe05f1783abf8a299aaf9f8d4c58b9ecea3d2` |
| [drivers/regulator/rpmh-regulator.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/regulator/rpmh-regulator.c) | 60856 | `e24f271ce7627f1212dc2808ad7c217cc80aac61aa12547b268b4985b19f821c` |
| [drivers/clk/qcom/gcc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gcc-kona.c) | 115577 | `ed208678508c983d0989f1f135fac898c5a9a91c14af1fa42cd7ea2f24cd14cd` |
| [drivers/clk/qcom/clk-branch.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-branch.c) | 10657 | `375830dc9cd0ecbc12ea018eee1d260d8ba724dc52f1ac8b3589a07bc75d08aa` |
| [drivers/clk/qcom/camcc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/camcc-kona.c) | 74311 | `60fa0934e7ec2ad37ade0b710430d137bf1f46008e7115c2d77569f34edd50f9` |
| [include/dt-bindings/clock/qcom,gcc-kona.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/include/dt-bindings/clock/qcom,gcc-kona.h) | 10232 | `ec7b2a775a7f96a543c064c7fc733fb83ffa7e3dae3c4bb517200627ae5f7fed` |
| [hw/misc/qcom-gdsc.c](https://github.com/cyjin-yl/qemu-tb371fc/blob/09c638d6a6d4fa4a4e4e332a7b39aa7bf7f5418e/hw/misc/qcom-gdsc.c) | 12842 | `c6548e579ea683673f883973e527377f3309705bef9a702a2b6943fe7792b911` |
| [hw/misc/qcom-sm8250-gcc-data.inc](https://github.com/cyjin-yl/qemu-tb371fc/blob/09c638d6a6d4fa4a4e4e332a7b39aa7bf7f5418e/hw/misc/qcom-sm8250-gcc-data.inc) | 63115 | `a59a7102bca443094c77b4212ca35b6ab595cec473705b371db6e4ef4f76410c` |
| [hw/misc/qcom-sm8250-rpmh-data.inc](https://github.com/cyjin-yl/qemu-tb371fc/blob/09c638d6a6d4fa4a4e4e332a7b39aa7bf7f5418e/hw/misc/qcom-sm8250-rpmh-data.inc) | 9276 | `6f69acb80c1032e29a581197d9a41f1a9ac67fa9d464f0e7bb55410b26c54542` |
| [hw/arm/lenovo-tb371fc.c](https://github.com/cyjin-yl/qemu-tb371fc/blob/09c638d6a6d4fa4a4e4e332a7b39aa7bf7f5418e/hw/arm/lenovo-tb371fc.c) | 84069 | `c73b5d1e6f1a5b8265490856b870e47d7ced8b2fff2b8578bbbd7f8b0c8f7e22` |

### GDSC register operations

In vendor `drivers/clk/qcom/gdsc-regulator.c`, definitions at lines 27–43 give
GDSCR `POWER_ON` bit31, `CLK_DIS_WAIT` bits15:12, `RETAIN_FF_ENABLE` bit11,
`SW_OVERRIDE` bit2, `HW_CONTROL` bit1, and `SW_COLLAPSE` bit0. The separate
DOMAIN and SW_RESET controls only apply when their syscon resources exist.

The polling function at lines 100–135 reads POWER_ON from the optional hardware
status resource or the GDSCR itself. It polls with a software 1 us delay and a
bounded timeout. The source explicitly does not guarantee a hardware completion
delay. No camera transition latency is inferred from the poll interval.

Probe at lines 807–930 obtains bus access, reads GDSCR at line 847, clears
HW_CONTROL and SW_OVERRIDE, optionally sets CLK_DIS_WAIT, and writes the value
back. Only the skip-logic-collapse branch requests power during probe. All six
camera domains lack that property. Successful registration can therefore occur
with a collapsed domain and does not establish camera execution.

Enable at lines 205–378 may arrange regulator and bus requests, pulse optional
reset/clamp resources, clear SW_COLLAPSE, and poll POWER_ON. Disable at lines
381–458 sets SW_COLLAPSE and ordinarily polls for off. The `retain-regs` policy
can set GDSCR bit11. Clock retention calls are a different register interface.
The source at lines 138–203 notes that other voters can hold a domain on; the
bounded camera reference explicitly assumes those hidden votes are absent.

DT parsing at lines 605–695 distinguishes `parent-supply` from the separately
obtained `vdd_parent-supply`, and looks up `domain-addr`, `sw-reset`, and
`hw-ctrl-addr` only if present. `qcom,support-hw-trigger` permits software
regulator FAST mode. It is a declaration of the reference's supported mode,
not proof that other physical domains lack the hardware control bit.

### Parent regulator and access clock

Vendor `rpmh-regulator.c` lines 1834–1945 select ARC/VRM/XOB by compatible and
read `qcom,resource-name` before resolving a command-db address. The regulator
node's path spelling alone is insufficient to identify its resource; this is
why a second narrowly scoped DT capture was taken.

Vendor binding `GCC_CAMERA_AHB_CLK` is 11. `gcc-kona.c` lines 1711–1725 declare
that clock at CBCR offset `0xb008` with `clk_branch2_ops`; they do not declare a
parent rate. The current QEMU generated descriptor with that exact name has
array index 10 and external source index 2, which remains unknown. The next
entry, array index 11, is a different camera HF AXI clock.

`clk-branch.c` lines 132–168 implement PERIPH retention at CBCR bit13 and MEM
retention at bit14. GDSC probe/enable/disable call these clock operations.
The current QEMU GCC source at the triggering revision accepts only bit0 at
`0xb008`; a source-backed correction for retention is a separate GCC change,
not a reason to extend GDSCR or invent a clock frequency. A cold probe's clear
of already-clear bits may be skipped by regmap; this contract does not predict
that the next native run necessarily reaches a new retention fault.

## Actual DT captures

The input is the fixed original complete DT, 973052 bytes, SHA256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
The captures were executed once each by the root coordinator, with read-only
opens, nofollow on each input path component, the full input SHA256, and stable
file metadata before and after the read. Neither collector accessed hardware,
started QEMU, changed the DT, or published a complete DT dump.

| Evidence | Bytes | SHA256 |
| --- | ---: | --- |
| [Bounded GDSC nodes and direct relationships](../evidence/wholeboard-20261123-a47/gdsc-dt-evidence-09c638.json) | 513559 | `6dbe11407254a358648375ec13bb07baf5c7cee06f6709e835d9e2908bdbf0c1` |
| [Camera parent regulator and bounded supply chain](../evidence/wholeboard-20261123-a47/camera-parent-dt-evidence-09c638.json) | 19367 | `8a8bdded1c180c56a9029a3ef954c1817554b70e8cda52696bebb9075fb56ac2` |

The first capture contains 24 GDSC nodes, 58 direct consumers, 38 direct
providers, and 426 relationships, with no unresolved selected relationship.
It preserves raw hex and big-endian cells. The collectors deliberately do not
perform implicit physical address translation. An independent interpretation
of the captured `/soc` ranges uses one child address cell, two root address
cells, one size cell, and cells `[0,0,0,0xffffffff]`: these resources map from
bus address zero to physical address zero within that range. Each selected
four-byte resource lies wholly inside it. Thus the BPS address agrees with the
independently observed physical MMIO fault, without relying only on the node
name.

The six camera nodes all have `compatible = qcom,gdsc`, four-byte `reg`,
`qcom,retain-regs`, both supply properties pointing at phandle `0x68` (104),
and `clocks = <0x16 0xb>` named `ahb_clk`. They declare one bus path from master
1 to slave 589, with idle bandwidth zero and active average zero/instantaneous
one in the DT's KBps units. These declarations identify software requests; they
do not prove access-clock availability or a real bus bandwidth.

Only BPS and IPE declare HW-trigger support. Only TITAN explicitly sets the
500 us polling timeout; the other five use the driver's 100 us default.
All six lack `domain-addr`, `sw-reset`, `hw-ctrl-addr`, `skip-logic-collapse`,
`enable-root-clk`, `force-enable-root-clk`, `reset-aon-logic`, and a CLK_DIS_WAIT
override. No auxiliary reset, clamp, hardware-status, or broad CAMCC mapping is
justified by these six nodes.

The parent supplement contains nine nodes and seven supply references. Phandle
104 resolves to `regulator-pm8150a-s4-level`, whose aggregate parent is
`qcom,rpmh-arc-regulator` with actual `qcom,resource-name = mmcx.lvl`. The leaf
has set mask 3, minimum level 64, and maximum 65535. Its MX/CX regulator supply
chain is preserved in the supplement. These values do not measure PMIC volts
or justify treating 64 as a new hardware POWER_ON threshold.

The existing QEMU RPMh reference names resource index3 `mmcx.lvl`. Its digital
output is present-resource AND an effective nonzero ARC vote; its addresses
and level mappings are explicitly synthetic. Connecting that one output to
one aggregate camera input is therefore a bounded reference composition, not
an assertion that the complete physical rail hierarchy has been simulated.

## Ownership and minimal scope

The following comparison uses the actual 24-domain DT set and the triggering
QEMU GCC power table and board mappings. Ten DT domains already have a GCC
owner, two have GPU owners, and exactly six camera resources form the new
same-protocol group. The remaining six domains are outside this increment.

| Physical address | DT regulator name | Ownership for this increment |
| --- | --- | --- |
| `0x00106004` | `pcie_2_gdsc` | existing GCC power table: pcie_2_gdsc |
| `0x0010f004` | `usb30_prim_gdsc` | existing GCC power table: usb30_prim_gdsc |
| `0x00110004` | `usb30_sec_gdsc` | existing GCC power table: usb30_sec_gdsc |
| `0x0016b004` | `pcie_0_gdsc` | existing GCC power table: pcie_0_gdsc |
| `0x00177004` | `ufs_phy_gdsc` | existing GCC power table: ufs_phy_gdsc |
| `0x0017d050` | `hlos1_vote_mmnoc_mmu_tbu_hf0_gdsc` | existing GCC power table: hlos1_vote_mmnoc_mmu_tbu_hf0_gdsc |
| `0x0017d054` | `hlos1_vote_mmnoc_mmu_tbu_sf0_gdsc` | existing GCC power table: hlos1_vote_mmnoc_mmu_tbu_sf0_gdsc |
| `0x0017d058` | `hlos1_vote_mmnoc_mmu_tbu_hf1_gdsc` | existing GCC power table: hlos1_vote_mmnoc_mmu_tbu_hf1_gdsc |
| `0x0017d06c` | `hlos1_vote_mmnoc_mmu_tbu_sf1_gdsc` | existing GCC power table: hlos1_vote_mmnoc_mmu_tbu_sf1_gdsc |
| `0x0018d004` | `pcie_1_gdsc` | existing GCC power table: pcie_1_gdsc |
| `0x03d9100c` | `gpu_gx_gdsc` | existing qcom-gdsc GX |
| `0x03d9106c` | `gpu_cx_gdsc` | existing qcom-gpu-cx-gdsc |
| `0x09981004` | `npu_core_gdsc` | outside this increment; no newly assigned owner |
| `0x0abf0bf8` | `mvs0c_gdsc` | outside this increment; no newly assigned owner |
| `0x0abf0c98` | `mvs1c_gdsc` | outside this increment; no newly assigned owner |
| `0x0abf0d18` | `mvs0_gdsc` | outside this increment; no newly assigned owner |
| `0x0abf0d98` | `mvs1_gdsc` | outside this increment; no newly assigned owner |
| `0x0ad07004` | `bps_gdsc` | new six-domain camera reference |
| `0x0ad08004` | `ipe_0_gdsc` | new six-domain camera reference |
| `0x0ad09004` | `sbi_gdsc` | new six-domain camera reference |
| `0x0ad0a004` | `ife_0_gdsc` | new six-domain camera reference |
| `0x0ad0b004` | `ife_1_gdsc` | new six-domain camera reference |
| `0x0ad0c144` | `titan_top_gdsc` | new six-domain camera reference |
| `0x0af03000` | `mdss_core_gdsc` | outside this increment; no newly assigned owner |

This table records the camera-only increment. The later
[MDSS reference](mdss-gdsc-reference.md) assigns only its own four-byte word,
without expanding this camera type or its six-entry VMState.

The six camera registers sit within the declared CAMCC address range, but the
current board has no CAMCC controller mapping there. A future CAMCC model must
exclude these six four-byte resources or use a container that preserves their
ownership. Mapping an entire camera controller as RAM is not part of this work.

The existing GPU generic device has only the GX instance, a version2 payload
without per-domain physical identity, and an already used `qcom-gdsc` global.
Adding camera instances of that type would expand the old option's meaning and
risk migration state confusion. The new directly derived SysBus type keeps
that code, instance order, profile scope, and migration payload unchanged.

## Explicit reference decisions

The six domains share one parent input but keep independent controls and HW
request inputs. The opt-in profile assumes cold collapse, no extra hidden
voters, and synchronous Boolean availability. Unknown selected HW requests
remain unknown. It adds no time, IRQ, analog voltage, clock, execution engine,
retention operation, or firmware success response. DT-derived mode policy and
resource geometry are protected by the new independent version1 migration
identity. See the implementation document for the precise 194-byte field order
and atomic rejection semantics.

At the time this contract was prepared, the camera model had only source and
static review. Compilation, component runtime, and original-image progress
require the next formal CI artifact and separately archived native result.
No whole-board or Android success is inferred from this contract.
