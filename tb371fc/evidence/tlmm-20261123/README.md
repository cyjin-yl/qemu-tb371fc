# TLMM acceptance — frozen 7

QEMU SHA256: f3fa0d2db54d9f59ba9b0ea25239553900b805c3c05e53e0dd8577e94607e41b.

Actual results on 2026-11-23: **25/25 C QTests**, **15/15 migration checks** over four real process migrations, and **9/9 map-parser tests**. The generated map was checked against pinned Linux source. Full per-check output, binary/source hashes, UTC and commands are in the adjacent result files.

Frozen6 failed before the first feature assertion: a board-owned IMEM region used an invalid DeviceState owner. Original failed results remain separately recorded; host object pointers are the only redacted values. No failed test was relabeled as a pass.

The vendor crosscheck independently compares all 180 GPIO offsets with the fixed public kernel-source mirror. Dedicated UFS reset has different logical group indices (vendor 183, upstream 180) but identical physical control/IO addresses. EGPIO bits, native wake registers and I3C mode fields are outside the tested TLMM subset.

These checks establish the recorded functional GPIO/IRQ/reset/migration behavior. They do not establish electrical timing, PDC wake routing, touch/pen operation, complete Android or whole-tablet equivalence. See [TLMM implementation notes](../../docs/tlmm.md).
