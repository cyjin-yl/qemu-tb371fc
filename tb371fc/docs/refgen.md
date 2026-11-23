# Kona / SM8250 REFGEN software request

The `qcom-refgen` device implements the documented Kona software enable request
at REFGEN offset `0x80`. The model deliberately represents an unknown initial
request separately from a known disabled request. It does not model the analog
reference voltage, circuit settling, hardware votes, or a power-good signal.

## Evidence and the native failure

The captured TB371FC DT declares `qcom,refgen-kona-regulator` at
`0x088e7000` with a `0x84`-byte resource, phandle 179, a self-referencing
proxy supply, and a 5 microsecond enable ramp delay. The captured consumers are
two DSI controllers and six CSI PHYs. This capture does not identify REFGEN as a UFS or
USB supply. The allowlisted topology, capture hash, and source hashes are in
`../evidence/expansion-20261123/refgen-topology.json`.

An unmodified production kernel reached
`refgen_kona_is_enabled+0x14/0x28` through regulator proxy registration in
the native boot run of 2026-11-23. It faulted on a read at the mapped resource
plus `0x80`. This execution evidence identifies a missing software-visible
register; it does not reveal its real power-on or firmware handoff value.

The fixed [third-party source mirror][mirror] selects `refgen_kona_ops`
for the captured compatible. Its Kona enable, disable, and is-enabled
operations use only offset `0x80`, mask bit 0, and values 1/0. Enable and
disable perform a masked read-modify-write, so an unknown initial read cannot
honestly be replaced by zero. The downloaded mirror file has SHA-256
`d90e2160f6ad616ff993c8fa25b5799d78016b619c09b0258251ccddb5659841`.
It is not asserted to be the exact source of the production kernel.

Two primary sources independently corroborate the relevant interface:

- [Official Android Kona DT][android-dt] declares the same base, size,
  compatible, proxy arrangement, and ramp delay.
- [Upstream Linux REFGEN driver][upstream] uses CTRL5 at offset `0x80`,
  bit 0, enable 1, disable 0 for SM8250, with a 5 microsecond enable time.
  Its SDM845 register operations and IPQ9650 clocks/status path are different
  implementations and are not borrowed into this device.

The attempted direct official downstream driver download timed out. The
record distinguishes that failed retrieval from the successfully read official
DT/upstream driver and fixed mirror. Runtime sysfs state, if observed later,
would not by itself establish a cold-reset value.

## Register contract

Only four bytes at physical `0x088e7080` are mapped. The rest of the DT
resource is left unimplemented; this device does not claim a complete REFGEN
register block.

| Location | Supported access | Behavior |
| --- | --- | --- |
| DT base + `0x80`, device MMIO offset 0 | Aligned 32-bit little-endian read | Return known software request 0 or 1; reject if unknown |
| Same register | Aligned 32-bit write of 0 or 1 | Store disabled/enabled request and establish known state |
| Same window, unsupported width, offset, or reserved data bits | Unsupported | Diagnose and return `MEMTX_ERROR`; do not change the stored request |

A successful request write has immediate digital readback. The source's
5 microsecond driver delay does not establish an observable hardware-ready
register or a measured analog settling curve. The model therefore does not
invent either a timer-driven ready indication or a voltage.

The system memory layer may split a transaction that crosses the four-byte
region boundary before invoking the device. In particular, a crossing 8-byte
write can deliver a valid first 4-byte write and a second access elsewhere.
Neither the model nor the tests claim whole-transaction rejection or atomicity
for such a crossing access. The invalid-width tests stay within the mapped
window and check 1/2-byte accesses and unsupported data bits.

## Explicit initial-state policy

`boot-disabled-reference` is a Boolean qdev property, default false.

| Configuration / action | Stored state | Read behavior | `state-known` | `software-enable` |
| --- | --- | --- | --- | --- |
| Default reset, no measured handoff | Unknown | Rejected with a specific diagnostic | 0 | 0, invalid |
| Explicit reference reset | Disabled request | 0 | 1 | 0 |
| Supported full write 0 | Disabled request | 0 | 1 | 0 |
| Supported full write 1 | Enabled request | 1 | 1 | 1 |

For a diagnostic native boot using the documented reference reset state,
select it explicitly:

```sh
qemu-system-aarch64 ... \
  -global qcom-refgen.boot-disabled-reference=on
```

This is a model reference profile. It is not a measured register value or an
assertion about Lenovo firmware. Omitting the option intentionally leaves the
first `is_enabled` read unresolved. Advancing virtual time does not make an
unknown request known.

The default profile permits a supported direct full write to establish a
known request. It does not permit the downstream masked read-modify-write
helper to treat an unknown read as zero.

## Board and consumer interface

The board creates a child at `/machine/refgen` of type `qcom-refgen`.
Map MMIO index 0 at `0x088e7000 + QCOM_REFGEN_CTRL5_OFFSET`. The MMIO size
is `QCOM_REFGEN_CTRL5_SIZE` (four bytes). The device has no interrupt.

There are two named GPIO outputs, one line each:

- `software-enable` is the stored request bit when valid.
- `state-known` is validity. Consumers must honor this signal; a low
  request with low validity is not proof that the physical reference is off.

The corresponding read-only QOM Boolean properties expose the same logical
values. Read-only QOM string `state` reports `unknown`,
`disabled-request`, or `enabled-request`.

When the state becomes unknown, the model lowers validity before lowering
the request output. When a write establishes known state, it drives the
request before asserting validity. These are digital adapter semantics, not
an inferred analog circuit or a synthesized hardware vote.

The output interface allows future DSI/CSI models to consume the software
request honestly. Exporting the signals does not establish that those
consumers or the panel/cameras have been emulated.

## Reset and migration

Reset returns to the selected policy: unknown by default, known disabled
only with the explicit reference property. It does not preserve a previous
guest enable request.

VMState version 1 carries the request, its validity, and a separate serialized
copy of the source reset policy. The destination's configured property is not
overwritten. Incoming migration rejects a different policy in either
direction, even if both sides currently contain the same request value;
otherwise a later reset would silently change semantics. Invalid request
values and inconsistent validity are also rejected before output restoration.

The component test uses `ufs-enabled=off`. Its successful migration would
not establish migration support for a board with UFS in flight, analog state,
or external physical devices.

## Actual test entry point

```sh
python3 tb371fc/tests/test_refgen.py \
  --qemu /absolute/path/qemu-system-aarch64 \
  --out /fresh/output/refgen
```

The output directory must not exist. The suite is standalone and uses only
Python's standard library. The default fixture is
`tb371fc,ufs-enabled=off`, 128 MiB RAM, eight CPUs, and the QTest accelerator.

The exact test plan has 16 top-level checks:

- Ten behavior checks cover unknown reads, explicit reference initialization,
  writes establishing validity, the downstream read-modify-write sequence,
  reserved bits and subword rejection, both reset policies, and absence of
  invented initialization over time. Each named GPIO is actually intercepted
  in dedicated fixtures; validity is not inferred only from a text property.
- Four process-to-process migrations cover unknown state, known disabled state,
  enabled state without a reference reset policy, and enabled state with it.
  They verify restored registers and GPIOs, subsequent writes, and subsequent
  reset behavior.
- Two migrations with opposite reset profiles must be rejected by the
  destination, with the specific profile diagnostic and a nonzero exit.
  A sender reporting completion without a return path is not treated as
  evidence that the destination accepted the state.

The source clock is explicitly advanced by a positive step before transfer,
and the destination is aligned without issuing `clock_step 0`. Test
conditions use explicit exceptions and remain active under Python `-O`.
Startup and protocol waits have deadlines, sockets use short temporary paths,
stderr goes to a file rather than an undrained pipe, and owned QEMU processes
are reaped on failure. Per-fixture command lines, QMP/QTest transcripts,
diagnostics, and stderr remain in the output directory. Temporary socket
directories and migration socket paths are retained as referenced by these
transcripts. QTest's observer begins low and emits only transitions; an initial
low observation does not claim an emitted lower event. Dedicated enable,
disable, and reset cases require actual raise-to-lower transitions.

`result.json` contains the binary and test SHA-256 values, all completed
checks, `expected_checks: 16`, the six migration records in `migrations`
(`migration_expected_checks: 6`), and literal Boolean
`passed` / `all_passed` / `success`. All three are true only after
all 16 actual checks pass. A partial or failed run is retained and exits
nonzero.

QTest's memory commands do not expose `MemTxResult` to the Python client.
The rejection cases therefore require the model's diagnostic and unchanged
state; they do not pretend that a zero-filled QTest reply was a successful
register read. Actual native CPU fault/probe behavior is a separate
end-to-end observation. Source code and VMState declarations alone are not
test receipts; this document's test plan does not assert an unperformed run.

[mirror]: https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/regulator/refgen.c
[android-dt]: https://android.googlesource.com/kernel/msm-extra/devicetree/+/refs/heads/android-msm-bramble-4.19-android11-qpr1/qcom/kona-regulators.dtsi
[upstream]: https://code.googlesource.com/linux/torvalds/linux/+/7acf90feab8b009fde7def08ff2c622d0f10e99f/drivers/regulator/qcom-refgen-regulator.c

## Recorded integrated validation (2026-11-23)

The [16 targeted checks](../evidence/boot-resources-20261123-1005/refgen/report.json) and [55 public gates](../evidence/boot-resources-20261123-1005/public-55/result.json) passed on frozen binary SHA256 2173fa4da68f8f769c53942b056124c5621e4714652c7f572deacc9440ec09cf. Original receipts retain build/test source hashes. This validates the documented contract without expanding it to physical reset values, analog behavior, remote firmware services or full-tablet equivalence.
