# Fixed public LLCC geometry input

The revision-named directory contains the complete, unmodified `sm8250.dtsi`
from [torvalds/linux at adc218676eef25575469234709c2d87185ca223a](https://github.com/torvalds/linux/blob/adc218676eef25575469234709c2d87185ca223a/arch/arm64/boot/dts/qcom/sm8250.dtsi).
This is the eighth input already pinned by `generate_llcc_sm8250.py`.

| Identity | Value |
| --- | --- |
| Complete bytes | 176883 |
| SHA256 | `8735b5d8bb61d6a3a579e5b088ff2a719132266cef57c91c319ec19e12770499` |
| Git blob | `630f4eff20bf810e347685066dce74ab92a23b56` |
| Source SPDX identifier | `BSD-3-Clause` |

The source retains its original copyright notice for The Linux Foundation
(2020). `BSD-3-Clause` is the complete license text from the same upstream
revision's `LICENSES/preferred/BSD-3-Clause` (1845 bytes, SHA256
`7a83b1aeb460458db9a4bd521d7f237c45b1650f6e60ea5ffc71e49472454de3`).
The verifier and generator retain their existing GPL-2.0-or-later headers.

The bytes were recovered locally from the previously successful public CI
artifact 14991304675 for commit 7d01e5e294b788f97a8120c2d34122ddb25e6343.
The complete archive is 57340476 bytes with SHA256
`1e6c37d205a9e130e4f197c068c93c756488b6b579ab06eadd429190b9b0bd6f`.
The original member is
`tb371fc-results/components/16-llcc-source-generation/fixtures/data/inputs/primary-upstream/arch/arm64/boot/dts/qcom/sm8250.dtsi`.
Its complete length, SHA256 and Git blob match the pre-existing generator pins.

By default, the verifier reads this bundled file only for that exact seven-field
source tuple. The existing length, SHA256 and Git blob checks still run before
regeneration. A missing or changed bundled file fails without a network fallback.
The other seven inputs keep their existing download/cache behavior. An explicit
`--source-root` continues to supply all eight inputs. The generated C include,
register manifest, reference geometry and output comparisons are unchanged.
