# Sources and provenance

## Upstream baseline

- [QEMU 11.1.3 release](https://www.qemu.org/download/)
- [Pinned source](https://github.com/qemu/qemu/tree/005c61c0ef6f6ae615283440b4bb7be51b2c6ddd)
- [Arm machine guidance](https://www.qemu.org/docs/master/system/target-arm.html)
- Local QEMU `target/arm/tcg/cpu64.c`, `hw/arm/boot.c`, `hw/arm/sbsa-ref.c`,
  `hw/intc/arm_gicv3*.c` and device-model APIs at the pinned revision.

## Hardware/driver references

- The owner's direct [Android board capture](evidence/android-board.json),
  [normalized profile](evidence/board-profile.json) and collector source hash.
- [Linux v6.12 SM8250 DTS](https://github.com/torvalds/linux/blob/v6.12/arch/arm64/boot/dts/qcom/sm8250.dtsi)
- [Linux v6.12 GENI serial driver](https://github.com/torvalds/linux/blob/v6.12/drivers/tty/serial/qcom_geni_serial.c)
- [Linux v6.12 GENI register definitions](https://github.com/torvalds/linux/blob/v6.12/include/linux/soc/qcom/geni-se.h)
- [Linux v6.12 QUP/GENI support](https://github.com/torvalds/linux/blob/v6.12/drivers/soc/qcom/qcom-geni-se.c)
- [Original TB371FC Linux port](https://github.com/oproskyrin/TB371FC-6.12)
- [Owner's Linux research fork](https://github.com/cyjin-yl/TB371FC-6.12)

The board capture takes precedence for board-specific active paths. For
example, its enabled Android console is at `0xa90000`, while the generic
SM8250 UART at `0x988000` is disabled in the captured Android tree.

## Qualcomm QEMU research: useful but not a TB371FC emulator

- [39C3 presentation source](https://github.com/rmalmain/39C3-build-a-fake-phone-find-real-bugs)
- [Qualcomm QEMU commit ac2521b0dfca2dbe19fb75661f8f1637186a44a4](https://github.com/qualcomm/qemu/tree/ac2521b0dfca2dbe19fb75661f8f1637186a44a4)

That revision identifies itself as QEMU 10.0.3. Its `hw/arm/qcom-virt.c`
targets KaanapaliT r2 and extends generic `virt`. Its graphics code targets
Adreno 840/Gen8 (`qcom,adreno-gpu-gen8-2-1`), while `franksmmu.c` implements
a custom mapping protocol used with a modified guest driver. In `hfi.c`,
the raw command issue message is handled by a simple acknowledgement.

The implementation is useful research for resource/firmware contracts,
but is neither an SM8250 SMMUv2 model nor A650 rendering support. No code
from that branch is imported by this initial TB371FC model. Future reuse
must preserve each file's license and attribution and port the actual
Kona resource tables and device contracts.
