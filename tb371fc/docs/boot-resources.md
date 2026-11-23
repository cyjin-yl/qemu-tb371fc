# IMEM SRAM and PS_HOLD

These components follow the captured TB371FC address map. They do not provide
a complete boot firmware or PMIC power model.

## IMEM

The captured `qcom,msm-imem` node describes `0x146bf000 + 0x1000`.
The board now maps this aperture as ordinary writable SRAM, with byte,
halfword, word and doubleword accesses. It begins zeroed when no firmware
contents are supplied. Guest writes survive a machine reset and are included
in ordinary QEMU RAM migration.

The native restart-reason field is at offset `0x65c`, and the kernel's KASLR
handoff area starts at `0x6d0`. These are guest/firmware-owned bytes, not
fixed read-success registers. The model does not invent a serial number,
secure log pointer, crash record or bootloader state in this memory.

The original production-kernel diagnostic reached `msm_restart_probe+0xec`
with an external abort at a virtual address ending in `0x6d0`. The source and
DT identify the corresponding IMEM handoff area. The frozen-7 diagnostic
with the same untouched production Image and full live DT passed this
previous abort and reached a new fault in `dcc_probe+0x114`. The
[allowlisted progression receipt](../evidence/native-dt-progression-20261123/progress.json)
records binary and private-log hashes. This is progress through native
initialization, not an Android or complete-DT boot verdict.

## PS_HOLD

The captured restart controller has a four-byte `pshold-base` at
`0x0c264000` and a separate `tcsr-boot-misc-detect` location at
`0x01fd3000`. The new `qcom-pshold` device implements only the former.

Bit 0 controls the named `ps-hold` digital output. The direct-kernel handoff
starts high, writes retain the chosen level, narrow/unaligned accesses are
invalid, and migration restores the output. It has no implicit PMIC reset
policy. The receiving PMIC/PON must decide between warm reset and power-off;
that policy is not inferred from a generic write of zero.

The generated minimal test DT does not advertise this incomplete power path,
so its established PSCI lifecycle tests continue to use their declared
interface. The unmodified native DT can probe the physical PS_HOLD register.
The TCSR register and secure SCM download-mode calls remain separate work.

Reference behavior is established by
[upstream msm-poweroff.c](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/power/reset/msm-poweroff.c?h=v6.12.114)
and the captured DT. The community Lenovo driver candidate at commit
`9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1` is corroborating source,
not proof of the installed kernel's complete source identity.

## Acceptance command

```sh
python3 tb371fc/tests/test_boot_resources.py \
  --qemu /work/build/qemu-system-aarch64 --out /work/boot-resources
```

The gate tests the SRAM boundaries, byte order, guest-owned handoff fields,
reset retention, digital pin output, invalid-width nonmutation and a real
two-process QMP migration. It does not test PMIC power behavior. Results
must be read from the recorded binary-specific receipt, not inferred from
the existence of this command.

## Recorded acceptance

The [frozen-7 receipt](../evidence/boot-resources-20261123/qtest-migration-7.json)
passes all seven groups, including actual two-process migration with SRAM
contents and a low PS_HOLD output preserved. The binary SHA-256 is
`f3fa0d2db54d9f59ba9b0ea25239553900b805c3c05e53e0dd8577e94607e41b`.

The earlier frozen-6 machine initialization failed before any guest checks:
the SRAM mistakenly used a MachineState as an automatic device RAM owner.
The GDB diagnosis and failed receipt remain in the same evidence directory.
Using globally registered RAM with a null owner fixed that initialization
error; the subsequent seven-group run supplies the behavioral evidence.
