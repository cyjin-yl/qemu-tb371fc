# TCSR mutex and the missing SMEM handoff

This is a driver-protocol model for the TB371FC TCSR mutex bank. It does not create the SMEM data which the boot firmware normally supplies.

## Evidence and geometry

The [public geometry receipt](../evidence/smem-mutex-20261123/geometry.json) records the selected DT properties and their provenance.

The private extraction receipt is /work/smem-mutex-draft-20261123/metadata.json, SHA256 542935cce710f1f3251333f4ebb2bfc42218847899259ff1fa43ac0665ae5fd5. The extractor read existing device assets; it made no new tablet accesses. The live DT and the first DTB by file offset in the current boot_a backup independently agree:

| Item | Observed DT value |
| --- | --- |
| Mutex driver node | /soc/hwlock; compatible qcom,tcsr-mutex |
| Mutex syscon | /soc/syscon@1f40000; base 0x01f40000, size 0x20000 |
| syscon arguments | Offset 0, stride 0x1000 |
| SMEM node | /soc/qcom,smem; compatible qcom,smem |
| SMEM region | Base 0x80900000, size 0x200000, no-map |
| SMEM lock | /soc/hwlock, index 3 |

The pinned public kernel mirror at commit 9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1 registers 32 lock slots and selects the syscon offset and stride from DT. Its drivers/hwspinlock/qcom_hwspinlock.c SHA256 is 19722f9e4e3a8ddeb11500185745e8bf72911be1a3e751f5d62986d9d66e6778. This is a public source mirror, not a claim of an official complete vendor release.

The number of slots registered by the driver and the DT window do not prove that every execution environment can access every slot. No native reset owner or access-security register was captured.

## Protocol contract

Each slot has one aligned 32-bit owner word. Other addresses, unaligned transactions, and widths other than 32 bits fail as unsupported MMIO. The whole malformed transaction is rejected; it must not become multiple accepted word writes.

The driver writes owner 1 for APPS and then reads the owner to decide whether acquisition succeeded. The model uses an explicit reference arbitration contract: a nonzero write claims a known free slot; a held slot retains its current owner. Writing the same owner again does not add a recursion count.

Writing zero releases the slot. The model does not add an owner-only release ACL: the driver still writes zero after reporting that an owner differs from APPS. A zero write also establishes a known free state when the initial state was unknown.

An owner identifies an execution environment under this protocol. It is not a CPU identifier. Linux's hwspinlock core obtains a software spinlock before the hardware operation to provide local SMP exclusion. Readback of owner 1 on two APPS CPUs is therefore not a proof of CPU-level exclusion.

Only the APPS owner token is supported by direct evidence from this driver's access sequence. Other nonzero words in model tests are reference arbitration stimuli, not captured remote processor identities or proof of the hardware's complete owner-bit namespace.

## Initial state and reset

The device property boot-unlocked-reference is false by default. Reset leaves every slot's owner unknown. Reads and nonzero claims of such a slot return MEMTX_ERROR. The emulator does not silently choose a zero owner.

Setting boot-unlocked-reference=true explicitly selects an unmeasured reference handoff in which every slot starts free. Reference validation selects this explicitly with -global qcom-tcsr-mutex.boot-unlocked-reference=on. The board keeps the unknown default and needs no new machine field. The choice is not a measured POR or native boot state.

There are no model interfaces claiming to represent a real remote processor or secure-world owner. Native arbitration latency, interconnect ordering beyond QEMU's MMIO serialization, power collapse, and security restrictions remain unmodeled.

## Migration

The device migrates the 32 owner words and their known-state bitmap. Destination and source must select the same boot-unlocked-reference setting. A migrated unknown slot must not carry a nonzero hidden owner. Migration preserves held locks; it does not release them or invent a new owner.

## SMEM remains a separate dependency

The matching native smem.c SHA256 is 3ba12c3fdeca5fea8e0df97d30a7c13d8ce6a7553610b9a3700b036bd48af380. Its probe checks the SMEM header before requesting the hardware lock. Therefore the reported "SMEM is not initialized by SBL" failure does not demonstrate that this mutex previously worked.

The matching source supports SMEM layout versions 11 and 12, and defines 11 host slots. These are parser/protocol definitions; they do not tell us which version, allocations, partition host pairs, socinfo payload, or free offsets the tablet's boot firmware created.

No verified SMEM handoff blob is currently available. The intended strict private importer accepts original bytes and validates the selected region, native ABI, offsets, sizes, partition/item bounds, and overlaps. It does not create or repair entries. A runtime Android snapshot is also not automatically a pre-kernel SBL handoff: allocation state and remote IPC may have changed.

Synthetic data may exercise parser and arbitration tests. It must not be injected into a native boot as a purported captured partition directory or socinfo block.

## Validation status

The integrated model has been compiled and tested on the frozen binary recorded below. These component checks do not establish complete native-driver or tablet boot success.

## Recorded integrated validation (2026-11-23)

The [24 targeted checks](../evidence/boot-resources-20261123-1005/tcsr-mutex/report.json) and [55 public gates](../evidence/boot-resources-20261123-1005/public-55/result.json) passed on frozen binary SHA256 2173fa4da68f8f769c53942b056124c5621e4714652c7f572deacc9440ec09cf. Original receipts retain build/test source hashes. This validates the documented contract without expanding it to physical reset values, analog behavior, remote firmware services or full-tablet equivalence.
