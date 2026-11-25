# SM8250 RPM statistics initialization reference

This optional leaf exposes the immutable state immediately after the identified
AOP statistics initializer. It is disabled by default. Enable it explicitly with
`-global qcom-sm8250-rpm-stats.enabled-reference=on`.

The reference does not execute AOP, sample physical-device memory, or report live
sleep/residency statistics. The input AOP image was a previously saved **current
active-slot** image; `firmware_is_pristine_stock` is false. Its identity does not
establish an untouched factory image or the complete production kernel's build
provenance. The fixed public vendor sources establish the matching consumer ABI.

## Source and initialization boundary

The complete [canonical contract](rpm-stats-sm8250-reference-contract.json),
including its trailing LF, has SHA-256:

```text
9aef5bfdf8c6c7ccbd01ef666f8c0f4cd9c4083365a06d27ca1d4bf979aa8337
```

The contract identifies the current-slot AOP image by SHA-256
`3dbb0746d21100d124d76ee19b096d122d58c4aa534dec5828005835535b6671`.
Retained private static analysis identifies the initialization chain, the full
32-bit directory offset, three 32-byte clears, and the subsequent three tag
stores. Original firmware bytes and private disassembly are not distributed.
The public generator verifies six complete pinned public source files and the
consumer ABI; it does not claim to reproduce the private firmware analysis.

The actual kernel configuration has `CONFIG_MSM_RPM_SMD` disabled. There are
exactly three 32-byte records and no appended SMD data or extra third-record
padding. The fixed ARM64 `memcpy_fromio` implementation uses 64-bit accesses
for naturally aligned eight-byte fields, so a read32-only model is insufficient.
These source identities and distinctions are recorded in the canonical
contract and generated [source evidence](rpm-stats-sm8250-reference-evidence.json).

The identified firmware also has later entry/exit update functions. This model
implements the earlier initialized snapshot only. Zero counts and timestamps
are the proven initial values, not a claim that the physical device has never
slept. Time progression, guest writes and QOM observations do not update them.

## Exact MMIO ownership

| Guest physical range | Accepted scalar reads | Contents |
| --- | --- | --- |
| `0x0c3f0004`–`0x0c3f0007` | 4 bytes at `0x0c3f0004` | Unsigned directory offset `0x000f0048` |
| `0x0c3f0048`–`0x0c3f00a7` | Naturally aligned 4- or 8-byte accesses that fit the record leaf | Three initialized records |

The offset is relative to the vendor resource base: `0x0c300000 + 0x000f0048`
equals `0x0c3f0048`. It is not truncated to a page offset.

The device owns two I/O regions totaling 100 bytes. It does not map the holes
between them or extend the separate 4 KiB APSS QMP page at `0x0c300000`.
The DDR directory word at `0x0c3f001c`, later DDR data, and other global
message-RAM entries remain outside this contract.

All writes are rejected with `MEMTX_ERROR`. Reads are rejected when the profile
is disabled, the scalar width is unsupported, the address is not naturally
aligned, or the scalar access does not fit its leaf. The last valid record
read32 is at `0x0c3f00a4`; the last valid read64 is at `0x0c3f00a0`.

This is a scalar device-access contract. QTest's raw `read`/`write` commands
and address-space bulk buffers can be split into smaller accesses. The model
does not promise whole-buffer atomicity or reject a legal sequence merely
because its total buffer length exceeds eight bytes. QTest's `OK` response
alone is not evidence that an MMIO access succeeded. Ordinary rejection tests
check the specific device or unassigned-region diagnostics and unchanged data.

Each little-endian record has this layout:

| Offset | Width | Field | Initial value |
| --- | --- | --- | --- |
| `0x00` | 4 | `stat_type` | Record tag |
| `0x04` | 4 | `count` | 0 |
| `0x08` | 8 | `last_entered_at` | 0 |
| `0x10` | 8 | `last_exited_at` | 0 |
| `0x18` | 8 | `accumulated` | 0 |

| Record | Physical base | Tag | Tag u32 |
| --- | --- | --- | --- |
| 0 | `0x0c3f0048` | `aosd` | `0x64736f61` |
| 1 | `0x0c3f0068` | `cxsd` | `0x64737863` |
| 2 | `0x0c3f0088` | `ddr ` | `0x20726464` |

The fourth byte of `ddr ` is an ASCII space, not NUL. Reset retains the same
initialized reference; the disabled profile continues rejecting accesses.
There is no mutable register bank, generic RAM backing, READY state, event
producer, IRQ, timer, clock, or counter increment mechanism.

## Identity and migration scope

Read-only QOM properties expose the two physical ranges, record geometry,
reference profile and ABI, full contract SHA-256, and the unsupported behavior
flags. The identity is an emulation reference interface, not a hardware version.

VMState version 1 has exactly 40 declared payload bytes:

| Field | Wire bytes |
| --- | --- |
| Enabled reference profile, u32 | 4 |
| Reference ABI version 1, u32 | 4 |
| Complete canonical-contract SHA-256 | 32 |

The numeric fields use normal VMState u32 serialization. The digest is a
32-byte buffer. `post_load` compares the profile with the target's explicit
configuration, checks the reference ABI, and compares every digest byte with
the compiled contract. Immutable record contents are reconstructed from that
contract; no evolving statistics are transferred.

The current acceptance suite does not create, load, alter or inject migration
streams. Its `migration_tested`, `migration_profile_mismatch_tested`, and
`migration_wire_payload_verified` fields are explicitly false. Guard review is
static. A later, separately authorized positive migration test may preserve an
unmodified enabled-to-enabled or disabled-to-disabled stream and inspect its
40-byte identity payload. No such runtime result is claimed here.

## Ordinary acceptance coverage

`test_rpm_stats_sm8250.py` defines 15 checks for the default disabled profile,
pure QOM identity, complete read32/read64 snapshots, all record fields and tags,
legal end addresses, rejected writes, unsupported scalar widths and alignment,
the exact two mapped leaves and surrounding holes, stable observations through
time changes, reset in both profiles, and paused-TCG identity without executing
guest CPUs. It retains actual QTest/QMP commands, process diagnostics, readback
values and cleanup records.

The suite binds the complete canonical contract and records hashes of the model,
public/generated headers and test. A passing ordinary suite establishes only
the behavior it observes. It does not establish live AOP statistics, production
kernel probe success, migration acceptance, or Android boot completion.
