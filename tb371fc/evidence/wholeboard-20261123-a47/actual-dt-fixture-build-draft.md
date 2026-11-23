# Full production-DT userspace fixture preparation

This directory holds the separate eight-check fixture in unreferenced commit
`896a02a8ed82e03552039f3f813012c092ef293d`, based on source
`3b31e83dc412962826ada872e361a1d32ea4e463`. It has not been compiled or run.
No QEMU, test suite, native diagnostic, guest ELF, or new guest process was
started to prepare it. No repository ref has been changed.

## Why a separate fixture is needed

The unchanged generic guest and RAM validator expect a model containing
`TB371FC`. The separately verified complete production DT with SHA256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`
instead has the exact root model `Qualcomm Technologies, Inc. kona QRD`.
The property is 37 bytes including its NUL terminator; its SHA256 is
`9bf6d330148e3423a006773f7ece6eac4c9719787a7da87c75574a00186d2878`.
This is an input identity check, not an assertion that an Android userspace
has booted or that a physical tablet matches every simulated device.

The model-only observation was performed by the parent workflow. The builder
does not read a production Image or DTB. It requires the approved public
identity receipt, Git blob `2336b3a9b6775ecccc8d962cbce89395a8020be0`, SHA256
`d7c70cfcdc7ac0d19df25feb22c3431d047129a85d03b4f306e97f5bd1f08e28`,
before invoking a compiler. The receipt is copied unchanged into the candidate.

## Candidate files

`candidate/tb371fc/tests/guest-init-actual-dt.c` changes only the bit-6 model
predicate to exact `strcmp` against the observed root model. Reversing that
single replacement reproduces every byte of the existing `guest-init.c`.

`candidate/tb371fc/scripts/boot_verdict_actual_dt.py` copies the complete
3120-byte decoder and `validate_record` from `boot_smoke_gdb.py`, changing
only the model predicate. The same strict counter-probe validator is reused.
It does not change the RAM record, suppress errors, or filter the original
validator's result. The eight check names/order, pass mask `0xff`, fail mask
zero, complete mask `0xff`, Linux/AArch64/online `0-7\n`, timer interval at
least 20 ms, monotonic timestamps, stdout byte count, and one complete probe
JSON remain required.

`candidate/tb371fc/scripts/build_actual_dt_fixture.py` is a build-only CLI.
It uses the exact existing GDB fixture compile flags:

```text
-static -fno-pie -no-pie -g -Os -Wall -Wextra -Werror
-Wl,--section-start=.tb371fc_checkpoint=0x8000000
```

The unchanged `build_arch_probe.sh` produces the probe ELF, whose size must be
2264 bytes and SHA256 must be
`0ed51cb6c6dd24685beb3f77056d17d47c5c2f697df96d87eef4a678d4c5cb1b`.
The builder records tool paths, executable hashes, version output, full
commands, and compiler/binutils logs. It validates ELF64 little-endian
AArch64 ET_EXEC, absence of PT_INTERP, the actual result symbol and writable
mapping for all 3120 bytes, and the fixed checkpoint's exact four-byte RET
through raw ELF decoding, `nm`, `readelf`, and `objdump`. The result address
comes from this build; the old fixture's address is never substituted.

The initramfs reuses the unchanged `cpio_entry` helper. Its seven entries,
in order, are `init`, `dev`, `dev/console`, `proc`, `sys`, `arch-probe`, and
`TRAILER!!!`. Modes, inodes and console rdev 5:1 match the existing fixture;
gzip mtime is zero. Only the new ELF/probe and archive are build outputs.

`workflow-full-dtb-fixture.patch` is the minimal workflow candidate. It adds
one build-only step after the four existing Linux boots and before artifact
upload. Its output is under the already uploaded
`tb371fc-results/full-dtb-user-fixture` directory. It changes neither old
gate names/order nor the component/Linux registry. Its receipt path and SHA are now bound to the approved exact input. No formal
build has yet executed this new step.

## Manifest contract

The builder writes `manifest.json` with schema
`tb371fc-full-dtb-user-fixture-v1`. It records:

- `build_completed`, source commit, commands and input/toolchain identities;
- expected full-DT identity and the exact independent model-observation
  receipt, with `dtb_read_by_builder` and `model_measured_by_builder` false;
- actual init/probe/archive hashes and sizes, complete ELF program headers,
  result/checkpoint symbols, RET bytes, and the seven archive-entry metadata;
- the unchanged eight-check and 3120-byte RAM ABI contract;
- `execution.performed: false`, with `userspace_passed`,
  `guest_shutdown_observed`, and `android_boot_passed` all null.

Successful compilation sets only `build_completed`. A later authorized
native run must use the newly accepted manifest's exact ELF/initrd hashes
and actual symbol addresses, retain the original full Image/DT hashes and
explicit model assumptions, and collect real TCG breakpoint/EL0/RAM evidence.
Eight-check completion and QMP guest shutdown must remain separate outcomes.

## Static preparation evidence

`static-preparation.json` records the exact local candidate identities and
the inverse-change proofs. Python was parsed and compiled to code objects
only; the builder and validator functions were not called. The existing
guest source, header, `boot_smoke*`, `boot_verdict.py`, architectural probe
source/build script, and four Linux gates remain unchanged.
