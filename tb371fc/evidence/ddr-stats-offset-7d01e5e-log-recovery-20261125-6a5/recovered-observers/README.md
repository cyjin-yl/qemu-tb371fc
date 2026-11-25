# Recovered original DDR CI observer sources

Both Python files in this directory are byte-identical recoveries of the original frozen DDR observers. Their complete byte counts and SHA256 values match the recorded pre-interruption identities. They were reconstructed from the immutable RPM evidence commit `d103750ca2a191559daeb9eff4341b8c80013dc6` using the ordered transformations in `source-recovery.json`.

| Observer | Bytes | SHA256 |
| --- | ---: | --- |
| Board | 16,288 | `0dc1140336be191caef99046745c23e13ff48bb9fe51eab397da7a25a754bbbd` |
| A77 | 8,932 | `f2e491852c385ac2286f225ef79570fbc9c6792c52dc8405335925f3eeabe740` |

The board observer retains the complete original scope, including the selective read of the actual push artifact's CI unit members. The A77 observer retains all nine variants, the independently summed 852 guest checks, two rejected configurations and all 17 PCI UFS cases. Exact source, merge, tree, base, run, job and attempt bindings remain in the source.

This addition restores source bytes. The bodies of the previous observer receipts were lost with the old executor; their known hashes are retained as historical identities only. These recovered sources have not been executed by this recovery operation. A later read-only observation must produce a separately identified receipt.

The preceding commit `6bee0321ce9892c9448d062e54abaf499a54faa5` contains the unchanged four public CI logs, manifest and logs-only README. This append does not rerun CI or QEMU, execute a native attempt, or create a new qualification claim. No private firmware, native logs, host control data, credentials or signed download URLs are included.
