# SPMI debug-fuse native observation — d0b62a8

The single native invocation of artifact **15057062079** ended in **kernel-failure**. The kernel explicitly took the configured SPMI debug-unavailable branch, then encountered its first fatal fault while accessing GENI I2C. No original LUN was attached, Android userspace was not observed, and UI was not verified. The artifact's native once claim is consumed; another native invocation of this artifact is not permitted.

The accompanying [independent review](independent-review.json) passed **1,442 execution, provenance and evidence checks**. That is a review result; the native boot result remains failed.

## Observed kernel branch and next fault

At guest time **4.314094 seconds**, complete printk line **1261** contains the unique message:

> SPMI PMIC arbiter debug bus disabled by fuse

This is positive evidence of the kernel's disabled branch under the explicit virtual configuration. The configured word is `0x01000000`; its bit24 policy is a virtual reference. It is not a measurement of the physical tablet fuse. The pure preflight did not issue a fuse MMIO read or fuse QOM getter, and the SPMI debug-controller core was not mapped.

The first fatal error occurs at **4.505569 seconds**, printk line **1344**. Its corresponding QEMU diagnostic, line **27669**, is a **32-bit read at physical address `0x00984068`**. The recorded symbols are **PC `get_se_proto+0x4/0x10`** and **LR `geni_i2c_runtime_resume+0xdc/0x240`**. The fault is a synchronous external abort (`ESR 0x96000010`). This evidence identifies the next observed failure; it does not establish I2C firmware identity, register semantics, or a working I2C transaction.

The prior SPMI fault signature is absent in this complete run, and the explicit disabled-branch message supplies the stronger branch evidence above. Absence of other earlier fault strings does not establish that those paths succeeded. The complete QEMU log contains two Data Abort records; the I2C event is the first fatal printk error. Earlier warnings are retained in the independent review and are not declared fixed.

## Execution and scope

- The pure QCE preflight completed 146 requests /146 replies, including two identical 70-getter snapshots. It issued no MMIO, RAM, QTest, QOM-set or `cont` command. Its saved active memory map matches the actual reply bytes.
- The native runner and host returned **2**; QEMU returned **0** after normal QMP shutdown. The native trace has22 messages and quit acknowledgement16. The final drain was reset/errno104 with zero residual bytes. Normal process cleanup is not Android boot success.
- Input selection was **no-lun-diagnostic**, with **zero original LUNs**, storage incomplete, and no ready marker at selection. The mapped-ready consumer was present but its six-LUN route was not exercised.
- The existing33 globals were preserved. The explicit machine additions were `spmi-debug-fuse-enabled=on` and `spmi-debug-fuse-word=0x01000000`.
- Runner/coordinator records report unchanged hashes for the four original core inputs, AOP and symbols. The reviewer did not independently reread remote original inputs; no additional independent postrun full-input hash receipt is claimed.
- Kernel entry and ramdisk unpack were observed. Android userspace, completed stock boot and interactive display were not established. No cryptographic or DMA correctness claim is made.

## Exact evidence identities

Source commit: `d0b62a8b690803b13b5f66e46c29cd3aecea08a2`; Git tree: `eec9e8ef05cf83ca533fc58e0472547cdcd78735`.

| Item | Bytes | SHA256 |
|---|---:|---|
| CI archive, artifact15057062079 | 74,553,161 | `195b1df718ece4a270e5c7db98c45ecc95ed6865c80e9fdd9d27c0424d54eb46` |
| Executed QEMU ELF | 63,855,784 | `37bc860312db6d96e70b8c75e0283ca85a968bc4fb3f1debd56cf3f63584864c` |
| Complete private collection manifest | 3,142,830 | `aa2221195f1bce925b8921215b61cc9323dc5d2a4ad89b201ebfe34f49d14155` |
| Complete native printk log | 91,782 | `f244f5cd20a11bf064f21aef0a95890a5b255e1af8b5c540ce03974f80ab7355` |
| Complete native QEMU log | 1,009,583 | `5c1fd9343b030f745f7c572826913e05f948eb0fa2f4683f8d1e2dd98618c125` |
| Exact independent public review | 32,152 | `2dc393a50068d48e9ee95e06e3768919d0176760e464ef32075f34baf54abb70` |

The manifest and review bind49 complete private files /2,342,146 bytes. Two ring/banner records remain inventory-only, and two optional six-LUN files were absent. No raw ring decode was replayed locally. The earlier failed preflight remains a failed historical observation with no native invocation; it is not reclassified as passed.

Only this result note, the already sanitized exact review and their public manifest are included here. Raw native logs, startup commands, private filesystem locations and binary payloads are not included. No reviewer, native run, CI run or device operation was repeated while preparing this package.
