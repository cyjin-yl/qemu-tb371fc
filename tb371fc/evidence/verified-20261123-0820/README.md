# Isolated verification, 2026-11-23 08:20 source snapshot

The [final verification](final-verification.json) combines three actual results:
47/47 public gates, 852 A77 guest checks across nine variants plus two rejected
configurations, and all 15 PCI UFS compatibility tests. The dedicated builds
come from source snapshot `9a80cddf1bd6ad8ecdbba34fa164acb5524e206b`.
The original JSON receipts are copied byte for byte.

The board binary is `87f52240ff55e0b60a87e9a0dc57b76dff75e653f3dbe1d5dbdc64b2b7d9ac89`;
the A77/PCI binary is `2605ddb7a6c503cff9532bcd1f24a0945b76f5f148149170ee3af7813beeec29`.
The public Linux Image is `c513268fb0727d289fdfc0f02637c53cf5f976cc836ea9c586763f5f584bce40`,
built without patches from the pinned official Linux 6.12.114 archive.

## Retained failed attempt

[The original sequential aggregate](initial-sequential-verification.json)
remains false. Its public and CPU tasks passed; PCI test discovery returned
`1..0` because the frozen file was named `qemu-system-aarch64-a77-pci`.
`tests/qtest/libqtest.c:qtest_get_arch()` interprets the complete suffix after
`-system-` as the architecture. A byte-identical real file with the canonical
`qemu-system-aarch64` name passed all 15 cases. No test assertion or model was
changed for that rerun. Both attempts and the unchanged-input recheck remain.

## Source and publication scope

The source hash maps before and after the sequential run are identical.
The CI runner, its verdict unit tests and the new real SPMI/PDC/GIC route test
are preserved in `runner-sources/`. After verification, root split one DCC
`default: break;` line; the separate metadata records the exact before/after
hashes and identical non-whitespace token stream. Frozen-run hashes still
identify the original tested bytes.

`SHA256SUMS` verifies every file actually published in this directory.
`public-47/private-source-manifest.json` preserves the original full runtime
artifact inventory; its ELF, object, initramfs and synthetic raw disk entries
are intentionally not all published. Logs, JSON reports and source hashes
are retained. No private production Image, native DTB or AOP is included.

The 47 gates cover 43 component/parser gates and four full 8-CPU, 8-GiB
clean-Linux boots. The added SPMI route observes actual SPMI → PDC input 1 →
GIC SPI 481 wiring and one real process migration with the remaining virtual
transaction deadline. It uses an explicitly synthetic test peripheral; it
does not establish native PMIC behavior, power-state wake equivalence or a
complete original-DT/Android boot.
