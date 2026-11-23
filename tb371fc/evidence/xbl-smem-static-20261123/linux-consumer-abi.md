# TB371FC SMEM consumer ABI: fixed-source comparison

This document records the Linux consumer contract used to compare a future
bootloader initialization trace with the TB371FC SMEM region. It does not
initialize SMEM, implement a new device, or establish a successful native boot.

The allocator reviewed is [drivers/soc/qcom/smem.c][smem] and its public
[API header][api] in the fixed third-party mirror
tem423/android_kernel_lenovo_tb371fc, commit
9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1. The exact source identity of the
production kernel has not been verified. No other platform's similarly named
msm_smem implementation is substituted for this source.

The machine-readable counterpart is
[consumer-abi.json](../evidence/smem-consumer-abi-20261123/consumer-abi.json).

## Evidence categories

| Category | Meaning |
| --- | --- |
| SOURCE_CONTRACT | Behavior and rejection conditions read from the pinned consumer source. |
| LAYOUT_DERIVATION | Offsets and sizes derived from the fixed-width C structures, not read from live RAM. |
| SUPPLIED_BOARD_GEOMETRY | The project supplied SMEM base 0x80900000 and size 0x00200000 from earlier board evidence; this review did not recapture them. |
| UNOBSERVED_BOOT_STATE | Actual bootloader initializer values and final shared-memory contents remain unknown. |

Private firmware payload, disassembly, identifiers and file offsets are not
included. This work used public GitHub source reads. It did not access doesvm,
execute firmware, create a model, run tests, or alter any running build.

## 1. Primary SMEM header

All numeric fields below use little-endian encoding. The largest member
alignment in these structures is four bytes. Offsets are derived from the
source structure layout. For the supplied board geometry, the exclusive end of
SMEM is 0x80b00000, and its last 4 KiB begins at 0x80aff000.

The primary struct smem_header is as follows. [Source: allocator][smem]

| Offset from SMEM base | Bytes | Field | Consumer use |
| --- | ---: | --- | --- |
| 0x0000 | 4 * 0x10 | proc_comm[4] | Legacy structures; this probe does not validate their contents. |
| 0x0040 | 32 * 4 | version[32] | Per-component version words. |
| **0x005c** | **4** | **version[7], SBL version** | **Only the high 16 bits select a supported layout: 11 or 12. The low 16 bits are not restricted here.** |
| **0x00c0** | **4** | **initialized** | **Must equal exactly 1.** |
| 0x00c4 | 4 | free_offset | Next free byte offset in the version-11 global heap. |
| 0x00c8 | 4 | available | Remaining bytes in the version-11 global heap. |
| **0x00cc** | **4** | **reserved** | **Must equal 0.** |
| 0x00d0 | 512 * 0x10 | legacy toc[] | Version-11 global item entries. |
| 0x20d0 | - | Exclusive structure end | Total header structure size: 8,400 bytes. |

The key physical words implied by the supplied base are:

- SBL version: 0x8090005c.
- initialized: 0x809000c0.
- reserved: 0x809000cc.

Each legacy proc_comm record has command at +0, status at +4, and two
parameters at +8 and +0xc. The primary allocator probe does not use these
records to establish initialization.

The version-11 global entry for an item is at:

    SMEM base + 0x00d0 + item_id * 0x10

| Offset within entry | Bytes | Field |
| --- | ---: | --- |
| +0x00 | 4 | allocated; lookup treats any nonzero value as allocated. |
| +0x04 | 4 | offset within the selected memory region. |
| +0x08 | 4 | size of the allocation, including alignment space. |
| +0x0c | 4 | aux_base; low two bits are masked by 0xfffffffc. |

aux_base equal to zero selects the default region. A nonzero value must match
a mapped region. Legacy lookup checks offset + size against that region's
length using a widened offset. An unallocated entry returns -ENXIO; an
unmatched auxiliary region returns -ENOENT; an out-of-range item returns
-EINVAL.

The source's initialization error is reached when initialized differs from 1
or reserved differs from 0. It occurs before the hardware lock is resolved or
reserved. This establishes a consumer predicate, not the actual values stored
by the bootloader.

## 2. Probe order and version selection

The fixed qcom_smem_probe implementation performs these steps. [Source][smem]

1. Parse memory-region. Map its first 4 KiB for the primary header and its last
   4 KiB for the partition table. If qcom,rpm-msg-ram is present, map that
   auxiliary region as well.
2. Require initialized == 1 and reserved == 0. A failure returns -EINVAL.
3. Resolve the first DT hwlocks specifier through of_hwspin_lock_get_id(node,
   0). Lookup errors, including -EPROBE_DEFER for an unavailable provider,
   propagate.
4. Reserve the specific lock through hwspin_lock_request_specific. A null
   result produces -ENXIO. Reservation is distinct from acquiring the
   cross-processor owner register.
5. Read version[7] and branch on its high 16 bits.
6. Enumerate APPS private partitions, tolerating -ENOENT but rejecting other
   negative enumeration results.
7. Publish the singleton __smem pointer only after the successful path.

Version-specific behavior:

| Major version | Global layout | Item bound |
| --- | --- | --- |
| 11 | Legacy 512-entry global heap; invoke legacy remapping. | 512 |
| 12 | Require and validate a global partition in the partition table. | SIII num_items if recognized, otherwise 512 |
| Other | Return -EINVAL. | None |

The major-11 remapping routine acquires the actual hardware lock, reads:

    p_size = header.available + header.free_offset

and releases the lock before remapping the primary region to p_size. Thus the
two fields influence memory geometry, not merely an informational counter.

**Source gap:** the fixed major-11 probe branch ignores the return value of
qcom_smem_map_legacy. It is incorrect to describe every legacy lock or mapping
failure as being rejected by the probe. This is a property of the reviewed
implementation, not permission to manufacture an accepted header.

For major 12, a missing or invalid partition table is fatal to global-partition
initialization. For major 11, the final private-partition enumeration may
return -ENOENT without failing the probe.

## 3. Partition table and optional information block

The partition table is located at:

    primary_base + primary_region_size - 0x1000

For the supplied TB371FC geometry, this is 0x80aff000. It is a shared-RAM
structure, not a separate MMIO register bank.

### Partition-table header

| Offset from table base | Bytes | Field and consumer condition |
| --- | ---: | --- |
| +0x00 | 4 | Magic bytes 24 54 4f 43, the ASCII string $TOC. |
| +0x04 | 4 | LE32 version; must be 1. |
| +0x08 | 4 | LE32 num_entries. |
| +0x0c | 20 | reserved[5]; not validated by this parser. |
| +0x20 | 0x30 per entry | Partition entries. |

A bad magic returns -ENOENT. A recognized magic with version other than 1
returns -EINVAL. [Source: qcom_smem_get_ptable][smem]

### Partition-table entry

Entry i begins at table + 0x20 + i * 0x30.

| Offset within entry | Bytes | Field |
| --- | ---: | --- |
| +0x00 | 4 | offset relative to the primary SMEM base. |
| +0x04 | 4 | partition size. |
| +0x08 | 4 | flags; currently unused by this consumer. |
| +0x0c | 2 | host0. |
| +0x0e | 2 | host1. |
| +0x10 | 4 | cacheline alignment for cached item headers. |
| +0x14 | 28 | reserved[7]. |

A partition's physical address is primary SMEM base plus entry.offset.
The offset field must not be interpreted as an absolute address.

### SIII information block

The consumer looks immediately after the last entry:

    info = table + 0x20 + 0x30 * num_entries

| Offset within info | Bytes | Field |
| --- | ---: | --- |
| +0x00 | 4 | Magic bytes 53 49 49 49, the ASCII string SIII. |
| +0x04 | 4 | size. |
| +0x08 | 4 | base_addr. |
| +0x0c | 4 | reserved. |
| **+0x10** | **2** | **LE16 num_items.** |

The structure has size 0x14 including trailing alignment. In the major-12 path,
an unrecognized SIII magic causes a fallback item count of 512. With the
recognized magic, num_items is used directly.

**num_items is an exclusive upper bound:** item IDs must satisfy
item < num_items. It is not the highest accepted ID itself. This parser does
not validate info.size, info.base_addr, info.reserved, or a nonzero/minimum
num_items value. [Source: qcom_smem_get_item_count][smem]

A complete header, entry array and info structure fit in the final page only
if:

    0x20 + 0x30 * num_entries + 0x14 <= 0x1000

This derives a maximum of 84 entries when the info structure is included.
**This is a geometry sanity condition, not a check implemented by the fixed
consumer.** The parser lacks an explicit final-page entry-count bound.

## 4. Global and private partition headers

Each partition begins with a 0x20-byte struct smem_partition_header.

| Offset within partition | Bytes | Field |
| --- | ---: | --- |
| +0x00 | 4 | Magic bytes 24 50 52 54, the ASCII string $PRT. |
| +0x04 | 2 | host0. |
| +0x06 | 2 | host1. |
| +0x08 | 4 | partition size. |
| +0x0c | 4 | offset_free_uncached. |
| +0x10 | 4 | offset_free_cached. |
| +0x14 | 12 | reserved[3]. |

### Major-12 global partition

The table search requires both entry hosts to equal 0xfffe. A missing entry,
zero offset, or zero size returns -EINVAL. Mapping failure returns -ENOMEM.

After mapping, the consumer requires:

- $PRT magic.
- The LE32 partition_header.size field at +0x08 equal to the selected
  table entry's size field; both declare the entire partition length.
- offset_free_uncached no greater than the LE32 partition_header.size
  field. This bound is the entire partition length, not sizeof the
  0x20-byte partition-header structure.

There is an important mismatch between the entry search and the header host
test. The entry search requires both hosts to be 0xfffe. The header is rejected
only when **both** header hosts differ from 0xfffe. Therefore a header with only
one matching host passes that particular test. This is documented as a source
gap; it is not evidence that the bootloader emits such a mixed-host header.
[Source: qcom_smem_set_global_partition][smem]

### APPS private partitions

The local host is 0. Enumeration:

- Skips entries that do not contain local host 0.
- Skips entries with zero offset or zero size.
- Rejects remote host >= 11 with -EINVAL.
- Rejects a duplicate already-registered partition for the same remote host.
- Requires $PRT magic in the mapped header.
- Requires the header to contain both the local and remote host.
- Requires the LE32 partition_header.size field at +0x08 to equal the
  table entry's size field (the entire partition length).
- Requires offset_free_uncached <= le32_to_cpu(partition_header.size).
  The comparison is against the entire partition length, not the
  0x20-byte structure size.

[Source: qcom_smem_enumerate_partitions][smem]

The probe does not explicitly validate every necessary geometry constraint:
entry offset + size within the original SMEM region, cacheline validity, a
minimum free cursor after the header, or the cached free cursor. Later item
operations add range checks. Those later checks do not prove the bootloader
initialization was correct, and parser omissions do not establish hardware
behavior.

## 5. Host IDs and allocator routing

| Identifier | Value | Meaning |
| --- | --- | --- |
| SMEM_HOST_APPS | 0 | Local SMEM host. |
| SMEM_GLOBAL_HOST | 0xfffe | On-RAM global partition host marker. |
| QCOM_SMEM_HOST_ANY | -1 | API sentinel, converted to 0xffffffff by the unsigned host argument. |
| SMEM_HOST_COUNT | 11 | Private descriptor index range 0 through 10. |
| QCOM_MUTEX_APPS_PROC_ID | 1 | APPS owner value in the hardware mutex protocol; a different numbering scheme. |

The allocator does not assign names to every host number from 1 through 10.
GLINK and SMP2P obtain remote IDs from their consumer DT properties. Their
meaning must be checked against the actual DT and bootloader partition
configuration rather than guessed from a generic platform enum.
[API][api] [GLINK][glink] [SMP2P][smp2p] [Mutex driver][mutex]

The public entries are:

    int qcom_smem_alloc(unsigned host, unsigned item, size_t size);
    void *qcom_smem_get(unsigned host, unsigned item, size_t *size);

Both route to:

1. The requested host's private partition when host < 11 and its descriptor
   exists.
2. Otherwise, the major-12 global partition, if configured.
3. Otherwise, the legacy global heap.

A requested remote host without a private partition therefore falls back to
the global allocation path. A successful lookup alone does not prove that a
specific private partition existed.

Common entry checks and outcomes:

| Condition | Result |
| --- | --- |
| __smem not published | -EPROBE_DEFER |
| item >= item_count | -EINVAL |
| alloc requests item < 8 | -EINVAL; these fixed entries belong to bootloader initialization |
| get requests item < 8 | Not rejected solely because it is a fixed item |
| Existing allocation | -EEXIST |
| Unallocated legacy get | -ENXIO |
| Missing private item | -ENOENT |
| Insufficient legacy allocation space | -ENOMEM |
| Insufficient private allocation space | -ENOSPC |

Both alloc and get acquire the hardware lock with irqsave and a 1000 ms
timeout, then release it after the operation. The API is allocation-only; it
does not provide a free operation. get_free_space does not acquire the hardware
lock and is described as a quick indication of allocation progress. It rejects
a computed free amount larger than its partition or region size.
[Source: public allocator APIs][smem]

## 6. Partition item headers, traversal and publication

A struct smem_private_entry has size 0x10.

| Offset within item header | Bytes | Field |
| --- | ---: | --- |
| +0x00 | 2 | canary, required to be 0xa5a5. |
| +0x02 | 2 | item ID. |
| +0x04 | 4 | size, including data padding. |
| +0x08 | 2 | padding_data. |
| +0x0a | 2 | padding_hdr. |
| +0x0c | 4 | reserved. |

Let P be the partition base and H the current item header.

Uncached entries grow upward:

    first_header = P + 0x20
    payload = H + 0x10 + padding_hdr
    next_header = H + 0x10 + padding_hdr + size
    returned_size = size - padding_data

Cached entries are traversed downward:

    first_header = P + partition_size - ALIGN(0x10, cacheline)
    payload = H - size
    next_header = H - size - ALIGN(0x10, cacheline)
    returned_size = size - padding_data

The Linux allocator in this file creates uncached entries. It rounds payload
size up to eight bytes, sets padding_hdr to zero, fills the item header,
executes wmb(), then advances offset_free_uncached. It does not thereby create
the remote service's protocol state or payload.

The get paths validate the canary, free-cursor ordering/ranges, monotonic
traversal, and returned payload range. For a matching entry they require
size < partition_size and padding_data < size. Corrupt geometry or canaries
return -EINVAL. These checks are consumer defenses, not observations of the
native allocation state. [Source: private alloc/get][smem]

Legacy allocation similarly writes the entry offset/size, publishes allocated
after wmb(), then adjusts free_offset and available.

The separate qcom_smem_virt_to_phys helper checks whether the pointer belongs
to a known private partition, global partition, or memory region, returning
the corresponding physical base plus offset. It returns zero for a pointer
outside those known ranges. This is a translation of a resolved item pointer,
not a substitute for validating an arbitrary physical address.

## 7. Hardware-lock reservation versus owner acquisition

The allocator source does not hardcode lock number 3. It asks for DT hwlocks
specifier index 0. If the board's first specifier selects lock 3, that is the
lock subsequently used. The core checks provider registration and local index
bounds; an unavailable provider yields -EPROBE_DEFER.
[hwspinlock core][lock-core]

| Stage | Cross-processor owner claim? |
| --- | --- |
| initialized/reserved check | No |
| Resolve DT lock ID | No |
| hwspin_lock_request_specific | Reserves the Linux lock object and performs relevant runtime power management; this is not the owner-register claim |
| Major-12 global/private metadata probe | No claim in this SMEM path |
| Major-11 legacy free-metadata read | Yes |
| qcom_smem_alloc and qcom_smem_get | Yes, for each operation |
| qcom_smem_get_free_space | No |

The fixed Qualcomm mutex provider has 32 locks. Its register for each index is
computed from the DT syscon base offset and stride. The normal claim protocol
writes APPS owner value 1, reads it back, and succeeds when the read owner is 1.
Unlock checks the owner and writes zero.

The provider is registered by postcore_initcall; the SMEM driver by
arch_initcall. Registration order does not prove a provider or remote owner is
ready in any particular boot. Neither the value 1 used by this mutex protocol
nor lock index 3 is a SMEM host ID. The native initial mutex owner remains an
unobserved boot-state value. [Mutex driver][mutex] [Allocator][smem]

## 8. Consumers need more than allocator probe success

### socinfo

socinfo retrieves item 137, SMEM_HW_SW_BUILD_ID, through HOST_ANY. If absent, the
fixed consumer falls back to dummy platform values. That fallback does not
establish authentic SoC or board information. It also retrieves item 469 for
the image-version table. The item IDs are declared by the fixed socinfo
header. [Consumer][socinfo] [IDs][socinfo-h]

### GLINK SMEM

The remote host is taken from qcom,remote-pid. Registration allocates or
retrieves descriptor item 478, and rejects a descriptor size other than
32 bytes. It allocates item 479 for the 16 KiB TX FIFO. RX handling later
retrieves item 480 created for the remote side. A valid heap does not create
that remote FIFO or execute a remote firmware service. [GLINK source][glink]

### SMP2P

Item IDs come from the two qcom,smem cells; local and remote IDs come from
qcom,local-pid and qcom,remote-pid. Linux allocates and initializes its own
outbound item, publishes its valid version after wmb(), and separately looks
up the remote inbound item. A valid SMEM allocator does not imply remote
negotiation or inbound status exists. [SMP2P source][smp2p]

## 9. Required bootloader value-chain evidence

The consumer offsets above identify targets for future comparison. They do
not reveal which platform configuration the current bootloader selected, or
what data it actually published.

Evidence still required includes:

1. The selected version configuration, any decoding/transformation, and the
   value actually written to version[7], including its major and minor parts.
2. The initialized and reserved writes, the completion path that performs
   them, and publication ordering relative to the structures they validate.
3. The actual TOC location, magic/version, entry count, and bounded entry list.
4. Every partition's host pair, offset, size, flags, cacheline, header, and
   cached/uncached free cursors.
5. Whether SIII exists and how its LE16 exclusive item count is formed,
   including any narrowing or transformation.
6. For major 11, the legacy free_offset and available values that determine
   remapping and allocation.
7. Fixed and preallocated item IDs, sizes, payload provenance, and their
   effects on the allocation cursors.
8. Agreement among DT remote IDs, partition hosts, and actual mutex ownership
   behavior.

All actual initializer values remain unknown in this analysis. Recognizing
bootloader labels or finding a Linux acceptance predicate is insufficient to
populate initialized=1, a version-12 header, an empty TOC, or invented native
items.

An explicitly declared synthetic/reference fixture can have its own contract.
Its results must remain separate from an authenticated native handoff and from
full-device or Android boot claims.

## 10. Source integrity

The following files were read at the same fixed commit. SHA-256 was computed
over the retrieved UTF-8 content. These are source identifiers, not firmware
or RAM hashes.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| [drivers/soc/qcom/smem.c](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/smem.c) | 33090 | `3ba12c3fdeca5fea8e0df97d30a7c13d8ce6a7553610b9a3700b036bd48af380` |
| [include/linux/soc/qcom/smem.h](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/include/linux/soc/qcom/smem.h) | 348 | `771dc38216ed963f1abaa40100d1d1376538149822dd814fbd52cb42a544cb86` |
| [drivers/hwspinlock/qcom_hwspinlock.c](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/hwspinlock/qcom_hwspinlock.c) | 4141 | `19722f9e4e3a8ddeb11500185745e8bf72911be1a3e751f5d62986d9d66e6778` |
| [drivers/hwspinlock/hwspinlock_core.c](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/hwspinlock/hwspinlock_core.c) | 27210 | `49e00d6858bc805734eecb039ff1a47ba177df6c9c4b3ab2f4ad21bb23dbc8ac` |
| [drivers/soc/qcom/socinfo.c](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/socinfo.c) | 59167 | `489ffdd5b94e958c09a7f5641551dbbc93d1a1226f5834aaa511df315bbeeb1e` |
| [include/soc/qcom/socinfo.h](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/include/soc/qcom/socinfo.h) | 10688 | `4765258d271e0a55bcf7c895117d424f6731690fb56410e9a2a321b3bcb9e2c4` |
| [drivers/rpmsg/qcom_glink_smem.c](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/rpmsg/qcom_glink_smem.c) | 7773 | `8385231087688deb6a4eee861d340941b80a2af1b804e659d088eac0df389b5f` |
| [drivers/soc/qcom/smp2p.c](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/smp2p.c) | 18621 | `4c2f3b054b69cad4a5f7868b058f2060f2657ef0dd7e84a1e2818e8a53daf0e2` |

The primary allocator Git blob is `173e1ecdc86ca145f4a8de5df848dc5145e1a4b1`.
The JSON includes all source Git blob IDs and the explicit evidence categories.

[smem]: https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/smem.c
[api]: https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/include/linux/soc/qcom/smem.h
[mutex]: https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/hwspinlock/qcom_hwspinlock.c
[lock-core]: https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/hwspinlock/hwspinlock_core.c
[socinfo]: https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/socinfo.c
[socinfo-h]: https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/include/soc/qcom/socinfo.h
[glink]: https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/rpmsg/qcom_glink_smem.c
[smp2p]: https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/smp2p.c
