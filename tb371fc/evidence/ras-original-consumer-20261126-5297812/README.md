# Original RAS consumer: bounded static evidence

This package records the original kernel code behind the observed RAS frontier
at QEMU source `5297812e6af5f5a6d88372debb12c5841142757c`. It contains semantic
facts and complete-input hashes. It does not contain kernel instruction bytes,
private logs or host file paths. The companion
[native evidence](../npu-pending-native-20261126-5297812/README.md) records the
actual failed native execution; this package records the later static analysis.

## Complete scope

Nine exact functions were collected from the pinned original kernel. Every
function's complete same-name text-symbol candidate set contained one entry.
The whole kernel and symbols were checked twice with stable file identity for
each collection. All **1,784 bytes / 446 four-byte instruction slots** were
decoded, including padding; all **45 direct BL sites** were checked against the
original symbol metadata. Those counts describe code coverage, not execution.

| Function | Start address | Bytes | Decoded slots | Direct BL sites |
|---|---|---:|---:|---:|
| `kryo_cpu_erp_probe` | `0xffffff8008c32238` | 904 | 226 | 26 |
| `kryo_pmu_cpu_pm_notify` | `0xffffff8008c32608` | 104 | 26 | 2 |
| `kryo_cpu_panic_notify` | `0xffffff8008c32670` | 64 | 16 | 2 |
| `kryo_l1_l2_handler` | `0xffffff8008c326b0` | 40 | 10 | 1 |
| `kryo_l3_scu_handler` | `0xffffff8008c326d8` | 32 | 8 | 1 |
| `initialize_registers` | `0xffffff8008c326f8` | 24 | 6 | 0 |
| `l1_l2_irq_enable` | `0xffffff8008c32710` | 32 | 8 | 1 |
| `kryo_check_l3_scu_error` | `0xffffff8008c31e78` | 224 | 56 | 6 |
| `kryo_check_l1_l2_ecc` | `0xffffff8008c31f58` | 360 | 90 | 6 |

The JSON supplies the complete SHA-256 for every function, its next distinct
text-symbol boundary, the full kernel and symbols hashes, and the observation
and analysis receipt hashes.

## Register and callback paths

The first observed fault is the unconditional selector-zero write at
`kryo_cpu_erp_probe+0x30`. Later code calls `initialize_registers` through
`smp_call_function_single`, using the possible-CPU mask and `nr_cpu_ids` symbols.
Their values were not read. The helper return is not checked by this probe.
The callback writes control `0x10f` and miscellaneous value `0x7f7f00000000`
without selecting a record. The probe later explicitly selects record 1 and
writes those same values locally. Its successful IRQ setup path also calls
`on_each_cpu(l1_l2_irq_enable, ..., 1)`.

The CPU-PM callback repeats the initialization for selectors 0 and 1 when its
numeric action equals 2, then calls the L3 checker and the L1/L2 checker. The
numeric action is not assigned an enum name here. Panic and interrupt callbacks
reach the same two checkers. These are static paths; the native run stopped at
the earlier selector write, so this is not evidence that they ran successfully.

## Zero status and conditional writeback

The **L3 checker** selects 1, reads both `ERXSTATUS_EL1` and `ERXMISC0_EL1`, then
tests status bit 30. A zero status branches to unlock and return. It performs no
diagnostic output, error dump or status write on that path.

The **L1/L2 checker** selects 0 and reads status and `TPIDR_EL1`. A zero status
falls through to unlock and return before any miscellaneous-register or MIDR
read, diagnostic output, dump or status write.

Zero miscellaneous data alone is a different condition. It also makes L3 skip
its handled-error path, but it does **not** make L1/L2 return early when status
bit 30 is set. The virtual zero-status value is the condition that closes both
clean paths.

On the conditional nonzero error paths, L3 writes the captured status in `x21`
back at `+0xd4`; L1/L2 writes the captured status in `x20` at `+0x160`. No fixed
all-ones acknowledgment is constructed. This describes local register dataflow
under the calling convention, not independently measured hardware behavior.

## Limits

The bodies of `dump_err_reg`, spinlock, diagnostic-output and panic helpers
were not collected or reviewed. The nonzero-miscellaneous jump table was not
collected; neither zero-status path needs it. No CPU-mask value, physical RAS
register, hardware error record, successful error injection or error interrupt
was observed. The evidence does not claim a generic QEMU architecture defect,
a compiled model pass, or successful Android startup.

See the [virtual policy](../../docs/ras-zero-records-reference.md) for the
separate opt-in model contract. New compiled and native execution remain
separate validation steps.
