# Independent doesvm CT build: actual attempt 05 success

The exact source commit `fe89e5a553fe038babe07abb279a0975d4306715`
was independently reconstructed and built inside doesvm CT102 Docker
`qemu-tb371fc-dev`. **All 11 XML tests and all 14 CPU preflight checks passed.**
Both `job.exit` and `build.exit` contain zero.

## Reproducible identity and actual execution

| Item | Observed identity or result |
| --- | --- |
| Commit | `fe89e5a553fe038babe07abb279a0975d4306715` |
| Tree | `302cff20495d2b63e49a5bbde8879c028fb583ea` |
| Actual QEMU | 62,228,488 bytes |
| QEMU SHA256 | `c6bed2c465e0b771d6e40d60225b9f89636f6d213bf78b3c174510c24f3f4493` |
| Frozen runner SHA256 | `18896b8b53720df6809441624b68342245bb7e09bfab8eee26b161dbb3ab613b` |
| Certificate SHA256 | `c67e18406ee1c577483a93fa6fad969c7b1b4b6befbf28caecceb791d8734966` |
| CPU preflight result SHA256 | `8d7c4231f15a0d7a68a26ae969a06fe536f3026c313a0023b1565948a84bb5a1` |
| Started | `2026-11-23T14:35:20.343585+00:00` |
| Finished | `2026-11-23T14:54:01.293404+00:00` |
| Total elapsed | 1,120.950 seconds |
| Combined XML and CPU tests | 4.765 seconds |

[The original certificate](result.json) records 34 completed commands.
Every command returned zero, and all recorded process groups were absent
after waiting and cleanup. Source inputs, dependency inputs, the binary,
test scripts, controller and transport remained unchanged.

[The original CPU report](preflight/result.json) retains each of the
fourteen actual assertions. [XML output](31-xml-unit.log) retains the
eleven passing tests. [manifest.json](manifest.json) is a derived file
index, explicitly distinguished from those original receipts.

## Source acquisition after the preserved fetch failure

[Attempt 04](../ct-attempt-04/README.md) failed during network Git fetch
before compilation or tests. Its files and failure remain unchanged.

This new attempt used a
[transparent packet of public Git objects](method/public-git-objects.json):
22 changed blob objects plus the original commit bytes, bound to the
already available parent `567e4f9834547f1445ecf24de96603132df8cfb8`.
Each blob Git identity and SHA256 was checked. A separate Git index rebuilt
the exact tree; `hash-object` reconstructed the original commit identity.
[The acquisition receipt](source-acquisition.json) records those checks.
The source checkout and build output are separate from earlier attempts;
network fetch was not required.

The original fourteen-case function remained byte-identical. The
[controller](method/build_and_preflight.py) and its
[focused change from attempt 04](method/ct04-to-ct05.diff) are retained.
This CT binary differs from the separately built public CI binary;
the two executions have distinct hashes and receipts.

## Scope

These tests execute synthetic guest instructions and read no private
firmware. They establish the stated ordinary TCG watchpoint behavior,
XML parsing and observation interfaces on this exact CT binary.

The raw RSP range base is distinguished from the actual overlap address.
The STM boundary case explicitly permits and records earlier legal
element writes while verifying the first forbidden tail is untouched.
The reference Cortex-M3 is not an identification of the tablet's AOP CPU.
Internal exception-vector/stack accesses remain outside the proven
before-access guarantee.

No private firmware execution, native AOP reset, peripheral service,
full firmware start, Android boot or full tablet equivalence is claimed
by this checkpoint. A subsequent private diagnostic must retain its own
input identities and actual stopping result.
