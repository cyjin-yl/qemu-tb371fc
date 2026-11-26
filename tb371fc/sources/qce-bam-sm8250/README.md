# Complete QCE/BAM reference-source bundle

This directory contains 12 complete, unmodified public source files (770,745 bytes). Each file is checked by complete length, SHA256 and Git blob SHA1 before the generator parses any source text. There is no network fallback. These files define source facts; the separately reviewed profile defines the virtual policy choices.

## Origins and licensing

The original copyright notices and SPDX statements are retained byte for byte. The complete fixed-revision vendor `COPYING`, `LICENSES/preferred/GPL-2.0`, `LICENSES/exceptions/Linux-syscall-note` and `Documentation/process/license-rules.rst` are included under `primary-vendor/`. Their complete source identities are recorded in `license-sources.json`. `BSD-3-Clause` preserves the notice already distributed with the same complete upstream SM8250 DTS in the LLCC source bundle. The YAML file retains its original dual-licence statement; this bundle may use its GPL-2.0-only alternative. The vendor Kona DTS has no inline SPDX or copyright header in the pinned original; no new per-file licence is invented here. The pinned repository COPYING states GPL version 2 with the Linux syscall note for the kernel as a whole and points to its per-file licensing rules. These complete primary notices supply the repository licensing background for the unchanged Kona file; this bundle does not add SPDX text to it or claim a new per-file exception.

| Key | Complete repository-relative source | Bytes | Original inline SPDX |
| --- | --- | ---: | --- |
| vendor_qce | [primary-vendor/drivers/crypto/msm/qce50.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/crypto/msm/qce50.c) | 188,196 | GPL-2.0-only |
| vendor_regs | [primary-vendor/drivers/crypto/msm/qcryptohw_50.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/crypto/msm/qcryptohw_50.h) | 18,355 | GPL-2.0-only |
| vendor_bam | [primary-vendor/drivers/platform/msm/sps/bam.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/platform/msm/sps/bam.c) | 69,776 | GPL-2.0-only |
| vendor_spsi | [primary-vendor/drivers/platform/msm/sps/spsi.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/platform/msm/sps/spsi.h) | 13,415 | GPL-2.0 |
| vendor_sps_bam | [primary-vendor/drivers/platform/msm/sps/sps_bam.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/platform/msm/sps/sps_bam.c) | 65,781 | GPL-2.0-only |
| vendor_sps_api | [primary-vendor/include/linux/msm-sps.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/include/linux/msm-sps.h) | 46,545 | GPL-2.0 |
| vendor_dt | [primary-vendor/arch/arm64/boot/dts/vendor/qcom/kona.dtsi](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/arch/arm64/boot/dts/vendor/qcom/kona.dtsi) | 132,499 | None in this complete file |
| linux_bam | [primary-upstream/drivers/dma/qcom/bam_dma.c](https://github.com/torvalds/linux/blob/adc218676eef25575469234709c2d87185ca223a/drivers/dma/qcom/bam_dma.c) | 40,461 | GPL-2.0-only |
| linux_binding | [primary-upstream/Documentation/devicetree/bindings/dma/qcom,bam-dma.yaml](https://github.com/torvalds/linux/blob/adc218676eef25575469234709c2d87185ca223a/Documentation/devicetree/bindings/dma/qcom,bam-dma.yaml) | 2,550 | (GPL-2.0-only OR BSD-2-Clause) |
| linux_qce | [primary-upstream/drivers/crypto/qce/core.c](https://github.com/torvalds/linux/blob/adc218676eef25575469234709c2d87185ca223a/drivers/crypto/qce/core.c) | 6,952 | GPL-2.0-only |
| linux_regs | [primary-upstream/drivers/crypto/qce/regs-v5.h](https://github.com/torvalds/linux/blob/adc218676eef25575469234709c2d87185ca223a/drivers/crypto/qce/regs-v5.h) | 9,332 | GPL-2.0-only |
| linux_dt | [primary-upstream/arch/arm64/boot/dts/qcom/sm8250.dtsi](https://github.com/torvalds/linux/blob/adc218676eef25575469234709c2d87185ca223a/arch/arm64/boot/dts/qcom/sm8250.dtsi) | 176,883 | BSD-3-Clause |

Vendor repository: `OnePlusOSS/android_kernel_oneplus_sm8250`, immutable revision `98719830b0de5584f5588bbe9f4c8304d97a64c6`. Upstream repository: `torvalds/linux`, immutable revision `adc218676eef25575469234709c2d87185ca223a`. Complete SHA256 and Git blob identities are stored both in the reviewed profile and as independent production constants in the generator.

Repository licence references: [vendor COPYING](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/COPYING) and [upstream COPYING](https://github.com/torvalds/linux/blob/adc218676eef25575469234709c2d87185ca223a/COPYING). The vendor COPYING and its three referenced licence/rule files have been completely read back at the pinned vendor revision and copied here byte for byte. The upstream link identifies the original repository terms; this preparation does not claim a separate readback of upstream COPYING.

## Reproduction

From the repository root:

```sh
python3 -B tb371fc/scripts/gen_qce_bam_sm8250.py --source-root tb371fc/sources/qce-bam-sm8250 --output-root .
python3 -B tb371fc/scripts/verify_qce_bam_sm8250_generation.py --out /tmp/qce-bam-source-generation-new
```

The verifier output directory must be new. The generator accepts existing outputs only when their complete bytes are equal; it refuses to overwrite different data. `--source-root` is the bundle root, containing the `primary-vendor/` and `primary-upstream/` paths listed in the profile. No code in this directory is executed as firmware or driver code.
