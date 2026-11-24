# SM8250 SPSS interrupt-mask write control

The `qcom-sm8250-spss-irq-mask` device retains the single 32-bit control word
written by the original kernel's SPSS probe at **`0x01881028`**. Its explicitly
selected reference profile implements guest writes to this four-byte word.
The reset mask, MMIO readback, remote SPSS state, interrupt delivery and secure
firmware services remain outside this device's contract.

The source generation checks described below have run locally. Compilation,
component execution and a new original-kernel run with this device require
their own binary-specific receipts. The preceding original-kernel run reached
this write and faulted; it did not establish Android user-space or UI success.

## Actual production evidence

The [actual DT and probe extraction](../evidence/spss-irq-mask-20261124-6a5/actual-dt-and-pil-probe.json)
binds the complete original DT to SHA256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`
and original kernel to
`ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f`.
The SPSS node is `/soc/qcom,spss@1880000`, compatible
`qcom,pil-tz-generic`, with these named resources:

| Resource name | Physical address | Size | Use in the pinned driver |
| --- | --- | --- | --- |
| `sp2soc_irq_status` | `0x0188101c` | 4 B | Read from the interrupt handler |
| `sp2soc_irq_clr` | `0x01881024` | 4 B | Written when clearing a handled event |
| **`sp2soc_irq_mask`** | **`0x01881028`** | **4 B** | **Written once by the cold probe helper** |
| `rmb_err` | `0x0188103c` | 4 B | Read while handling PBL completion |
| `rmb_err_spare2` | `0x01882014` | 4 B | Read by the interrupt/error handlers |

The node declares `qcom,spss-scsr-bits = <24 25>`, PAS ID 14, firmware name
`spss`, and interrupt cells `<0 352 1>`. It has the generic IRQ-handler and
AOP-signalling properties. The `qcom,pil-no-auth` property is absent.

The [preceding native frontier](../evidence/spss-irq-mask-20261124-6a5/native-frontier-before-spss.json)
from exact `c02f3d794e85cb5bc95d1aca62780542e806d4dc`
reached `188101c.qcom,spss`, then faulted at guest time 3.740434 in
`pil_tz_driver_probe+0x6bc/0x788`. The complete extracted 1,928-byte probe has
SHA256 `e4776f8aa7ab65d93ec670dd08a95e34777ff438f04f6c25785ae79b85f5cf08`.
At offset `0x6bc`, opcode `0xb9000148` decodes as `STR W8, [X10, #0]`, a
four-byte store. The recorded `W8` is `0xfcffffff`; QEMU reported the invalid
physical write at `0x01881028`.

The [independent comparison](../evidence/spss-irq-mask-20261124-6a5/actual-source-boundary-comparison.json)
decodes the original DT's raw bytes, verifies the probe bytes, and compares
the resource names and bit positions with the fixed source. It establishes
this local path correspondence, without claiming the complete production
kernel was built from that source revision.

There is a concrete source/device difference: the original DT's SPSS
reserved memory is `0x8be00000 + 0x100000`, while the fixed R11 base DTS
declares `0x92300000 + 0x100000`. This device does not import either firmware
memory region, change the original DT, or synthesize remote image contents.

## Two immutable source inputs

Both full files come from `OnePlusOSS/android_kernel_oneplus_sm8250` at
`98719830b0de5584f5588bbe9f4c8304d97a64c6`:

| Input | Bytes | Git blob |
| --- | ---: | --- |
| [drivers/soc/qcom/subsys-pil-tz.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/soc/qcom/subsys-pil-tz.c) | 32,203 | `ed5e07c193b3841c60c94d92a4db259335d665c9` |
| [arch/arm64/boot/dts/vendor/qcom/kona.dtsi](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/arch/arm64/boot/dts/vendor/qcom/kona.dtsi) | 132,499 | `e9651f09bd579616787894dc8514429063623b2d` |

Their SHA256 values are respectively
`c574017a8f4e5ad5a4215434029d73bdc2c9ee9736b006429aa30a30ac219608`
and `12b4b191b0f517b0c9fdd42c23ad428193ef71e63831bade996f9a18b88ece55`.
The [generated manifest](spss-irq-mask-sm8250-registers.json) retains the full
immutable URLs, byte lengths, both hashes, resource inventory and parsed mask
expression.

In this source, `ERR_READY` and `PBL_DONE` are indices 0 and 1 into the
DT-provided bit array. `mask_scsr_irqs()` initializes a `uint32_t` to all ones
and clears those two selected bits before writing the mask field. With the
actual bit array this produces:

```text
0xffffffff & ~(1 << 24) & ~(1 << 25) = 0xfcffffff
```

A written one masks the corresponding notification under this driver
contract. The formula is an initial guest programming sequence; it does not
establish a silicon reset value, a reserved-bit mask, or firmware completion.

The probe maps all five resources before calling this helper. Those mapping
calls do not read or write the other four resources. Its later interrupt
handler reads remote error/status words, and error paths also read
`spare2 - 4` and `spare2 - 8`, at `0x01882010` and `0x0188200c`. These extra
reads are a further reason not to equate five DT resources with a complete
SPSS device model.

The fixed repository's `Documentation/devicetree/bindings` is a symlink to
`../../arch/arm64/boot/dts/vendor/bindings`; that target is absent at this
revision. No separate binding text is claimed as an input. The two complete
source files and original DT/instruction evidence define the present scope.

## Generation and verification

[`gen_spss_irq_mask.py`](../scripts/gen_spss_irq_mask.py) verifies both full
input identities before parsing or writing outputs. It selects the mask
resource by matching the probe's resource-name lookup to the helper's target
field, parses the flat SPSS DT node, and resolves the source's bit-index
macros against that node's bit array.

The helper is parsed using a restricted expression AST containing integer
constants, its local variable, unary complement, bitwise AND and `BIT()` of
the declared source bit array. Other operators or calls are rejected. The
generated mask is evaluated from this expression with 32-bit complement
semantics; it is not a literal value emitted independently of the inputs.

Exactly two outputs are reproduced:

- `hw/misc/qcom-sm8250-spss-irq-mask-data.inc`, containing the five
  `SPSS_IRQ_MASK_*` macros for base, window length, two bit positions and the
  driver mask.
- `tb371fc/docs/spss-irq-mask-sm8250-registers.json`, containing the same
  derived contract and the other resources explicitly marked out of scope.

```sh
python3 -B tb371fc/scripts/verify_spss_irq_mask_generation.py \
  --out /work/spss-mask-generation
```

The verifier fetches only the two fixed raw-source URLs. HTTP 429, HTTP 5xx
and transport timeouts may receive at most three attempts, with delays of
one and two seconds. Every attempt is recorded. A successful response with
the wrong length, SHA256 or Git blob is rejected immediately outside that
retry loop. It does not change revisions, switch URLs or substitute a cache.

For local reproduction, add `--source-root /path/to/source-root`; that root
must contain the exact `primary-r11/` hierarchy and the same full identity
checks still apply. The new output directory contains the verified input
bytes, generated files, generator stdout/stderr and `result.json`. Its schema
is `tb371fc-spss-irq-mask-generation-v1`; success requires exactly two verified
inputs, two byte-identical output checks and the exact derived contract.

## Device state and access contract

The board child `/machine/spss-irq-mask` owns only `0x01881028 + 4`. The
`enabled-reference` property defaults to false. Explicit selection is:

```text
-global qcom-sm8250-spss-irq-mask.enabled-reference=on
```

When enabled, an aligned 32-bit guest write records all 32 bits and marks
the control as programmed. Arbitrary `uint32_t` values, including zero and
all ones, are retained. The pinned driver does not establish a silicon
reserved-bit mask that would justify discarding some written bits.

All guest MMIO reads return `MEMTX_ERROR`; QOM inspection is a host-side
observation interface, not proof of hardware readback. Disabled writes and
other callback offsets/widths are rejected without changing this state.
Transactions crossing mapped/unmapped boundaries remain subject to QEMU's
address-space dispatch; this contract does not promise wider atomic bus
transactions across adjacent words.

Cold and reset state has `programmed=false`, `mask-known=false`. Its internal
`mask-value=0` is an unknown-state placeholder. A guest write of zero creates
the distinct known state `programmed=true`, `mask-known=true`, `mask-value=0`.
Reset returns to the unknown reference state and does not assert a measured
silicon power-on value.

The observation properties expose the profile, identity, geometry, value,
known/programmed state and VMState length. IRQ, clock input/output and timer
counts are zero. `readback-supported`, `ready-modeled`, `firmware-modeled`
and `pas-modeled` remain false. There is no automatic notification or remote
peer. The existing [QSEE banks](qsee-irq.md) at `0x01886000`/`0x01888000` and
[TCSR mutex](tcsr-mutex.md) at `0x01f40000` retain their separate contracts.

## Migration contract

VMState version 1 uses identity `0x53504d31` (`SPM1`) and a fixed **25-byte**
payload. Multi-byte fields use QEMU VMState's big-endian wire representation:

| Offset | Width | Field |
| ---: | ---: | --- |
| 0 | 4 | Reference profile |
| 4 | 4 | Reference identity |
| 8 | 8 | Physical base |
| 16 | 4 | Window bytes |
| 20 | 4 | Guest-programmed mask |
| 24 | 1 | Programmed flag |

Post-load rejects a profile, identity or geometry mismatch, a programmed flag
outside 0/1, nonzero hidden state in an unknown mask, or any programmed/nonzero
state in the disabled profile. A known zero mask remains distinguishable from
unknown zero. Migration does not invent readback, interrupts, firmware state
or security-service completion.

## Security and validation boundary

The source's trusted PIL callbacks separately call `PAS_INIT_IMAGE`,
`PAS_MEM_SETUP`, `PAS_AUTH_AND_RESET` and `PAS_SHUTDOWN` through SCM. None of
those services is implemented or acknowledged by this mask device. The mask
write does not authenticate an image, release a secure processor, or generate
PBL_DONE/SW_INIT_DONE. No fuse, strap, secure-memory ACL or remote processor
reset state was observed or inferred here. Selecting the existing TCSR
unlocked reference affects mutex ownership only; it does not unlock PAS.

The [source validation receipt](../evidence/spss-irq-mask-20261124-6a5/source-validation.json)
records the actual two-input/two-output reproduction and both complete-input
one-bit tamper rejections. It also records isolated parser and retry-policy
checks, with mocked transport explicitly distinguished from an online CI
fetch. These checks execute Python source processing only. Compiled device
tests, migration execution and a new original-kernel frontier must be assessed
from the subsequent exact-source, exact-binary CI/native receipts.
