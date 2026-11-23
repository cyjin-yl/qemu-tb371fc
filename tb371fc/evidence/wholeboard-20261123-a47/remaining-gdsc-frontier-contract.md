# Remaining NPU, video and display GDSC frontier

This is a read-only preparation note. It does not add devices, change the camera reference, or predict the next native exception. Source 09c638's observed failure remains the BPS word at 0x0ad07004; the six camera words are the complete scope of the proposed 78 increment. The six words below remain deliberately unowned in that increment.

## Exact inputs and scope

The actual DT is the unchanged 973,052-byte input with SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
The bounded GDSC extraction is 513,559 bytes / SHA-256
`6dbe11407254a358648375ec13bb07baf5c7cee06f6709e835d9e2908bdbf0c1`;
the parent-chain supplement is 19,367 bytes / SHA-256
`8a8bdded1c180c56a9029a3ef954c1817554b70e8cda52696bebb9075fb56ac2`.
They are preserved, byte for byte, in camera model commit
`c2118d645a1d54d66d6da8817a99443acf62acf9` under
`tb371fc/evidence/wholeboard-20261123-a47/`.

All kernel statements here refer to OnePlusOSS/android_kernel_oneplus_sm8250
commit `94304e12a3509eac7b3716bf574ce0b1fcaacbae`, not a moving branch.
QEMU ownership and clock descriptor indices refer to
`09c638d6a6d4fa4a4e4e332a7b39aa7bf7f5418e`, with the separately reviewed
78 camera-only addition identified above.

The six selected DT nodes each declare a single four-byte resource. Their
/soc bus uses one address cell and one size cell; its recorded ranges entry
maps bus zero to physical zero across these addresses. This translation is
an independent interpretation of the recorded cells; the original collector
explicitly did not claim to perform address translation.

| Domain / regulator name | Physical word | Declared parent | DT HW mode declaration | GCC binding ID / current descriptor ID | AHB register |
|---|---:|---|---|---:|---:|
| npu_core_gdsc | 0x09981004 | No parent-supply or vdd_parent-supply | Absent | 43 / 42 | 0x4d004 |
| mvs0c_gdsc | 0x0abf0bf8 | phandle 104, mmcx.lvl | Absent | 205 / 196 | 0xb004 |
| mvs1c_gdsc | 0x0abf0c98 | phandle 104, mmcx.lvl | Absent | 205 / 196 | 0xb004 |
| mvs0_gdsc | 0x0abf0d18 | phandle 104, mmcx.lvl | Present | 205 / 196 | 0xb004 |
| mvs1_gdsc | 0x0abf0d98 | phandle 104, mmcx.lvl | Present | 205 / 196 | 0xb004 |
| mdss_core_gdsc | 0x0af03000 | phandle 104, mmcx.lvl | Present | 24 / 23 | 0xb00c |

All six declare qcom,retain-regs and one ahb_clk. None declares domain-addr,
sw-reset, hw-ctrl-addr, skip-logic-collapse, enable-root-clk,
force-enable-root-clk, reset-aon-logic, retain-mem, retain-periph,
disallow-clear, no-status-check-on-disable, clk-dis-wait-val or a timeout
override. Thus the fixed driver uses its 100-us software polling bound.
That bound is not a measured power transition latency.

The video nodes request one software bus path, master 1 to slave 596:
idle 0/0 KBps and active 0/1 KBps. MDSS uses slave 590 with the same two
vectors. The NPU GDSC node declares no such bus-vote properties. These are
driver access requests, not measured throughput or a specification for
executing video/NPU/display traffic.

## What can be reused, and what cannot

The fixed [gdsc-regulator.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gdsc-regulator.c)
uses the same single GDSCR access path as the camera nodes: bit 0 software
collapse, bit 1 hardware-control selection, bit 11 retain-flops request,
bits 12..15 clock-disable wait field, and bit 31 power feedback. Probe
reads the word, clears HW_CONTROL and SW_OVERRIDE, and writes the complete
word back (lines 847..859). Optional auxiliary register paths are absent
for these six actual nodes. The support-hw-trigger property only enables
regulator mode operations (lines 687..691); absence does not prove that
the silicon lacks a HW_CONTROL bit.

A future bounded digital reference could reuse the same three-state
reasoning and strict per-word storage rules, with independent identities
and explicit opt-in. It must not enlarge the already frozen six-camera
VMState array or reinterpret the old GX profile as permission to power
other devices. The existing generic qcom-gdsc has a GPU-specific 18-byte
VMState without physical geometry; adding these as interchangeable
instances would need a separate compatibility design.

The four video domains and MDSS are siblings of the camera domains on
mmcx, not children of TITAN or of one another. The supplement binds
phandle 104 to qcom,rpmh-arc-regulator / mmcx.lvl; its leaf level limits
do not define an analog voltage or a threshold for QEMU POWER_ON.
Current 78 wiring consumes RPMh power[3] once for the camera aggregate.
A later companion must use an explicit fan-out or another reviewed
sharing mechanism; a second qdev_connect_gpio_out call must not silently
replace the camera connection.

The NPU node has no declared parent supply. This is not evidence that its
physical rail is permanently on, or that it should be connected to mmcx.
Any future NPU reference must retain an explicit unknown parent input or
describe a separately selected boot-supply assumption. A collapsed domain
can still have known-off feedback without asserting an unknown parent is
powered.

## Current clock-controller ownership and retention boundary

The current NPUCC sparse windows deliberately leave offset 0x1004 open:
the neighboring window at 0x1000 is exactly four bytes, followed by the
0x1010 RCG window. NPUCC's complete source hash is
`15565e894632ec0eeacd262c1ebfe0ac11fcfbf6cd7d175f165d18898b5214ba`.
There is no current VIDEOCC or DISPCC model in the board source.

The actual provider nodes reserve VIDEOCC 0x0abf0000..0x0abfffff and
DISPCC 0x0af00000..0x0af1ffff. Any later clock-controller model must
continue to leave their GDSC words to their explicit owner. The current
display RPMh RSC begins separately at 0x0af20000. Mapping an entire
controller range as RAM would lose this ownership and error boundary.

Fixed [gcc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gcc-kona.c)
declares NPU_CFG_AHB at 0x4d004, VIDEO_AHB at 0xb004 and DISP_AHB at 0xb00c,
each with clk_branch2_ops and no parent name. Binding IDs and the current
QEMU descriptor-array indices differ as shown above. The current
external input indices are 21, 79 and 10 respectively, all left unknown;
their names are not grounds for inventing a 19.2-MHz parent.

The GDSC driver calls CLKFLAG_RETAIN/NORETAIN_MEM and PERIPH in probe,
enable and disable. Fixed
[clk-branch.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-branch.c)
lines 132..182 use RMW on halt_reg, bit 14 for MEM and bit 13 for PERIPH.
The existing register write masks at these three words are:

| Register | Current mask | Minimal retention-request mask if later explicitly implemented |
|---|---:|---:|
| GCC 0x4d004 NPU_CFG_AHB | 0x00000003 | 0x00006003 |
| GCC 0xb004 VIDEO_AHB | 0x00000001 | 0x00006001 |
| GCC 0xb00c DISP_AHB | 0x00000001 | 0x00006001 |

This table is a future source-based delta, not a change included in 78.
The current 78 change only allows the camera word 0xb008's two retention
request bits. A cold clear of an already-zero bit can be skipped by
regmap_update_bits, so this note does not assert that any of these future
writes is the next native exception. Allowing their storage would still
not simulate physical retention or supply an unknown clock.

## MDSS proxy consumer is an additional software path

MDSS alone in this six-word set has proxy-supply pointing to its own
phandle 112. The extraction preserved qcom,proxy-consumer-enable in the
omitted-property-name list. Its raw value was not published, but the
fixed driver tests property presence, so its boolean meaning is known
without another capture.

Specifically,
[proxy-consumer.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/regulator/proxy-consumer.c)
lines 54..58 and 77..78 check presence; lines 92 and 119..125 obtain the
proxy regulator and call regulator_enable. Fixed regulator/core.c
lines 4758..4760 invoke this helper during registration. The helper is
conditional on CONFIG_REGULATOR_PROXY_CONSUMER in its header; this memo
did not establish that production build option or observe the path
running. With it enabled, the proxy is an ordinary software consumer
request, not a hardware reset value. Its removal at late_initcall_sync
does not establish when the current guest reached that phase.

The self proxy is distinct from the real mmcx parent. It must not become
a circular hardware power-parent edge, an invented always-on default, or
a proxy register block. Actual enable and retention requests should use
the normal regulator/GDSCR/GCC paths if they occur.

## Evidence limits and next-step discipline

Actual direct consumers include the NPU driver and several CoreSight
nodes, Venus PIL/VIDC/CVP, SDE RSCC and the rotator. These references do
not prove those engines or firmware execute. Neither a modeled POWER_ON
bit nor clock registration establishes NPU inference, video decoding,
display scanout, DMA, firmware authentication, or Android userspace.

The appropriate next increment remains conditional on the actual next
native trace. Keep source/DT geometry, current mapping ownership and
reference assumptions separate. If a future model changes a formerly
unmapped address, update its dedicated ownership/negative-access test
explicitly; do not silently remove the existing hole assertions.

This note performed local read-only parsing/source review and fetched
fixed public source text. It did not execute any collector, build,
QEMU, public test, native run, firmware, or guest instruction.

## Additional source identities

| Fixed source | Bytes | SHA-256 |
|---|---:|---|
| gdsc-regulator.c | 24838 | 41acd7aa326c656db5b9dbe57c5fe05f1783abf8a299aaf9f8d4c58b9ecea3d2 |
| gcc-kona.c | 115577 | ed208678508c983d0989f1f135fac898c5a9a91c14af1fa42cd7ea2f24cd14cd |
| clk-branch.c | 10657 | 375830dc9cd0ecbc12ea018eee1d260d8ba724dc52f1ac8b3589a07bc75d08aa |
| qcom,gcc-kona.h | 10232 | ec7b2a775a7f96a543c064c7fc733fb83ffa7e3dae3c4bb517200627ae5f7fed |
| proxy-consumer.c | 6192 | a0e6a6d34301b8137b46881200a694f82db037a34c3b1b0b2ea2b3e63342312c |
| regulator/core.c | 138850 | a597f9a87336974bf8266333b1aeb0772fc94537705ba689579e644eba5e41d1 |
| regulator/proxy-consumer.h | 784 | 7e472b493f6d5b255c283edde716599e274c39b387536e1843ec8e58e2741fd5 |

