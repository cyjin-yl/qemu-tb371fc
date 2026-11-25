# Verified capture storage retirement — 2026-11-25

The two TB371FC capture trees were relocated from the host SSD to `tank`, with
full source/destination SHA-256 verification and the original paths preserved.
The final old-payload retirement ran once on **2026-11-25, 10:07:26–10:07:42 UTC**
(18:07 Beijing time) and completed successfully.

## Recorded result

| Check | Observed result |
| --- | --- |
| Old files retired | 3,321; 120,197,133,237 logical bytes |
| Empty directories removed | 1,571 |
| Old anchor retained | 92 directories and all 90 original capture-lock files |
| Original native lock | device 29, inode 6066218, link count 1, length 0 |
| Lock-domain checks | 540 independent negative probes passed |
| Original lock alias checks after completion | 270 exact stat checks across the three aliases |
| Target payload | Unmodified; complete target metadata unchanged |
| Execution and release | Original heavy gate admitted; launcher exit 0; all 90 capture FDs and native FD closed |

The original SSD anchor now contains **only the lock files and their directory
skeleton**. It is no longer a payload rollback copy. The previously verified
`tank` copy is authoritative and remains visible through the original canonical
paths. Any future return to SSD requires a separately verified copy-back.

## Capacity and verification

At **10:08:24.793716 UTC**, the independent read-only follow-up observed
**141,808,500,736 bytes (132.07 GiB)** free on the root filesystem, compared with
21,544,042,496 bytes before retirement. The measured net recovery was
**120,264,458,240 bytes (112.01 GiB)**. This is the settled observation; the
controller's immediate completion read happened before all free-space accounting
had settled. Other host activity can also affect net available capacity.

The controller retained the existing copy and cutover evidence, acquired the
original native and capture locks, and checked exact source/target metadata,
mount topology, process references and snapshot state before its first unlink.
Its process-map reader consumed complete streams to EOF using bounded line
memory; it inspected 1,175 processes and completed 1,171 mapping streams.

The follow-up read the complete small launcher, result, release and skeleton
receipts and checked their SHA-256 links. All 90 capture locks retained their
original inode identity and single link through the anchor, `tank` and canonical
aliases. Both canonical roots remained backed by `tank` ZFS, and the persistent
mount-restoration service remained enabled and active. This follow-up did not
re-run the controller, rehash payload files or rescan payload directories.

The installed restoration service was also inspected after retirement. Its cold
path needs the surviving original lock anchors, native lock and stable directory
identities; it does not require the removed payload or subdirectories. Directory
mtime, ctime, size and link count are deliberately excluded from its root identity
checks. This is static compatibility evidence: **the cold-boot path has not been
executed**, and no service restart, unmount or host reboot was performed after
retirement while other work continued.

## Evidence and scope

[manifest.json](manifest.json) records exact controller, launcher, binding,
result, release and skeleton sizes and SHA-256 identities, alongside the measured
counts and timestamps. The copy proof covered 3,411 files (including 90 empty
locks) and 120,197,133,237 bytes on each side. Before this successful invocation,
the preceding attempt had stopped in preflight with zero deletions; the new
launcher verified that immutable failure/release record before proceeding.

This directory publishes **two metadata files only**. QEMU sources, tests,
workflows and firmware inputs are unchanged. Firmware, userdata, capture chunks,
credentials and full private control receipts are not published here; the
manifest identifies the private execution evidence and its observed results.

This operation establishes storage relocation and space recovery. It includes
**no new QEMU or Android execution**, and makes no claim of complete six-LUN
assembly, Android userspace, `boot_completed` or an interactive Android UI.
