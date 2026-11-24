# Historical evidence retained from the default branch

This index preserves the independently recorded results published by commit
[`5fd52c120058fb2addedfa74fe5c855b138b590f`](https://github.com/cyjin-yl/qemu-tb371fc/commit/5fd52c120058fb2addedfa74fe5c855b138b590f).
The default branch and the later verified branch developed from common commit
`cc8cba4aa88353fe28e3a3c2365ae9c79f65cbf9`. Their differing histories must not
silently discard an earlier passing or failing receipt.

The [retained default-branch ledger](VALIDATION-default-5fd52c1-20261123.md)
is the exact original blob `e3af30027093dc9a29e884a0438e9c787e56235c`,
copied beside the current ledger so its relative receipt links remain valid.
Its [SHA-256 manifest](VALIDATION-default-5fd52c1-20261123.sha256) uses the
new archival filename. The original file content SHA-256 is
`487f6c398146bf783bb0f8129b5dfb102a3b976251d37e293edb294a8b087981`.
The [continued ledger](VALIDATION-continued-20261123.md) remains separate.

## Original measurements

| Retained receipt | Original result and boundary |
| --- | --- |
| [Frozen12 public suite](resume-final-20261123/result.json) | 46/46 public gates, 74.755 seconds, QEMU `178bace28c84bced9471b824d413feb18e76ddb15212ec32412ad7a7ec32a675`; public component tests and four clean-Linux boots |
| [Frozen12 production-kernel validation](production-gdb-frozen12-20261123/result.json) | 8/8 PID 1 checks, guest-requested shutdown and exit 0 on the same QEMU; unmodified production Image with generated subset DT and a validation initramfs |
| [Frozen12 full-native-DT diagnostic](native-dt-spmi-reference-20261123/result.json) | Failed boot; explicit SPMI reference profile, next fatal `qsee_irq_probe+0x140`; observer completion is not guest boot success |
| [Frozen12 SPMI reference component](spmi-reference-20261123/result.json) | 25 checks, 1.616 seconds, same QEMU; bounded reference transactions, errors, synthetic peripheral IRQ and actual migration |
| [Generic A77 validation](compat-final-20261123/a77/result.json) | 852 guest checks across nine configurations plus two invalid-configuration rejections; QEMU `c06bb42b52bc47839fbe40eb5a247c04d70b64840411af6efff1497d0a1d16c0` |
| [Generic UFS PCI validation](compat-final-20261123/ufs-pci/result.json) | 15/15 cases actually executed without skips on the same generic QEMU; shared-core/PCI scope, separate from the TB371FC board binary |

These are historical measurements, not a rerun against a newer model. Their
original input hashes, source provenance, positive and negative verdicts remain
unchanged. In particular, the frozen12 public receipt identifies a dirty source
tree based on the common commit; its later publication in `5fd52c1` does not
retroactively bind that run to a clean checkout of the publication commit.
No row establishes original Android userspace or complete-tablet behavior.

## Exact directory preservation

The following five directories contain 237 files that were absent from the
verified branch at `90c71325a7187bc24c747c4b220909d8ea2bf8c0`.
Their Git tree identities permit exact restoration without replacing any file
already present at that checkpoint.

| Directory under `tb371fc/evidence/` | Files | Original Git tree |
| --- | ---: | --- |
| `compat-final-20261123` | 60 | `3002cb1397232f4d25d30e3de55d1f1daf375d92` |
| `native-dt-spmi-reference-20261123` | 3 | `408599e738e427eb8f213ce5ea2fe8edabe8fee9` |
| `production-gdb-frozen12-20261123` | 2 | `0106603fe99ac387cc343f35ec9c997b993ae765` |
| `resume-final-20261123` | 143 | `b6ccc082acaf9d9ec95ffcf93576c64c67b435c6` |
| `spmi-reference-20261123` | 29 | `2cecbb439cf0ed12ead5c15f92fce048b25858d3` |

The two former `ufs-empty-lun-20261123/sha256.json` inventories are already
retained byte for byte as `private-source-manifest.json` at the corresponding
locations. Their [publication explanation](ufs-empty-lun-20261123/PUBLICATION.md)
and [reclassification record](ufs-empty-lun-20261123/manifest-reclassification.json)
distinguish full runtime inventories from the actually published subset.
Keep that classification and its published `SHA256SUMS`; restoring old names
does not add evidence.

## Source comparison boundary

A complete tree comparison of `5fd52c1` and `90c71325` found no source file
present only in the default branch. Outside `.github`, `README.md`, `hw`,
`include` and `tb371fc`, top-level tree identities are equal, including
`target`, `configs` and `tests`. The default board C source is retained
line for line with additions. Shared source differences are the later
display-RSC, DCC-operation, CPUSS-branch and validation changes described by
their respective commits. This comparison is a preservation audit, not a
substitute for compiling or executing the integrated result.
