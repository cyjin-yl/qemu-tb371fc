# SM8250 GENI HS reference profile and source provenance

This profile adds an opt-in `qcom-sm8250-geni-hs` reference UART at SE6 with a strict FIFO interface. Its `enabled-reference` property defaults to false. The inactive profile maps zero bytes; the enabled profile maps the 16 KiB SE window at `0x00998000` and only the four-byte wrapper version cell at `0x009c0004`. The existing console engine remains separate.

The source generation gates establish complete public-input identities, parser contracts and deterministic outputs. They do not establish C compilation, compiled QEMU behavior, a native kernel probe, Android startup or physical SE6 firmware state. Those require their own evidence.

## Evidence categories

The generated [register manifest](geni-hs-sm8250-registers.json) keeps three inputs separate:

1. **Public source facts.** Nineteen complete files, totaling 810,000 bytes, are pinned by immutable repository revision, length, SHA-256 and Git blob identity. The generator parses these files before deriving register, DT, clock, protocol and lifecycle facts.
2. **Reference model policy.** The [policy input](geni-hs-sm8250-reference-policy.json) explicitly chooses the enabled state, profile identity, wrapper compatibility value, FIFO geometry, error behavior and limits. These are declared emulator choices.
3. **Sanitized private producer facts.** The [fact input](geni-hs-sm8250-private-producer-facts.json) contains reviewed scalar values and complete range hashes. Public generation checks that declaration against public producer/consumer interfaces. It cannot reproduce review of the withheld private bytes.

The two JSON inputs are themselves complete-byte pinned. No private binary, instruction bytes, disassembly, private paths, boot arguments or device identity is needed by the public generator.

## Reference identity

| Generated macro | Value | Basis |
| --- | --- | --- |
| `GENI_HS_SE_BASE` | `0x00998000` | Public vendor HS UART DT node |
| `GENI_HS_SE_WINDOW_BYTES` | `0x4000` | Public vendor HS UART DT region |
| `GENI_HS_WRAPPER_VERSION_ADDRESS` | `0x009c0004` | Public wrapper base plus public version offset |
| `GENI_HS_WRAPPER_WINDOW_BYTES` | `4` | Deliberately bounded model policy |
| `GENI_HS_MAIN_FW_REVISION` | `0x201` | Reviewed UART producer scalar, checked against public field layout |
| `GENI_HS_SECONDARY_FW_REVISION` | `0x201` | Same declared UART image and producer rule |
| `GENI_HS_WRAPPER_VERSION` | `0x20050000` | Compatibility choice equal to reviewed image core version |
| `GENI_HS_CFG_REVISION` | `8` | Reviewed configuration byte used as fixed reference identity |
| `GENI_HS_FIFO_BYTES` | `64` | Existing emulator policy: 16 words of 32 bits |
| `GENI_HS_PROFILE_ID` | `0x47485331` (`GHS1`) | Explicit emulator profile identifier |
| `GENI_HS_DMA_TX_IRQ_MASK` | `0x400f` | Union of public TX DMA IRQ field definitions |
| `GENI_HS_DMA_RX_IRQ_MASK` | `0x77ff` | Union of public RX DMA IRQ field definitions |

The reviewed UART header contains protocol `2`, firmware version `0x0301`, configuration version `8` and core version `0x20050000`. Both reviewed producer copies consume the protocol byte and the low firmware byte: `(2 << 8) | 1` produces `0x201` at programming offsets `0x1000` and `0x1004`. The high firmware byte `3` is unused by those reviewed copies. The public producer also limits the revision field to its low-byte mask. The public consumer reads the protocol from bits 15:8 of the read-only revision views at `0x68` and `0x6c`.

This supports a coherent reference interface. It does not prove execution of the private producer, a live programming-to-read-only alias, physical SE6 assignment or reset retention. Reviewed static selection names engines `0x988000` and `0xa90000`; it does not establish selection of `0x998000`. The runtime selection condition was not captured. The saved driver binding supports a UART inference but cannot establish the exact firmware revision. `0x20050000` is a compatibility policy, not a captured wrapper-register read. The 64-byte FIFO is model geometry, not measured silicon geometry.

The fact declaration distinguishes a rehashed 1,048,576-byte source prefix from the recorded 3,670,016-byte full-source identity, which that reviewer did not independently rehash. It also pins both identical 2,112-byte UART image ranges and the reviewed producer/getter ranges without exposing their bytes. The complete fact input preserves these qualifications and exact hashes.

## Public DT and clocks

The parser selects the uniquely labelled `qupv3_se6_4uart` node with `qcom,msm-geni-serial-hs`. Other protocol nodes can share the same address, so a first-address match is insufficient. The public source records SPI 607, wake GPIO 19, wake byte `0xfd`, wrapper IOMMU SID `0x5a3` and source status `disabled`. These source properties are not a claim that every original private DT byte matches the public tree or that wake/DMA behavior is implemented.

The full public wrapper region is `0x9c0000/0x2000`; this model exposes only the version cell. The public GCC aperture is `0x100000/0x1f0000`.

| Clock | Vendor DT ID | QEMU internal index | Halt offset | Vote offset / mask | External parent |
| --- | --- | --- | --- | --- | --- |
| `gcc_qupv3_wrap0_s6_clk` | 100 | 99 | `0x1772c` | `0x52008 / 0x10000` | None; internal parent index 100 |
| `gcc_qupv3_wrap_0_m_ahb_clk` | 132 | 131 | `0x17004` | `0x52008 / 0x40` | 55 |
| `gcc_qupv3_wrap_0_s_ahb_clk` | 133 | 132 | `0x17008` | `0x52008 / 0x80` | 56 |

The S-AHB hardware clock-gating mask is bit 1 at `0x17008`. Wrapper0 reset is internal index 24 at `0x17000`, bit 0. Vendor DT IDs and QEMU internal indices are distinct namespaces. The generator parses both mappings and cross-checks physical register facts rather than assuming numerical identity. Public lifecycle parsing also checks that globals are applied before device realization, clock links require an unrealized device, and migration subsections load before the parent post-load callback; these source checks do not replace compiled migration tests.

## FIFO and DMA boundary

The reference uses aligned four-byte accesses and explicit transaction errors for unsupported operations. It stores publicly defined DMA IRQ enable masks and implements their SET/CLEAR aliases. The full RX field union is `0x77ff`, including the general-purpose extension bits; the observed cold-configuration RX mask `0x402f` fits within it. Mask storage does not implement DMA enable, pointers, lengths, status, transfer or completion. Those operations remain unsupported. Firmware RAM execution, baud timing, electrical flow control and a radio peer are also outside this profile.

The vendor transfer-mode enum is FIFO `1`, SE_DMA `3`; the separately pinned upstream enum is FIFO `1`, SE_DMA `2`. These source-specific values must not be interchanged. Later vendor nonconsole startup selects DMA, so a FIFO reference and readable UART protocol do not imply a complete HS startup, Bluetooth or Android success.

Model runtime and migration evidence belongs to the compiled-model gates. In particular, live TX length can be reprogrammed while a transfer is active, so the source implementation bounds `sent + remaining` without requiring equality to the current TX length register. RX counters can restart while canceled-command FIFO bytes remain. Source-generation evidence does not impose unsupported invariants on those states.

## Reproduction and failure preservation

Run from the repository root with Python's isolated mode and no site packages:

```sh
python3 -I -S -B tb371fc/scripts/test_geni_hs_sm8250_generation.py
python3 -I -S -B tb371fc/scripts/verify_geni_hs_sm8250_generation.py --out artifacts/geni-hs-source-generation
```

The verifier reuses `source_download.py` for public downloads. An offline run may add `--source-root SOURCE_DIRECTORY`; the 19 required cache filenames appear as `local_name` in the manifest. All input lengths and hashes are checked before decoding or parsing, and exact UTF-8 bytes are retained without newline normalization. Each public file is bounded to 256 KiB. No private artifact is downloaded.

The generator emits exactly `hw/char/qcom-sm8250-geni-hs-data.inc` and `tb371fc/docs/geni-hs-sm8250-registers.json`. The verifier runs the actual generator CLI and compares both complete outputs against the committed files and an in-process derivation. The C model is not used as the expected-value oracle; baseline UART/GCC inputs are pinned to immutable pre-change source.

The pure unit gate has **49 tests**. The source verifier has **51 negative cases**: 19 complete public-input identity mutations, 2 reference-input identity mutations, 19 parser mutations covering every public source role, 9 reference-scope mutations and 2 generated-output mutations. Parser and scope mutations deliberately bypass the identity layer inside their test path to prove the semantic validators independently. Every full mutant and its rejection record is retained under the verifier output directory. Native boot and compiled behavior are not outcomes of this source gate.

The [source CI contract](../evidence/geni-hs-sm8250-20261125-6a5/source-ci-contract.json) freezes exact schemas, list lengths, scalar predicates, counts and constant values. The [source evidence directory](../evidence/geni-hs-sm8250-20261125-6a5/README.md) records the successful final run and preserves the history of two initial parser failures and one duplicate-DT-property unit failure. Those earlier failures remain failures; their receipts and raw logs are pinned by complete hashes. The final parser corrected each issue and passed both gates.

## Immutable public inputs

Every link below includes an immutable revision. Complete SHA-256 and Git blob identities are in the generated manifest, together with all parsed contracts. Source files are checked in full, not by snippets or line matches alone.

| Input ID | Repository path | Bytes |
| --- | --- | ---: |
| [current_gcc](https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/hw/misc/qcom-sm8250-gcc.c) | `hw/misc/qcom-sm8250-gcc.c` | 29024 |
| [current_gcc_data](https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/hw/misc/qcom-sm8250-gcc-data.inc) | `hw/misc/qcom-sm8250-gcc-data.inc` | 63127 |
| [current_gcc_h](https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/include/hw/misc/qcom-sm8250-gcc.h) | `include/hw/misc/qcom-sm8250-gcc.h` | 1340 |
| [current_uart](https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/hw/char/qcom-geni-uart.c) | `hw/char/qcom-geni-uart.c` | 22547 |
| [current_uart_h](https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/include/hw/char/qcom-geni-uart.h) | `include/hw/char/qcom-geni-uart.h` | 1109 |
| [qdev_clock](https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/hw/core/qdev-clock.c) | `hw/core/qdev-clock.c` | 5316 |
| [qdev_core](https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/hw/core/qdev.c) | `hw/core/qdev.c` | 25849 |
| [upstream_header](https://raw.githubusercontent.com/torvalds/linux/75f2c0b3690702c90863c2e138cb5520670845ea/include/linux/soc/qcom/geni-se.h) | `include/linux/soc/qcom/geni-se.h` | 16853 |
| [upstream_se](https://raw.githubusercontent.com/torvalds/linux/75f2c0b3690702c90863c2e138cb5520670845ea/drivers/soc/qcom/qcom-geni-se.c) | `drivers/soc/qcom/qcom-geni-se.c` | 51564 |
| [upstream_serial](https://raw.githubusercontent.com/torvalds/linux/75f2c0b3690702c90863c2e138cb5520670845ea/drivers/tty/serial/qcom_geni_serial.c) | `drivers/tty/serial/qcom_geni_serial.c` | 58179 |
| [vendor-serial](https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/tty/serial/msm_geni_serial.c) | `drivers/tty/serial/msm_geni_serial.c` | 103712 |
| [vendor_gcc](https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/clk/qcom/gcc-kona.c) | `drivers/clk/qcom/gcc-kona.c` | 115577 |
| [vendor_gcc_h](https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/include/dt-bindings/clock/qcom,gcc-kona.h) | `include/dt-bindings/clock/qcom,gcc-kona.h` | 10232 |
| [vendor_header](https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/include/linux/qcom-geni-se.h) | `include/linux/qcom-geni-se.h` | 30916 |
| [vendor_kona](https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/arch/arm64/boot/dts/vendor/qcom/kona.dtsi) | `arch/arm64/boot/dts/vendor/qcom/kona.dtsi` | 132499 |
| [vendor_platform_se](https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/platform/msm/qcom-geni-se.c) | `drivers/platform/msm/qcom-geni-se.c` | 55444 |
| [vendor_qupv3](https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/arch/arm64/boot/dts/vendor/qcom/kona-qupv3.dtsi) | `arch/arm64/boot/dts/vendor/qcom/kona-qupv3.dtsi` | 32816 |
| [vendor_se](https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/soc/qcom/qcom-geni-se.c) | `drivers/soc/qcom/qcom-geni-se.c` | 23952 |
| [vmstate_core](https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/migration/vmstate.c) | `migration/vmstate.c` | 29944 |
