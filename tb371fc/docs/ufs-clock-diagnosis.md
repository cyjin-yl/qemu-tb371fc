# UFS 300 MHz clock error: driver-visible revision mismatch

The first real Linux UFS I/O run passed all 16 data/device checks but printed
`cfg core clk ctrl failed` during probe and a subsequent devfreq `-34` error.
Those passing I/O checks did not establish error-free clock scaling. The
[source/hash diagnosis receipt](../evidence/expansion-20261123/ufs-clock-diagnosis.json)
identifies the exact binary, kernel, log and input properties.

The error follows directly from the controller revision advertised by the
model. Linux 6.12.114's
[`ufs_qcom_set_core_clk_ctrl()`](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/ufs/host/ufs-qcom.c?h=v6.12.114)
uses the declared maximum UniPro clock during startup/scale-up. It converts
300,000,000 Hz into 300 cycles per microsecond. For controller major versions
below 4, the relevant DME `0xd002` field is only bits 7:0, so `FIELD_FIT` rejects
300 and returns `-ERANGE` (34). For controller major 4 and later the driver uses
bits 27:16. The masks are in
[`ufs-qcom.h`](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/ufs/host/ufs-qcom.h?h=v6.12.114).

This failure occurs before the frequency change reaches GCC. It does not show
that GCC readback or its RCG divider calculation produced the wrong rate. The
initial model's controller major 3 was an explicit reference default, not a
value sampled from this tablet.

## Why the frequency table must remain 300 MHz

The real `/soc/ufshc@1d84000` node in the
[device inventory](../evidence/expansion-20261123/android-device-inventory.json)
sets both `core_clk` and `core_clk_unipro` to a 37.5 MHz minimum and 300 MHz
maximum. ICE has the same range. The original `freq-table-hz` property SHA-256
is `73bff8e4a1bd55ef26e3a5358cfcebf682bc79872bea06527497e9bc70f248d9`.
Upstream
[`sm8250.dtsi`](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/arch/arm64/boot/dts/qcom/sm8250.dtsi?h=v6.12.114)
also contains paired 37.5 MHz and 300 MHz core/UniPro operating points.
Changing the DT maximum to 150 MHz would hide the inconsistent revision while
moving away from both sources.

The Lenovo community candidate kernel at commit
`9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1` independently supports this diagnosis.
Its
[`ufs-qcom.c`](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/scsi/ufs/ufs-qcom.c)
selects `(300, 12)` for 300 MHz in
`ufs_qcom_set_dme_vs_core_clk_ctrl_max_freq_mode()`. The callee accepts 300 only
with the wider major-4 field. The file SHA-256 is
`5914361ac55598d1e2ffb6105bbe161d93536aba7144310cc67cb94d400ac739`.
This is source-based evidence, not proof that the candidate tree exactly matches
the production kernel.

## Reference configuration and limits

A **reference controller revision 4.0.0** (`HW_VERSION=0x40000000`) is consistent
with the cited digital driver contract and the observed 300 MHz clock table.
It requires the v4 `0xd002` field and the `PA_VS_CORE_CLK_40NS_CYCLES` attribute
at `0x9007`: Linux writes 12 at 300 MHz and 2 at 37.5 MHz. The guest driver's
validation remains intact; neither a generic success response nor a fake GCC
rate is needed.

The controller revision register has **not been measured** on the tablet.
Existing safe sysfs paths for the HCI/controller revision were absent, as
recorded in [UFS unit evidence](../evidence/expansion-20261123/android-ufs-units.json).
The observed PHY compatible `qcom,ufs-phy-qmp-v4` is useful context but does not
by itself establish the host controller's revision. Minor and step zero remain
explicit reference choices. A subsequent real Linux run must establish that the
new reference configuration removes the reported errors and preserves I/O;
this diagnosis is not that runtime acceptance result.
