# PDC validation, 23 November 2026

Frozen QEMU SHA-256:
5a5c76062c5345d2be5960932a3a7ccefc73c6523e1531ea1b65088f5cdad555.

- [Compiled C QTests](qtest-result.json): **17/17**, exit 0; [original TAP](qtest.log).
- [Migration](migration-result.json): **9/9**, **two actual QMP migrations**.
- [Source provenance](source-provenance.json) and [publication review](publication-review.json).
- [SHA256SUMS](SHA256SUMS) covers this directory.

Run: 2026-11-23 08:02:49-08:02:52 UTC. Results and log are exact copies.
Real-GIC tests and intercepted-wire tests are separate because QTest
interception disconnects the parent connection.

**This is a historical 17/9 acceptance run.** Source and test definitions
subsequently gained an unsupported-config-bit regression and a no-edge-replay
migration regression (18/10). Their current hashes are recorded separately;
this receipt does not claim those later changes passed against this binary.

Tests explicitly select legacy reference VERSION 0x20000. **Native VERSION
remains unknown** and default VERSION access fails. Native DT supplies
addresses/routes; the cited driver sources provide protocol interpretation.

These results validate the documented MMIO, interrupt and migration subset,
not electrical timing, power-state wake, firmware behavior or complete tablet
equivalence. See [model documentation](../../docs/pdc.md).
