# GENI I2C native observation — 3803138

The single native invocation of artifact **15070246969** ended in **kernel-failure**. All nine configured GENI I2C instances emitted their 400 kHz initialization message; the complete log contains 19 I2C errors with return value `-107`. The kernel continued to NPU probe, where a mapped NPU GDSC read was rejected because power feedback was unknown. Zero original LUNs were attached. Android userspace, completed stock boot and interactive display were not established. The artifact's native once claim is consumed; another native invocation of this artifact is not permitted.

The accompanying [independent review](independent-review.json) passed **2,231 execution, provenance and evidence checks** on 2026-11-26. Its review result is separate from the failed native boot result. The native invocation itself ran on 2026-11-25, from the recorded dispatch at 23:44:28 UTC to the foreground launcher exit at 23:45:35 UTC.

## Observed I2C progress

The complete printk log contains one `Bus frequency is set to 400000Hz` message for each configured controller:

| Instance | Base | Printk line | Guest seconds |
|---|---|---:|---:|
| SE1 | `0x00984000` | 1343 | 5.311203 |
| SE3 | `0x0098c000` | 1367 | 5.398949 |
| SE5 | `0x00994000` | 1368 | 5.403830 |
| SE9 | `0x00a84000` | 1384 | 5.423431 |
| SE11 | `0x00a8c000` | 1389 | 5.433956 |
| SE13 | `0x00a94000` | 1413 | 5.451058 |
| SE15 | `0x00884000` | 1414 | 5.455068 |
| SE16 | `0x00888000` | 1415 | 5.459543 |
| SE19 | `0x00894000` | 1416 | 5.463847 |

These are positive observations of initialization and continued kernel execution. The 19 `i2c error :-107` messages are recorded errors under the explicit `empty-bus-v1` reference policy: protocol 3, no attached slaves, and address-phase NACK for supported commands. They do not establish a successful peripheral transaction, useful payload transfer, DMA data movement, or physical register identity. The published model and policy remain the source of that limited virtual behavior. The prior `0x00984068` invalid-read and `get_se_proto` fatal signatures are absent, but that absence alone is not used as proof of success.

## First fatal fault: mapped NPU GDSC rejection

At guest time **5.524890 seconds**, printk line **1564** reports a synchronous external abort, `ESR 0x96000210`, on **CPU 6 / PID 1**. The symbols are **PC `regmap_mmio_read32le+0x8/0x20`** and **LR `regmap_mmio_read+0x40/0x68`**. The recorded call path includes:

`regmap_mmio_read32le → regmap_read → gdsc_enable+0x278/0x560 → npu_enable_core_power+0x104/0x1e8 → npu_probe+0x648/0x1430`.

The complete active memory map has the unique four-byte leaf **`0x09981004–0x09981007`, `qcom-npu-gdsc.npu-core-control`** at line **432**. QEMU line **29495** records a rejected **32-bit read**, domain 0, offset 0, reason **`power-feedback-unknown`**. The immediately following Data Abort records **FAR `0xffffff800ae9d004`** and **ELR `0xffffff800884e968`**. This is a rejection by a mapped model, not an unmapped address. The diagnostic's printed zero is not a successful register readback and does not establish whether the physical NPU rail is on or off.

The subsequent init panic occurs at **5.528973 seconds**, line **1615**. The complete QEMU log contains two Data Aborts; the earlier CPU 5 / FAR 0 / ESR `0x96000005` record is not reclassified as this first fatal printk event. Existing warnings remain in the independent review and are not declared fixed. No new power, parent-supply or clock-availability conclusion is made here.

## Execution and scope

- The previously completed CI qualification binds **107 component gates plus 5 Linux gates**, 47 host unit tests and 11 ordered independent audits to this artifact. Native behavior is assessed separately.
- The paused QCE observer completed **146 requests / 146 replies**, with two identical **70-getter** snapshots. It issued no MMIO, RAM, QTest, QOM-set or `cont` command. The saved full memory map equals the actual reply bytes.
- The existing 33 globals were retained. The single added machine property was `geni-i2c-enabled=on`, immediately before the two retained SPMI fuse properties. The nine I2C mappings were checked in the already collected full memory map; the preflight issued zero I2C QOM getters or MMIO commands.
- The native runner and host returned **2**. QEMU returned **0** after normal QMP shutdown. This actual native trace has **22 messages / 16 replies**, quit acknowledgement **16**, and final drain reset / errno 104 with zero residual bytes. Normal cleanup does not establish Android boot success.
- Input selection was **no-lun-diagnostic**, with zero original LUNs and incomplete storage. The mapped-ready consumer was present but its six-LUN route was not exercised.
- Original core input, AOP and symbol hashes were reported unchanged by the pinned runner and coordinator. The reviewer did not independently reread remote inputs. It did not invent a separate QEMU wait-call journal or replay inventory-only ring bytes.
- Kernel entry and ramdisk unpack were observed. No Android userspace, completed stock boot, interactive display, cryptographic correctness, physical power state or successful I2C payload claim is made.

## Exact evidence identities

Source commit: `3803138af99cc2c9d227127c15f4de8a3e0d124a`; Git tree: `ed448edde1fecec194b899f23f6a6afeb1c78a81`; completed push run: `45712384004`.

| Item | Bytes | SHA-256 |
|---|---:|---|
| CI archive, artifact 15070246969 | 83,258,649 | `78f418fc4b360b43a8a95e3cff118cd138a743f3f84e9d7bf4bf3ed22545c880` |
| Executed QEMU ELF | 63,932,728 | `0e40f17cc4c604294e444eddef1b75a884c817929e9f174f4d28b02e2a33b4ba` |
| Complete private collection manifest | 3,314,330 | `02bf36ef1301e2379468d36bfdb1ae63c065aaeb963f541ed6ed78ff2ffbcc3b` |
| Complete native printk log | 104,189 | `16904458b863ff8e1e65d09a0e2bd0bc325e6826e6aa10365f57c661bde35808` |
| Complete native QEMU log | 1,074,862 | `5cf86663840c6c309fad3e9b81bfd457344a2589d43f6d9ef522473f7f08f556` |
| Exact independent public review | 35,921 | `26ede883c0d5f74e08cbc0fab55c6914ee9944ebf39b6d55cf6f6295ef532278` |

All **49 complete private files / 2,470,821 bytes** were rechecked against the complete manifest, embedded bytes and public alias pins when preparing this package. Two ring/banner records remain inventory-only; two optional six-LUN files were absent. The 17-frame materialization is retained privately. Prior failed CI, qualification and preflight observations remain failed historical records.

Only this note, the exact sanitized independent review and their public manifest are included. Raw native logs, startup commands, private filesystem locations and binary payloads are excluded. No reviewer, native run, CI run, collector, device operation or remote operation was repeated while preparing this package.
