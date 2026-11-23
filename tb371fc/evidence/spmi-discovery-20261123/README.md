# SPMI observation boundary, 23 November 2026

[Actual export discovery](kernel-export-capabilities.json) was collected on the
tablet at 08:15:17 UTC. The controller is bound to spmi_pmic_arb. No named
version, APID or ownership attributes were found in the three selected
controller/bus directories. This does not show those hardware features absent.

The production configuration has BPF_EVENTS=y but KPROBES=n and DEBUG_FS=n.
The selected kprobe interface and BTF file are absent. No trace program was
attached, no security setting changed, and no arbitrary kernel memory or MMIO
was accessed. Kernel config availability does not establish every possible
observability route; it rules out the requested kprobe route in this kernel.

The same read-only collection records firmware-reported socinfo fields:
QRD, SKUF, subtype ID 2, platform version 65536. Mapping them to an AOP internal
structure requires separate source evidence.

[DT root matching cells](dt-root-matching-metadata.json) were extracted from the
already private live DTB using a four-property allowlist. These are software
matching metadata, not an independently read PMIC identity or arbiter table.

The exact [collector](../../scripts/collect_spmi_exports_20261123.py) is retained
with its native SHA-256. [Publication review](publication-review.json) records
scope and normalization. Native source/results remain in the task directory.
Unknown native VERSION/APID/ownership values must not silently use a reference
profile. Missing PMIC responses must not be represented as successful reads.
