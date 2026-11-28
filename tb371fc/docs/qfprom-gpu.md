# SM8250 GPU QFPROM cells

## Observed original-kernel failure

The tenth original-image diagnostic retained the original kernel, ramdisk,
full DT and bootargs. Its printk explicitly reports SDE RSC initialization at
guest time 7.870924 and successful SDE RSC probe at 7.871914. The next fatal
exception at 8.349365 is a synchronous external abort, ESR `0x96000010`:

* PC `qfprom_reg_read+0x1c/0x48`.
* Call chain `nvmem_cell_read` to `adreno_probe+0x3b4/0x10b0`.
* QEMU rejected a one-byte read at physical address `0x0078419b`.

The diagnostic ended on this kernel failure. It had no original UFS LUNs and
did not reach Android userspace, GPU rendering or full-device boot. A normal
host QMP quit closed and reaped the QEMU process. This model does not change
that recorded result or replay its consumed artifact.

Input/evidence SHA256 pins:

| Item | SHA256 |
|---|---|
| Original kernel | `ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f` |
| Original full DT | `5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207` |
| Native010 result | `f36e5ef893743b6eae078d5883e7f60ffd42f1124cd0ba4e07dd53dc030b5d97` |
| Native010 printk | `0084bf7002850733565031fe32c118cbabf27091aae7448d3b5d5471d2569d08` |
| Native010 QEMU diagnostic log | `f36697d3e8499ec43b731e3febd39a81ee40a660faef686394d42af31dfa472e` |

## Exact cells and source interpretation

The original `/soc/qfprom@780000` is read-only, with
`reg = <0x780000 0x5000>`. Its GPU consumer names two cells:

| Physical bytes | DT cell | Consumer name | Interpretation |
|---|---|---|---|
| `0x78419b` | `gpu_speed_bin@419b`, `reg=<0x419b 1>`, `bits=<5 3>` | `speed_bin` | Guest nvmem extracts `(raw_byte >> 5) & 7` |
| `0x7845c8..0x7845cb` | `gpu_lm_efuse@45c8`, `reg=<0x45c8 4>` | `isense_slope` | Four raw little-endian bytes |

The original GPU phandles are `0x335,0x336`, in slope/speed order. The
faulting cell is not a GPU-disable fuse. The actual GPU node contains no
`qcom,gpu-disable-fuse` property. The separate thermal cell at offset `0x1a2`
is outside this model.

Disassembly of the pinned Lenovo 4.19.157 kernel confirms an `ldrb` at
`qfprom_reg_read+0x1c`. The provider loops by one byte. The actual
`adreno_probe+0x388` obtains `speed_bin`, `+0x3b0` reads it, and `+0x410`
calls `nvmem_cell_read_u32` with the next actual string `isense_slope`.
Returning a pre-shifted bin would be wrong: nvmem applies the bits property.

The corresponding public reference is OnePlus 4.19.110 commit
`98719830b0de5584f5588bbe9f4c8304d97a64c6`, rather than the exact Lenovo
kernel source:

* [qfprom byte provider](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/nvmem/qfprom.c)
* [nvmem core cell extraction](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/nvmem/core.c)
* [Adreno cell reads and power-bin selection](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/gpu/msm/adreno.c)
* [LM data passed to GMU HFI](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/gpu/msm/kgsl_hfi.c)

The retained physical-device metadata confirms that KGSL and QFPROM bound
drivers, but does not contain either raw fuse value. The actual DT has
power-level entries for decoded bins 0, 1, 2, 3 and 4. Those alternatives do
not identify the physical device's bin. The slope is configuration/calibration
data, not a ready flag or a completion acknowledgment.

## Explicit configuration and narrow decoding

The independent QOM child is `/machine/qfprom-gpu`, with type
`qcom-sm8250-qfprom-gpu`. Machine properties are:

* `qfprom-gpu-enabled`: boolean, default off.
* `qfprom-gpu-speed-bin-byte`: complete raw byte, exactly `0xhh` in lowercase.
* `qfprom-gpu-isense-slope-word`: complete word, exactly `0xhhhhhhhh` in lowercase.

Enabled mode requires both values. Providing either value while disabled is
an error. There is no implicit zero value, truncation, sign, whitespace or
uppercase spelling. Device properties are `enabled`, `speed-bin-byte` and
`isense-slope-word`. The read-only `bound-speed-bin-byte` and
`bound-isense-slope-word` report `UNBOUND` while off. Machine setters reject
changes after creation; device configuration is fixed after realization.

Two 32-byte software decode guards at `0x784190` and `0x7845c0` are enabled
only in the opt-in configuration. Their extent keeps 2-, 4- and 8-byte accesses
starting at any defined byte intact for rejection. The extent does not claim
physical fuse-bank contents. Only the five specified one-byte reads succeed.
The slope is returned least-significant byte first. All writes, other widths
and other offsets return `MEMTX_ERROR` with a device diagnostic. There is no
backing RAM and no blanket mapping of the original 0x5000-byte provider.

The independent SPMI debug fuse at `0x7820a8`, ordinary SPMI, thermal fuses,
GPU/GMU registers and firmware execution are unchanged. A readable nvmem cell
does not create an interrupt, GPU ready indication, HFI reply or render result.

## Reference identity policy

`qfprom-gpu-reference-policy.json` records an explicitly virtual diagnostic
identity: raw speed byte `0x40` (bin 2 and five synthetic reserved zero bits),
and raw slope word `0x00000000` as an **uncalibrated virtual fixture**. Bin 2's
six nonzero power-level frequencies (410, 452, 537, 602, 642 and 683 MHz) match
the original GPU's selected OPP table, phandle `0x333`. This preserves internal
DT consistency; it does not infer a measured bin, silicon frequency or
performance. Choosing bin 0 would instead select frequencies absent from that
OPP table. The zero slope is not asserted to be correct physical calibration
or a neutral value for real hardware. The current device only exposes immutable
configuration; no calibrated LM/GMU behavior is implemented or validated.

This policy supports bounded kernel diagnosis through the nvmem read path.
It cannot substantiate GPU functionality, calibrated power/thermal behavior,
physical equivalence or successful Android boot. Hardware-derived use requires
a separately identified read-only capture of all five bytes. The model accepts
explicit identities so such a capture can replace the virtual fixture without
silently modifying original kernel/DT inputs. The policy does not enable the
device by default or authorize reuse of a prior native artifact.

## Reset, migration and validation

Reset preserves the immutable identity. VMState version 1 has nine bytes:
big-endian `profile = 0x51474600 | enabled`, the raw one-byte speed value,
then the big-endian slope word. All three fields use equality checks. A
destination with a different on/off state, speed byte or slope rejects incoming
state rather than replacing its configuration. The disabled profile's internal
zeros are serialization sentinels, not readable fuse values. This does not
assert that a failed whole-machine load left unrelated guest RAM untouched.

`test_qfprom_gpu.py` exercises the compiled binary through real QMP/QTest:
explicit/off configurations, five byte reads and endianness, field extraction,
strict rejection, immutable QOM configuration, reset, complete VMState payload,
same-identity load and each incompatible identity. It also checks independence
from the existing SPMI debug fuse. Protocol replies alone are not interpreted as
successful or rejected memory transactions: diagnostics and identity checks
provide the evidence. Owned children must actually exit and be reaped; forced
cleanup fails acceptance. The public workflow retains the result and logs.
Consult the exact commit's CI result for execution status; this documentation
does not itself claim that a test or a subsequent original-image boot passed.
