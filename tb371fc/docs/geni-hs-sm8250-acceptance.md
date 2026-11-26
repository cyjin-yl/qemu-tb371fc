# GENI HS implementation and acceptance boundary

The new `qcom-sm8250-geni-hs` device is a separate, default-off reference profile. It addresses the observed original-kernel read of the UART protocol register at physical `0x00998068`. It does not establish a completed Android boot. The previous DDR diagnostic ended in a kernel failure at that GENI read with no LUN attached.

Enable the profile explicitly with:

```text
-global qcom-sm8250-geni-hs.enabled-reference=on
```

The existing console at `0x00a90000` remains a separate device and backend. The new profile uses serial backend 1, wrapper0 reset index 24, GIC SPI 607, and the named wrapper0 S6/M-AHB/S-AHB clock outputs. Only an enabled profile adds the two wrapper0 fabric parents and the three corresponding GCC hardware requests. An inactive profile maps neither its SE window nor its wrapper version cell.

## Implemented interface

The enabled profile exposes a strict aligned 32-bit FIFO interface, independent TX/RX byte queues, interrupt mask and clear behavior, source-defined DMA IRQ mask storage, and explicitly bounded configuration registers. Unsupported addresses, transaction sizes, reserved values, formats and DMA transfers return transaction errors without accepting an invented completion.

A disconnected or absent backend retains pending HS TX bytes. It cannot acknowledge delivery, discard the bytes or complete the command. Clock gating and reset control whether the interface and data path can progress. Paused and incoming machines cannot consume the HS backend before validation and the subsequent running transition.

The fixed data format is four little-endian 8-bit characters per FIFO word. The pinned vendor `se_get_packing_config(8, 4, false)` derives vectors `0x00e`, `0x10e`, `0x20e`, `0x30f`, and the canonical packing words `0x4380e` / `0xc3e0e`. Byte granularity is zero. This reference profile also accepts each packing register's reset value zero as an explicit alias for that fixed model format. That alias is a model choice, not a measured silicon reset behavior. Other packing vectors and byte granularities are rejected before any state change, and migration enforces the same boundary.

The profile does not implement firmware execution, DMA transfers, DMA pointers or completion status, baud timing, electrical flow control, wake GPIO behavior or a Bluetooth peer. The later vendor nonconsole startup selects DMA; passing the earlier protocol probe would therefore not prove full HS startup.

See [source provenance](geni-hs-sm8250.md) for the immutable public inputs, the reviewed `0x201` firmware-word derivation, and the explicit distinction between that reference declaration and unknown physical SE6 firmware state.

## Migration boundary

The legacy UART retains its existing VMState declarations and wire layout. The HS profile has a distinct version-1 section with a 48-byte immutable profile guard first. The full HS payload is 16,608 bytes: the guard, all 4,096 register words, every TX/RX FIFO byte, counters, canonical boolean bytes, three input-clock witnesses and reset/zero-length-command state.

The guard binds the configured enabled state, profile identity, physical windows, firmware revisions, wrapper version and FIFO geometry before functional fields are loaded. The post-load validator rejects unsupported register and format state, invalid counters, noncanonical booleans, incompatible reset state and clock-witness disagreement. Restored provider clocks are checked rather than repaired by the UART. No backend activity is needed to make a migrated state valid.

The compiled acceptance script checks two complete enabled/disabled migrations, both profile-direction rejections, 25 independent one-bit mutations and three structural mutations. It retains 30 complete stream files. The complete legacy section, including its actual Clock subsection framing, is independently decoded alongside the new HS section.

Each successful destination is observed twice through 49 pure getters covering both engines, before any post-incoming memory command or `cont`. A separate RAM witness follows both observations. Subsequent continuation must deliver only the pending bytes, without replaying the prefix already sent by the source. Negative targets must exit with the specific migration error and preserve the original source stream; a repaired or merely unfinished migration cannot pass.

The suite requires 58 process-cleanup receipts: 28 normal exits with acknowledged QMP `quit`, and 30 expected migration-rejection exits. Forced termination cannot satisfy either class. The IRQ checks intercept the device's actual sysbus output before incoming migration; QTest replaces the GIC connection for these fixtures, so these checks do not establish GIC routing or guest interrupt handling. The structural mutations cover a missing guard within a present section, an unknown section identity and version zero; they do not claim rejection of a completely absent HS section.

## CI integration

The component plan adds three gates: the 49-test pure source/parser unit suite, the complete source-generation verifier with 51 preserved negatives, and the 55-group compiled-model suite. The resulting plan has 102 component gates and five Linux gates. A normalized plan fingerprint test preserves every field and the order of the previous 104 contracts.

The receipt judge requires the source negative rows to contain explicit rejection evidence and the correct category totals. It also requires completed migration state, complete wire lengths, both pure observations, the independent RAM witness, specific rejection evidence and the absence of early I/O or state repair.

The first local integrated harness run executed 40 unit tests: 39 passed, and the existing socket-preservation test errored because that executor rejected `socket(AF_UNIX, SOCK_STREAM)` with `PermissionError` / errno 1. All four new contract tests passed. This is a retained local environment failure, not an all-pass harness result. The actual compiled and complete CI outcomes must be read from their later retained artifacts; authoring these scripts does not establish a runtime pass.

The post-merge `a91cf67d` CI run independently ended with 98 of 99 old component gates passing. Its LLCC source-generation gate downloaded seven of eight inputs, then exhausted the existing bounded retries on HTTP 429. That job did not run the subsequent Linux gates. Its artifact is `14998056908`, 50,359,699 bytes, SHA-256 `a4e3339f726ff2a20d24e58004ed179aaedb4c111b8f8561650d4b2d96e53ab7`. This failure remains recorded. The new optional, job-local source cache reduces repeated requests for complete immutable public files while each generator still verifies its full input identities.

## Reproduction

```sh
python3 -B tb371fc/scripts/test_source_download.py
python3 -B tb371fc/scripts/test_ci_device_tests.py
python3 -I -S -B tb371fc/scripts/test_geni_hs_sm8250_generation.py
python3 -I -S -B tb371fc/scripts/verify_geni_hs_sm8250_generation.py --out artifacts/geni-hs-generation
python3 -B tb371fc/tests/test_geni_hs_sm8250.py --qemu build/qemu-system-aarch64 --out artifacts/geni-hs-model
```

Use new output directories and retain unsuccessful attempts. The compiled test requires an actual dedicated QEMU build with QTest and TCG; it does not substitute a Python register model or read the C implementation as its value oracle.
