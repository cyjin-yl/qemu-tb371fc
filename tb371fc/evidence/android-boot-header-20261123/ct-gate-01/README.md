# Android boot-header reader: actual CT gate 01

All **nine synthetic fixture methods passed** in doesvm CT102,
Docker `qemu-tb371fc-dev`, at 2026-11-23 15:28:55 UTC.
The test process exited 0, was reaped, and its process group was absent
after cleanup. Both source hashes and the controller hash were unchanged.

The exact [result.json](result.json), [fixtures.stderr](fixtures.stderr),
[installation.json](installation.json), empty stdout and exit file are
preserved as original outputs. The installation receipt precedes execution:
its `tests_executed: false` records that earlier point, while the final
result and actual unittest output record nine completed tests.

The source and fixtures are published together under
[tools/android-boot-header](../../../tools/android-boot-header).
The reviewed controller fetched only the two fixed public source blobs,
checked Git blob identity, SHA-256 and byte count, compiled in the CT,
installed exclusive files, and then ran
`python3 -I -B -S test_android_boot_header.py`. Its own process-group
deadline was 60 seconds.

## Meaningful cases

The fixtures check exact boot v0-v4 geometry, vendor v3-v4 geometry
including a 2048-byte page with a header spanning two pages, v4 ramdisk
table count/stride bounds, and kernel-size-zero modern init_boot geometry.
They reject truncated or inconsistent headers and recovery offsets.

A dedicated raw-read witness catches buffered I/O reading beyond the
declared header; the implementation uses unbuffered reads and rereads only
the header for stability. A producer-compatible fixture preserves both
valid zero-sized recovery-DTBO offset forms. Private text fields and the
image ID are never returned.

These checks execute only self-authored byte fixtures. They do not read
private firmware, authenticate AVB, decompress a kernel, start QEMU or
establish Android boot. A later native-input observation must have its own
receipt and must not be folded into this synthetic result.
