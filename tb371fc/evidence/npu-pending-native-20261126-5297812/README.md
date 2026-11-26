# Actual NPU pending-profile diagnostic — source 5297812

The single original-input native attempt reached the NPU driver's timeout and
error-return path, then failed in a later CPU RAS probe. **Native failed**:
runner and host exit 2, QEMU exit 0 after normal QMP shutdown. The independent
local record review passed 2,249 checks; that verifies the recorded attempt
and its evidence, not Android boot success.

## What was observed

The [complete safe independent review](independent-public-native-review.json)
and the [additional exact frontier facts](actual-frontier.json) identify source
`5297812e6af5f5a6d88372debb12c5841142757c`, tree
`81995ad7ea3904a57396f0b334a7eccd7744c8b8`, push run `45733250705`, and artifact
`15078443518`. The artifact has been consumed once and must not be rerun.

At guest 5.334112 s, the original driver logged `npu_core_gdsc enable timed out:
0x0`. It subsequently reported a final zero status, failed to enable NPU power,
dropped its regulator links, and returned probe error `-110` at 5.335631 s.
Execution then continued to the later fault below. This positive sequence
establishes that the software timeout/error path was reached. It does not
establish NPU power, a known physical parent supply, a successful NPU probe,
or NPU execution. The driver's printed “100 us after timeout” is retained as
a log observation, not an independently measured timing result.

The next fatal sequence began with QEMU rejecting a write to system register
`S3_0_C5_C3_1` (`ERRSELR_EL1`). QEMU recorded an Undefined Instruction exception
on CPU 4, ESR `0x02000000`, ELR `0xffffff8008c32268`. A complete same-original-symbol
metadata observation maps that address uniquely to `kryo_cpu_erp_probe+0x30`
in its 904-byte function. Printk reports undefined instruction at 5.470610 s.
A later Data Abort occurred in `die+0x268`, followed by the 5.473609 s panic.
The secondary handler fault is not the initiating unsupported register write.

The frozen independent review's existing first-fatal pattern selected that
later panic. Its original receipt is preserved byte-for-byte. The additional
frontier facts explicitly retain the earlier undefined instruction and the
complete-log ordering; no historical receipt was rewritten.

## Execution and evidence limits

This was a **0-LUN diagnostic**. Storage readiness was absent at input selection;
Android userspace, completed Android boot, and interactive UI were not observed.
The original four core inputs, AOP and symbols were reported rehashed unchanged
by the existing runner/coordinator. The local reviewer did not independently
reread those remote inputs. No original kernel or init payload was replaced.

The pure preflight performed 150 QMP requests, including the retained 140 QCE
getters and four NPU profile getters. It observed profile 2 and the explicit
pending flag twice, with no guest execution or device MMIO in that preflight.
Those configuration observations are separate from the later native timeout.

The complete local collection contains 49 full files (2,677,674 bytes), two
inventory-only records, and two explicitly missing optional six-LUN route
files. The public review retains their complete SHA-256 identities and the
actual cleanup limits. Earlier warnings and an earlier recoverable Data Abort
remain; this result does not claim that all prior warnings or paths succeeded.
Raw native logs, private input contents, command-line payloads and instruction
bytes are not included here. The RAS issue remains a new, unqualified frontier.
