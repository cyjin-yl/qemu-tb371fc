# SM8250 TBU contract review

## Sources and evidence scope

The [public evidence JSON](../evidence/sm8250-contract-20261124/hardware-facts.json)
contains allowlisted live TB371FC
device-tree bytes, safe UFS sysfs samples and exact upstream/vendor source
revisions. No native MMIO or DMA transaction tracing was performed. The official
OnePlus SM8250 source is a reference driver, not asserted to match Lenovo's
running kernel binary.

The native UFS node has no `iommus` property. SID `0xe0` and `0x4e0` come from
Linux v6.12.114 `sm8250.dtsi`. The native TBU ranges put these IDs in ANOC1 and
ANOC2 respectively if that upstream assignment is applied. That relationship is
an inference from two sources, not a native transaction observation.

## What the reference driver accesses

The vendor TBU probe maps its `base` and `status-reg` resources, reads
`qcom,stream-id-range`, requests optional debug interrupts and initializes the
declared power resources. No standalone TBU clock-enable register is programmed
by this probe. Live ANOC1, ANOC2 and apps-SMMU nodes have no `clocks` or
`clock-names` properties; apps-SMMU has `qcom,skip-init`.

The vendor hard-translation/debug operation uses the following registers.

| Register | Offset | Driver contract |
| --- | --- | --- |
| TCU version | TCU base + `0x18` | Distinguishes TCU 1.0 status-register access from later versions; native value unknown. |
| SID/halt | TBU base + `0x00` | Halt request bit 16; low 10 bits select the stream suffix for ECATS. |
| Virtual address | `+0x08` | 64-bit access; driver limits IOVA to 36 bits. |
| Translation trigger | `+0x18` | Trigger bit 0, direction bit 1, AXCACHE from bit 2, AXPROT from bit 6. |
| Halt/running status | `+0x20` | HALT_ACK bit 1, ECATS_RUNNING bit 0. |
| Translation result | `+0x28` | 64-bit PAR: fault bit 0; driver extracts `(PAR >> 12) & ((1 << 36) - 1)`. |
| AXUSER | `+0x30` | CDMID field at bit 36; driver writes 255. |
| ECATS lock/status | Separate status-reg resource | Driver polls read value 1, then writes 0 to unlock; omitted for reference TCU 1.0. |

Before a hard translation the driver powers the TBU, requests halt, handles
stalled context faults, disables new context fault IRQs, takes the ECATS lock,
programs SID/VA/AXUSER, triggers the query, polls status, checks PAR/context FSR,
and restores the context/TBU state. It retries failed translations for a
documented hardware quirk. This is substantially more than a clock stub.

## Reasonable implementation boundary

The opt-in [reference profile](tbu-reference-ecats.md) implements halt/ack and
ECATS against the existing SMMUv2 stream lookup/page-table walker. It must preserve the stream
range and reject unsupported attributes, address widths and query types. It
must propagate translation faults instead of manufacturing a physical address.
If no native clock input exists in the selected profile, it should explicitly
say so; an invented successful clock register would hide a missing dependency.

Before advertising native QSMMUv500 compatibility, tests should cover both
ANOC ranges, wrong-range selection, a nonidentity page-table translation,
permission/page-table faults, halt-before-query ordering, reset, pending-query
migration, cross-process restore and configuration mismatch. The generic
`arm,smmu-v2` identity remains appropriate for the current architectural subset.

## ICE scope reminder

An immutable ICE window can report an explicit unsupported sentinel and avoid
an unassigned VERSION read. It does not provide SCM/QTEE, keys or encrypted I/O.
The Linux v6.12.114 Qualcomm UFS variant obtains ICE without first checking HCI
CAP.CS; adding an ICE consumer resource can therefore trigger SCM probe deferral.
Keeping the generated functional DT free of an unsupported ICE consumer is
consistent with a no-crypto functional machine. Native-DT support remains a
separate acceptance gate.

VERSION zero is rejected by mainline's major-3/4 check but accepted by the
examined vendor driver's upper-bound-only version check. An explicit major-255
sentinel is rejected by both; it is not a sampled hardware revision.
