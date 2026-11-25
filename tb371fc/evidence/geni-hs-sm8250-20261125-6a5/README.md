# GENI HS source-generation evidence — 2026-11-25

This bounded package accepts only source generation and its pure Python tests for the default-off SM8250 GENI HS reference profile. The source baseline is immutable commit `7d01e5e294b788f97a8120c2d34122ddb25e6343`; the candidate is based on `a91cf67d09380c7f7fa8868f7068dba6b3956daa`, whose additional RPM evidence does not change that baseline engine source.

The final pure unit run passed 49 tests. The final verifier checked 19 complete public files (810,000 bytes), two pinned reference JSON inputs, two generated files, 12 constants, two model windows and three clock bindings. All 51 negative cases were rejected. The verifier used the local pinned public-source cache and did not access devices, SSH, QEMU or private binaries.

## Files and interpretation

- [source-ci-contract.json](source-ci-contract.json) freezes the gate schema, exact values, commands and list lengths for CI integration.
- [source-generation-result.json](source-generation-result.json) is the exact final verifier result, including every public input identity, generated output identity and negative-case record.
- [source-generation-unit-result.json](source-generation-unit-result.json) records the exact final script hashes, exit status, 49-test count and complete safe success log.
- [source-generation-history.json](source-generation-history.json) preserves two initial failed generator attempts, the first successful derivation, the failed 48/49 unit run, the corrected 49/49 run and both verifier runs. It pins each retained raw failure log and source snapshot without publishing local-path tracebacks.
- [source-generation-freeze.json](source-generation-freeze.json) pins all owned source, input, output, documentation and evidence files except itself.
- [The profile description](../../docs/geni-hs-sm8250.md) explains public facts, sanitized private-review declarations, explicit model policy and the functional limits.

All 51 complete mutated files remain in the local verifier output and are reproducible from the committed verifier; their hashes and byte lengths are published. The public repository package includes the bounded results and provenance, not a duplicate cache of all public sources and mutants. Complete private reviewed bytes remain withheld; exact hashes are provenance identifiers, not a claim of public reproduction of that private review.

## Preserved failures

The first generation failure concerned whitespace after an opening brace. The second concerned confusing a function call in an `if` expression with a declaration. Both failures remain recorded. A separate pure unit run then found that consuming the preceding semicolon could hide an adjacent duplicate DT property. Its 48/49 outcome remains recorded. The corrected parser uses a zero-width boundary and passes all 49 unit tests and all 51 verifier negative cases.

## Acceptance boundary

This package does not claim C compilation, compiled UART behavior, migration correctness, a successful kernel probe, physical SE6 firmware identity, DDR payload success, Android boot or a connected radio peer. The wrapper compatibility value `0x20050000`, read-only UART revisions `0x201`, configuration revision `8` and 64-byte FIFO are an explicit reference profile with the precise evidence categories documented separately. DMA IRQ mask storage is supported policy; DMA transfers and completion remain unsupported.
