# Public AOP loading and address research

Status: **source analysis only**. No device access, execution, repository ref changes, or vendor source copying was performed for this report.

## Provenance and limits

The principal source is the fixed third-party mirror [Rivko/android-firmware-qti-sdm670](https://github.com/Rivko/android-firmware-qti-sdm670/tree/20bb8ae36c93fc16bbadda0e0a83f930c0c8a271). It contains vendor-authored SDM670 target sources; it is not verified TB371FC/Kona OEM source. Exact paths, Git blob hashes, retrieved UTF-8 SHA256 hashes and scope are recorded in [sources-and-findings.json](sources-and-findings.json).

The sources establish a coherent SDM670 software loading convention. They do **not** establish the native tablet's AOP core model, physical RAM capacity, local aliases or core-reset release sequence.

## SDM670 address convention

| Region | AOP local base | AP/XBL base | Target-configured size |
|---|---:|---:|---:|
| Code RAM | 0x00000000 | 0x0b000000 | 0x18000 / 96 KiB |
| Data RAM | 0x000e0000 | 0x0b0e0000 | 0x8000 / 32 KiB |
| Message RAM 0 | 0x01300000 | 0x0c300000 | Hardware capacity not established here |

The target linker script `aop_proc/core/kernel/rex/target/670/aop.scl`, XBL `Target_cust.h` and the two target HWIO base headers agree on these software views. Dump lengths and generated aperture placeholders are not treated as hardware capacity measurements or proof of an architectural TCM implementation.

The same package also contains `common/Core/tools/cmm/common/sdm670/std_memorymap.cmm`, whose explicit legacy-update FIXME and incompatible code/data addresses make it unsuitable as geometry evidence. A path containing the target name is insufficient by itself.

## Build-time transformation and XBL loading

`aop_proc/core/bsp/build/scripts/aop_elf_builder.py` selects the 845/24/670/640 branch and adds 0x0b000000 to the physical and virtual addresses of non-hash PT_LOAD program headers at indices 0 and 1, and to the ELF entry field. The implementation copies the file and changes headers; it does not relocate payload instructions, literals or vectors. Other segments are not covered by this rule. Later signing-stage segment numbering has not been established.

The target `startup.s` supplies initial stack and local Reset_Handler vectors. Consequently an adjusted AP-view ELF entry is not sufficient to choose a Cortex-M reset PC. The local vector contents and their backing memory must be established separately.

`boot_images/QcomPkg/SDM670Pkg/Settings/SBLImage/sbl_images.json` selects AOP security type 33, loading and authentication, with execute/jump disabled. Its pre-procedure index 30 resolves to PMIC/SPMI preparation; there is no post-procedure. Cancel index 2 selects the download/VIRTIO skip predicate.

The normal `boot_elf_loader.c` path uses p_paddr as the copy destination, checks the complete p_memsz range, reads p_filesz, zeroes the remaining memory and flushes the data cache. It does not apply another AOP-local address subtraction. The generic `boot_config.c` execute/jump branches are not selected by this AOP configuration.

## Reset-release gap and closer Kona evidence

The generated AOSS header names bit 0 at relative offset 0x5002c as AOP_RESET. This is only a register definition: no reachable target write sequence, polarity or native physical address was established. `Clock_ConfigAOP()` configures PLLs, clock selectors/gates and shared handoff data. Its PLL reset operation is not evidence of AOP core-reset release.

The official Android [Kona DTS at c4540b8](https://android.googlesource.com/kernel/msm/+/c4540b8ee30c18988bab093454636b0c3bf1aa94/arch/arm64/boot/dts/qcom/kona.dtsi) describes XBL/AOP at 0x80700000/0x160000, command DB at 0x80860000/0x20000, SMEM at 0x80900000/0x200000 and QMP at 0x0c300000. It supplies closer SoC topology, but no AOP core identity, local code/data geometry or loader/reset implementation. No matching public Kona XBL implementation resolving these gaps was found in this research.

## Falsifiable next steps for the private native XBL

1. Locate the selected AOP image descriptor using its name, security type and configuration references together; establish the actual platform branch. A constant match alone is insufficient.
2. Follow native ELF fields into the loader's copy and zero-fill destinations. Determine whether p_paddr is used directly, adjusted, or translated by a table; compare every segment with the already-captured AOP image.
3. Establish separate code/data aliases and local vector backing. Treat DDR segments independently and reject any global subtraction hypothesis that fails one mapped segment.
4. Find a core-reset write or secure call reachable from the selected boot CFG, with arguments and ordering after loading, authentication, cache maintenance and required clocks. A generated register name or PLL reset does not meet this test.
5. Derive native RAM bounds from selected native range checks/configuration or an established density-register chain. ELF occupancy and stack pointers provide usage bounds, not hardware capacity. Keep CPU identity unknown without suitable native configuration or CPUID evidence.

These are analysis steps, not executed tests or a generated firmware handoff.
