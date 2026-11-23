# DCC canonical WRITE, LOOP and single-register RMW

The [16 new operation checks](../evidence/qsee-dcc-20261123/dcc-operations/report.json)
and [17 original checks](../evidence/qsee-dcc-20261123/dcc-existing/report.json)
passed on frozen binary SHA256
51fe61d35213a6602a8eaa865faba04bc85f88df341947ca8a2e6300a8ab5be6.
The original production three-list capture remains unverified.

## Primary inputs

- Fixed vendor driver:
  https://android.googlesource.com/kernel/msm.git/+/8e58a0d71f888104533e379c22c36a58841673c1/drivers/soc/qcom/dcc_v2.c
  Functions __dcc_ll_cfg, dcc_add_write, dcc_add_loop, dcc_rd_mod_wr_add.
- Qualcomm's later patch, the same emitted formats:
  https://lists.openwall.net/linux-kernel/2023/08/08/642
  Functions dcc_emit_write, dcc_emit_loop, dcc_emit_read_write.
- Author's ABI description (capture old value, then write):
  https://lkml.indiana.edu/hypermail/linux/kernel/2103.3/05534.html
- Author's confirmation that RMW affects only the mask-selected bits:
  https://lkml.indiana.edu/hypermail/linux/kernel/2112.1/09123.html
- Published Linux ABI:
  https://www.kernel.org/doc/Documentation/ABI/testing/debugfs-driver-dcc

WRITE has ADDRESS bit28, one-word LINK and a literal payload. LOOP uses
opcode bit30, word-count back distance from the LOOP word itself, and
(iterations - 1) at bit16 for the current reference HW_INFO. The driver's
entry limit is 255 total iterations. RMW is opcode bit31 followed by mask
and value words.

## Reference execution contract

The decoder builds and validates an ordered operation plan before any bus
transaction. It consumes literal/mask words as payload, never opcodes.
At the existing virtual deadline, each READ performs a real address-space
read and appends a captured word; each WRITE performs a real address-space
write. Repetition duplicates operations, never previously read data.

For RMW, the directly preceding LINK must contain exactly one actual READ.
The old value is that already captured word. There is no second source
read; the write is (old & ~mask) | (value & mask). Reusing that word is an
explicit reference interpretation because the public driver/ABI does not
specify a second read or intra-operation bus cycles. SRAM capture contains
the old word only; an explicit following READ captures the new value.

Only flat loops are implemented. Their target must be an already decoded
ADDRESS boundary in the same program, with a nonempty operation body.
A loop containing another LOOP is rejected; sequential loops are allowed.
READ captures must fit SRAM and must not overlap descriptors.
The expanded operation bound is 6144 * 255 operations, derived from this
SRAM aperture and the supported driver count, not asserted as a silicon
execution limit. A callback executes the bounded plan at one synthetic
deadline; per-operation timing and mid-callback migration are not modeled.

After full format/capacity validation, every non-READ target is checked before
the first bus operation. A self-modifying or split write anywhere in the
program therefore prevents earlier otherwise valid writes too. The check is
repeated at execution time for mappings that changed in the meantime.
Self-modifying writes into this DCC's own register/SRAM regions are rejected.
Actual target transaction failures preserve earlier real effects and retain
the existing busy+diagnostic unsupported/error behavior. No silicon error
bit taxonomy, completion IRQ, CRC, ATB, CTI or physical revision is invented.

The hidden migration geometry guard changes from 0x00020800 to 0x00020801
to reject transfers between old read-only and new operation interpreters.
Pending programs still migrate as SRAM/registers/absolute deadline, then
decode and execute on the destination. The test covers an actual transfer
with 600 ns remaining and verifies no execution before that deadline.

## Source ambiguity that is not silently repaired

Both the pinned vendor emitter and the later Qualcomm patch flush a pending
read LINK before a WRITE but leave link nonzero before using |= to assemble
the new write LINK. Some sequences can therefore contain a noncanonical
multiword write encoding. The model consumes actual SRAM words and rejects
the uncertain format; it does not reinterpret guest data to recover presumed
sysfs intent. A malformed descriptor is not evidence of hardware semantics.

General multiword WRITE, RMW following multiword READ, nested LOOP, count
fields beyond the supported driver's range, CRC and ATB remain unsupported.
Reference identity and bus timing remain distinct from observed TB371FC data.

## Tests

The new QTest runner has 16 independent groups, including literal words that
look like opcodes, ordering, mask corner cases, 1 and 255 loop iterations,
bad loop targets, truncation with no partial effects, overflow, self-write
rejection (including a valid WRITE followed by an unsupported self-WRITE),
and one real migration. The ordered loop starts with RAM=0 and
per iteration READs it then WRITEs 1: captured [0,1,1] proves reads occur on
every iteration. RMW READ -> RMW -> READ verifies SRAM [old,new] and the real
RAM update. This RAM success test does not independently count MMIO reads;
the no-second-read rule is explicit in the executor.

Run with a matching compiled QEMU:
  python3 tb371fc/tests/test_dcc_ops.py --qemu QEMU --out FRESH_DIRECTORY

The existing 17-case DCC suite must also pass unchanged. Its unsupported
WRITE/LOOP/RMW vectors are malformed or missing operands, so this extension
does not require weakening those historical negative checks.
