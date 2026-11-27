# EL2 physical timer routing

The TB371FC machine defaults to `hyp-timer-ppi=12` (GIC INTID 28). This
preserves the stock Android DT contract and the existing machine-profile
validation. A Linux candidate DT which declares HYP physical PPI 10 can use
the explicit option:

```text
-machine tb371fc,hyp-timer-ppi=10
```

Only integer values 10 and 12 are accepted. The selected value drives both
the `GTIMER_HYP` connection on every CPU and the fourth interrupt tuple in
the generated `/timer` node. It is immutable after machine creation. The
secure physical, nonsecure physical and virtual timer PPIs remain 13, 14
and 11 respectively. The option is independent of `rpmh-profile`, counter
frequency mode and other firmware reference choices.

An externally supplied DTB is not rewritten by this option. Its fourth
`arm,armv8-timer` interrupt must agree with the selected PPI. The launcher
must bind the command line, supplied DTB and paused QOM readback before
continuing the guest. Merely changing an outer runner profile does not
change the timer wire.

## Evidence and meaning

The stock passive inventory at
`tb371fc/evidence/expansion-20261123/android-device-inventory.json` records
`/soc/timer` interrupt tuples:

| Timer | DT tuple | GIC INTID |
| --- | --- | --- |
| Secure physical | `1 13 0xff08` | 29 |
| Nonsecure physical | `1 14 0xff08` | 30 |
| Virtual | `1 11 0xff08` | 27 |
| HYP physical | `1 12 0xff08` | 28 |

At source revision `6287cd9a41e5114de6cf3a4ac38b0fd85872ee06`, that inventory
is Git blob `bd461177089896e89411fd9453074e4887b0e6a5`. It was derived from
the owner's running-device `/sys/firmware/fdt`, 973052 bytes, SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
The 48-byte interrupt property has SHA-256
`bf9ac0fd504ad3785fd1532d5f0d666bf38a85376bd671d488a6338ee21521bf`.

The Linux port candidate instead supplies `1 10 0xff08` in the fourth
tuple: input DTB 126893 bytes, SHA-256
`7092d992582a2f49b65e8eb1a4209fede3b41ccbbacb3abc28d29b649b33423c`.
This difference is the reason for the explicit compatibility option. PPI
10 is not asserted to be the physical tablet's measured CNTHP wiring, and
the option does not replace the stock DT contract.

The modeled Cortex-A55/A77 CPUs implement EL2 and VHE. Linux running with
VHE selects the HYP physical interrupt from DT and its CNTP accesses are
redirected to the CNTHP timer bank. A DT PPI 10 with a model wire on PPI 12
therefore describes a different interrupt from the one the model raises.
The new option selects a coherent model for that Linux DT without changing
the CPU feature set or the guest DT bytes.

## Regression obligations

The existing `cpu-irq-realtime`, `cpu-irq-exact-icount` and
`cpu-irq-legacy-icount` gates keep their names and commands. Each invocation
of `test_cpu_timer_irq.py` now runs two fresh probes:

| Route | Machine choice | Expected IAR sequence |
| --- | --- | --- |
| `stock-default` | HYP option omitted | 30, 27, 28 |
| `linux-dt-ppi10` | `hyp-timer-ppi=10` | 30, 27, 26 |

The guest asserts `CurrentEL == 8` and `HCR_EL2.E2H == 0`, preserving the
existing CNTP and CNTV timer-bank checks while directly exercising CNTHP.
These are EL1 timer-bank checks executed at EL2; they do not claim an EL1
execution or VHE-alias execution test. For each bank, the probe checks
masked expiry, unmask, mask, disable, past compare and future compare at
the actual GIC redistributor input, before any CTL read can refresh state.
Both alternative HYP lines are observed so a second or misplaced wire
also fails.

Each timer additionally raises a real Group 1 interrupt. The probe reads
`ICC_IAR1_EL1`, checks the actual INTID and active bit, deasserts the timer,
drops priority with `ICC_EOIR1_EL1`, verifies that EOImode 1 leaves it active,
then deactivates it with `ICC_DIR_EL1`. It requires the active state to clear
and the next IAR to be spurious. IRQ exceptions remain masked; no interrupt
exception handler is claimed.

Before the guest runs, the host checks paused QOM readback, rejects a
post-creation routing change, dumps the generated DTB and reads its timer
interrupt cells using `fdtget`. The expected tuples and IAR values are
independent test contracts; they are not read from board C constants.
Separate QEMU invocations must reject `0`, `9`, `11`, `13`, `14`, `15`,
`16`, `4294967295`, `-1`, `10.5` and `ten`.

The report schema is version 2. It retains the top-level `passed` verdict,
and adds exactly two `routes`, eleven `invalid_values`, the route contract
`stock12-linux10`, actual IAR sequences, DT hashes and cells, QOM values,
source/helper hashes, ELF hashes, commands, logs and process-reaping
results. `passed` requires every route and invalid-value obligation.
Source qualification must require these fields and bind the probe source,
`start.S`, `link.ld` and generated ELF identities, as well as the QEMU
source and binary. A schema 1 success cannot satisfy the new obligation.

## Scope

The explicit PPI 10 mode is for a fresh, non-migrated Linux boot. This
change does not add a machine-level migration compatibility record. There
is no claim of migration support for PPI 10 or for mixed-route endpoints;
do not migrate between different `hyp-timer-ppi` values. The existing
default-PPI-12 timer migration checks remain required. A later migration
feature must explicitly reject mismatched routing on both endpoints and
validate that behavior before claiming support.

These component checks do not establish Linux boot completion, userspace
acceptance, native driver support or physical-device timer behavior. A
fresh board observation with the qualified new binary must supply those
separate results.
