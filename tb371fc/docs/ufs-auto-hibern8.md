# SM8250 UFS automatic Hibern8

The Qualcomm wrapper now implements the UFSHCI automatic idle path alongside
its existing explicit UIC Hibern8 commands. This is a digital driver contract:
it does not model M-PHY analog exit latency, NAND timing, or whole-board sleep.

## Register and state contract

`CAP.AUTOH8` (bit 23) is advertised when the `auto-hibern8` device property is
enabled, which is the new default. `AHIT` at HCI offset `0x18` resets to zero.
Its timer occupies bits 9:0, its scale occupies bits 12:10, and the interval is
`timer × 10^scale` microseconds. A zero timer disables automatic entry. Scales
0–5 are implemented; reserved bits or scales 6–7 produce a diagnosed
transaction error without changing the register.

The timer runs only with an active link, HCE enabled, all required host and
lane clocks, a ready PHY, no UIC command and no pending transfer or task
request. Request slots in the READY state also count as busy, before the
transport bottom half sets the doorbell. The transport's completion callback
starts a fresh interval even when its interrupt is masked. Register reads and
interrupt acknowledgements do not restart an already running idle interval.

At expiry the link enters Hibern8 and the existing WriteBooster Hibern8 policy
can observe that state. A new UTP request or ordinary UIC command automatically
exits a sleep entered by AHIT, provided the host and PHY are running again.
Automatic exit is atomic in this functional model; no measured exit latency
is claimed. Disabling AHIT stops further entries without making an already
automatic sleeping link require an explicit exit command.

Successful automatic transitions do not set UIC command-completion,
Hibern8-enter or Hibern8-exit interrupt bits. Linux treats Hibern8 status bits
outside an explicit HIBER_ENTER/HIBER_EXIT command as an automatic-Hibern8
error, so synthetic success interrupts would break the real driver.

Explicit HIBER_ENTER takes ownership of a current automatic sleep. That
explicit link remains asleep until HIBER_EXIT; a data doorbell cannot silently
wake it. Both kinds of Hibern8 retain the link across PHY shutdown. Powering
down an active link still drops it. Disabling a required host clock cancels
an active idle timer; enabling the clock starts a full interval once the link
is eligible again. A device-reset input now cancels any pending UIC command,
so a stale completion cannot restore a reset link.

The read-only QOM `link-state` property reports `down`, `active` or `hibern8`
for diagnostics. It is not a guest register or a force-ready control.

## Checkpoints and compatibility

The existing v1 main wire layout is retained. A versioned
`qcom-sm8250-ufs/auto-hibern8` subsection carries automatic-sleep ownership and
the virtual idle-timer deadline. The receiver validates that an automatic
sleep has a Hibern8 link and that a pending timer agrees with the complete
clock, PHY, UIC and request state. Invalid state is rejected before resuming.

The capability register remains equality checked. Old v1 streams with
`CAP.AUTOH8=0` require the receiver profile
`-global qcom-sm8250-ufs.auto-hibern8=off`; enabling the new capability does not
silently reinterpret an old profile. The old profile does not emit the new
subsection and retains explicit-only Hibern8 behavior. This describes the
wire compatibility mechanism, not a claim that every older binary pairing
has been tested.

Storage queues must still be quiescent, and source and receiver still reuse
the same external medium. This feature does not add in-flight SCSI migration
or copy storage contents into the migration stream.

## Runtime gate

```sh
python3 tb371fc/tests/test_ufs_autohibern8.py \
  --qemu /absolute/path/qemu-system-aarch64 \
  --out /absolute/path/new-result-directory
```

The gate uses nonidentity SMMUv2 translations for real UTRD, UPIU and payload
DMA. It writes and flushes a known block pattern before checking repeated
automatic sleep/read/wake cycles, exact idle deadlines, masked completion,
UIC exclusion, explicit sleep ownership, clock gating, reset recovery and
PHY shutdown. Two actual QMP source-to-new-process transfers test a partially
elapsed idle timer and an automatic sleep with PHY/core clock disabled.
Three rejected receivers cover inconsistent automatic-sleep state, a removed
pending timer and a different capability profile; each must exit without a
RESUME event or media modification. QMP is drained to EOF for that check.

Runtime results belong to the invoked binary and report hashes. This document
does not by itself assert that those tests have passed.

## Sources

- [Linux v6.12 UFSHCI definitions](https://github.com/torvalds/linux/blob/v6.12/include/ufs/ufshci.h):
  `MASK_AUTO_HIBERN8_SUPPORT`, AHIT timer/scale masks, scale factor and maximum.
- [Linux v6.12 UFS sysfs implementation](https://github.com/torvalds/linux/blob/v6.12/drivers/ufs/core/ufs-sysfs.c):
  conversion between microseconds and AHIT; rejection beyond the supported maximum.
- [Linux v6.12 UFS core](https://github.com/torvalds/linux/blob/v6.12/drivers/ufs/core/ufshcd.c):
  `ufshcd_configure_auto_hibern8()`, `ufshcd_auto_hibern8_update()` and
  `ufshcd_is_auto_hibern8_error()` establish the software-visible programming
  and interrupt contract.

The previous [explicit UFS/PHY contract](ufs-sm8250.md) and
[quiescent checkpoint boundary](ufs-migration.md) continue to apply.
