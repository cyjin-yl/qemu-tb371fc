# Numeric boot layout captured on 2026-11-23

These receipts contain numeric hardware and boot-layout evidence. No bootargs,
seeds, serial numbers, firmware payloads or user data are included.

- `native-iomem.json` records a root, read-only collection of only System RAM,
  Kernel code and Kernel data lines from `/proc/iomem`, captured at
  06:39:32.381757 UTC. The exact executable argv and the stdin operation are
  recorded; the original inline collector was not saved as a separate file.
- `boot-layout.json` joins that capture with only the two chosen initrd-address
  properties and static reserved-memory/FDT reservation ranges from the existing
  private live DT. It also reads the numeric AArch64 header from the already
  backed-up current active-partition Image. Source hashes and the analyzer hash
  are recorded.
- `aop-backup-provenance.json` records the bounded, read-only backup of the
  current active `aop_a` firmware partition. Only provenance/hashes are public;
  the payload remains a private development asset. This is a current partition
  backup, not a certification that the image is pristine factory firmware.

Observed Kernel code starts at `0xa0080000`; subtracting the Image's
`0x80000` text offset derives a loader base of `0xa0000000`. The Image's
memory footprint ends at `0xa2b36000` (exclusive), consistent with the observed
Kernel data end. The native initrd interval is
`[0xafec2000, 0xafffef93)`. Neither interval overlaps a statically addressed
reserved-memory or FDT memreserve entry in this capture.

The command-db region `[0x80860000, 0x80880000)` is explicitly reserved with
`no-map`. Moving the default direct-kernel loader away from that region follows
the observed layout; it does not require changing the board's real RAM base.
These facts do not imply that QEMU's eventual initrd/DTB placement is safe for
every input. Check their actual generated intervals separately, including
dynamic reservations for which this DT provides no fixed `reg`.

Reproduce inside the development container, choosing a new output path so that
the existing receipt remains immutable:

```sh
python3 /src/tb371fc/scripts/analyze_boot_layout.py \
  --dtb /work/device-assets/live-android-20261123.dtb \
  --image /work/device-assets/current-boot_a-kernel.Image \
  --native-memory /work/device-assets/native-memory-command-db-availability.json \
  --output /work/device-assets/boot-layout-repeat.json
```
