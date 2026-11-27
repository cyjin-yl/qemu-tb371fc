# Complete pinned source inputs

These files are complete, unchanged public source inputs for the existing RPM
statistics offset, DDR statistics offset, and GENI HS generation gates. They are
source data read by the existing generators; no copied upstream code is executed.

The three verifier commands now use the corresponding directory here by default:

| Verifier | Default source directory |
| --- | --- |
| `verify_rpm_stats_offset_generation.py` | `rpm-stats-offset/` |
| `verify_ddr_stats_offset_generation.py` | `ddr-stats-offset/` |
| `verify_geni_hs_sm8250_generation.py` | `geni-hs-sm8250/` |

Every complete input must still match the original generator's fixed byte count,
SHA-256, and Git blob SHA-1. The verifiers retain their existing checks, actual
generator subprocesses, byte-for-byte comparison with committed outputs, and the
GENI gate's 51 negative cases. None of the source revisions, expected source
identities, generated outputs, or gate verdicts changes with this input bundle.

`manifest.json` records the original repository, exact commit, path, URL, and three
content identities for every file. It is a provenance index;
the existing generator constants remain the validation authority. The 27 files
total 1,207,388 bytes and represent 26 distinct fixed source URLs. RPM and DDR
deliberately have separate `primary-upstream/` directories: their identically
named files come from different Linux commits. The vendor `kona-pm.dtsi` is the
one input shared by those two gates.

All original source bytes, copyright notices, SPDX identifiers, and line endings
are retained. The existing pins identify each complete upstream source file.

## Explicit input selection

`--source-root PATH` continues to select another complete local source directory.
`--network-source` explicitly selects the original pinned HTTPS downloader; these
two options are mutually exclusive. Receipts continue to report the actual
transport: `local` for local files and `shared-source-download` for the original
network path.

A missing or corrupt local input fails the gate. There is no automatic network
fallback. The shared source downloader is unchanged, including its cache
validation, transport refusals, request bounds, retry-delay budget, and respect
for `Retry-After`. Fixed local inputs avoid repeated downloads while preserving
complete source validation and generation checks.

The CI registry, workflow, gate names, and commands remain unchanged. Running a
verifier still requires a fresh output directory, for example:

```sh
python3 tb371fc/scripts/verify_rpm_stats_offset_generation.py --out /tmp/rpm-check
python3 tb371fc/scripts/verify_ddr_stats_offset_generation.py --out /tmp/ddr-check
python3 tb371fc/scripts/verify_geni_hs_sm8250_generation.py --out /tmp/geni-check
```
