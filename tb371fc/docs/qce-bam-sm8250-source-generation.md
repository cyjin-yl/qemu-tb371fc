# QCE/BAM virtual cold-control source generation

The generated include binds **`virtual-qce-bam-cold-control-v1`**, an explicitly
configured control interface. It does not identify the tablet's physical QCE or
BAM revision, reset state, secure firmware result, or working cryptographic
datapath. The device remains disabled by default. No key, payload, descriptor
submission, DMA execution, or successful completion is supplied by this profile.

The reviewed profile is
[`qce-bam-sm8250-reference-profile.json`](qce-bam-sm8250-reference-profile.json).
Its complete file is 16,599 bytes with SHA256
`6bb5bff5a445a73fab54261376ef73a820c6b2ac050048e5fca45715755f76be`.
Its canonical JSON is 13,434 bytes with SHA256
`f9809a8de0e6b91c57e7baf3d675dfd16c4ce1bc6e2797a0d866ec42bebf1c3b`.
Canonicalization sorts keys, uses compact comma/colon separators, ensures ASCII,
encodes UTF-8, and adds no newline or self-digest field. The latter digest is the
32-byte immutable profile identity emitted to the model.

## Independent derivation

The production generator first verifies **all 12 complete source files** using
their fixed repository, revision, byte count, SHA256 and Git blob SHA1. The
reviewed profile must carry the same complete pin set. Neither input source pins
nor numeric identity words can be overridden on the command line. The complete
bundle is 770,745 bytes; its original copyright and licence notices are retained
in [`../sources/qce-bam-sm8250/`](../sources/qce-bam-sm8250/README.md).

Only after every source identity passes does the generator decode and parse
source text. It selects the positive `CONFIG_SPS_SUPPORT_NDP_BAM` definition arm,
uses the `sps_bam_type` enum to select the third, 4K register table, and compares
that table with upstream `bam_v1_7_reg_info`. Legacy/NDP addresses and definitions
cannot satisfy a missing 4K or selected-NDP definition. DT properties must occur
in the selected node's own scope, not a child or unrelated node. Required
definitions, table entries and anchors must be unique. Integer expressions use
a bounded AST whitelist; the tool never evaluates C, arbitrary Python, imports,
attribute access, comprehensions or external functions.

The complete facts and intermediate fields appear in
[`qce-bam-sm8250-source-manifest.json`](qce-bam-sm8250-source-manifest.json).
The generator calculates all seven words independently, then requires equality
with the reviewed profile before producing the include.

| Output word | Derived value | Source fact and explicit virtual choice |
| --- | ---: | --- |
| QCE version | `0x05030001` | Select the vendor's named 5.3.1 software ABI; encode it through matching vendor/upstream version masks. No physical-version claim. |
| QCE engines | `0x00000000` | Every source-defined engine, algorithm-selection and burst/topology field is clear because the model accepts no crypto work. |
| BAM configuration | `0x07fff004` | Parse all 17 terms of upstream `BAM_CNFG_BITS_DEFAULT = 0x0ffff004`, then remove only command-descriptor-enable bit 27. Retained flags describe frozen control configuration; their transfer behavior is not executed. |
| BAM revision | `0x00000203` | Two EEs come from the complete SM8250 DTS. Low-byte marker 3 is a newly defined virtual non-MTI control ABI marker, derived as source MTI cutoff 2 plus one. It is not a silicon encoding of compatible string `bam-v1.7.4`. |
| BAM pipe count / width | `0x00004008` | Eight channels come from SM8250 DTS. Width code 1 is an explicit virtual choice; the vendor decoder maps zero to 32 bits and nonzero to 36 bits, matching the four-bit descriptor high-address field. |
| EE0 attribute | `0x800000f0` | Explicitly admit the configured virtual controller and assign the source-defined client pairs 3 and 2, hence pipes 4–7. This is no claim of physical secure initialization or DMA readiness. |
| Initial EE IRQ mask | `0x00000000` | Define an idle virtual initial mask. The model does not create transfer-completion state. |

The upstream compatible fallback selects a register layout; it does not supply
a numeric physical revision. A vendor/upstream difference in the CE-buffer-size
field is retained in the manifest: vendor names bits 13:12 and upstream names
bit 13. Both bits are zero in this profile, so no nonzero capability meaning is
inferred.

## First registration and cached clients

The generator also checks the relevant source branches in `qce50.c`. First BAM
registration reads configuration once and records that command descriptors are
unsupported; that branch clears the first client's `no_get_around` flag. A later
client finds the shared BAM registration and copies the support flag before
returning. It does not re-read configuration or clear its own version-derived
`no_get_around` flag. Both clients omit command-list setup because command
descriptors are disabled. The checked setup-data and dummy-request helpers have
no direct MMIO or submit call; they prepare RAM structures.

`qce_hw_support` separately hardcodes several software algorithm-support flags.
Those declarations do not come from the engines word and are not implemented
cryptographic capabilities of this model. Clearing engines therefore must not
be described as suppressing all driver algorithm registration. A later actual
operation can attempt unsupported direct QCE configuration before any BAM
doorbell; the device must reject the first unsupported access atomically.

The first cold BAM enable checks remote EE attribute, revision and pipe count
once for the shared BAM. Later pipe connections do not repeat that initial
check. Source-based component tests may use both client orders and chosen
descriptor addresses as explicit fixtures; they are not an observed original
kernel transaction trace.

## Verification and retained failures

`verify_qce_bam_sm8250_generation.py` regenerates and compares both complete
outputs, then independently checks the ten include macros, seven literal
reviewed words, full digest bytes, resource geometry, selected 4K addresses,
bitfields, 17 configuration terms and the first/cache-hit distinction. It uses
no C-model values as an expected-value oracle.

The completed source-only run has two gates and **127 rejection cases**:

| Category | Cases |
| --- | ---: |
| Profile policy, types, fields and immutable pin set | 36 |
| Duplicate/nonfinite/truncated JSON | 3 |
| One-bit and truncated complete input, for each of 12 sources | 24 |
| Oversized, missing or symlink input | 3 |
| Scoped parser fixtures | 42 |
| Illegal integer expressions | 12 |
| Independently changed derived word versus reviewed profile | 7 |

Each complete-file identity case observes zero source-parser calls. Parser
fixtures are separately hashed and retained. They call only the pure parser;
they never replace the production pin table or produce a bound include. Two
positive fixtures additionally demonstrate correct interpretation of an
equivalent safe integer expression and isolation from a changed unselected
legacy definition.

The first generator execution failed closed on an overly literal match of a
wrapped YAML description. Its original source, stderr and exit status remain
retained. The correction normalizes description whitespace and fixes two exact
source-call/name anchors found during inspection. The first full verifier
execution then passed output checks and 66 rejection cases before its fixture
builder stopped on an overly literal whitespace match for the MTI macro. The
fixture correction selects the unique existing macro line; the complete
successor run passes all 127 cases. Both first failures and their source diffs
remain part of the source-generation evidence. There is no automatic retry.

These are local Python source-generation results. They do not claim a compiled
QEMU test, a native kernel probe result, working cryptography, Android userspace,
or a completed UI boot.

## Commands

```sh
python3 -B tb371fc/scripts/gen_qce_bam_sm8250.py \
  --source-root tb371fc/sources/qce-bam-sm8250 \
  --output-root .

python3 -B tb371fc/scripts/verify_qce_bam_sm8250_generation.py \
  --out /tmp/qce-bam-source-generation-new
```

The output directory for verification must be new. Generator outputs are
`hw/misc/qcom-sm8250-qce-bam-profile.inc` and this documentation directory's
source manifest. Existing byte-identical outputs are accepted for reproduction;
different existing output bytes cause failure instead of replacement.
