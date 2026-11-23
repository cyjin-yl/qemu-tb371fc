# SMEM partition decoder: synthetic-only CT validation

## Scope and current status

This package runs the existing 28 synthetic unittest methods for the partition
candidate decoder. It does not scan firmware, read a DAL receipt, select an ABI,
generate a handoff, or initialize SMEM. At package creation the Python sources
have not been compiled or executed by the author. A successful installation
means syntax compilation and on-disk identity checks only; a successful launcher
means the job was submitted, not that its fixtures passed.

The decoder and corrected fixture file are byte-identical to the reviewed drafts:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| smem_partition_candidates.py | 16046 | 17ce02aae0f4c24d68824d69e3421408e1bf58ab03ad08b28c3059c4ca4caf18 |
| test_smem_partition_candidates.py | 17885 | 234f1919b775f6272c4e65215a85cba12ac6d28bac33b13edc5a8478b0595240 |

The source pin remains `NATIVE_PIN = None`. There is no native DAL adapter;
the decoder CLI refuses before opening input paths, including if that pin alone
were later filled in. The last fixture checks this refusal using a mocked file
open. No native partition pointer or actual firmware bytes are included here.

## Fixed execution scope

The only installation directory is
`/work/smem-partition-decoder-20261123-01` in CT 102's
`qemu-tb371fc-dev` container. It must be new. The package consists of exactly
six files: the installer, runner, host launcher, decoder, fixtures and this
document. The installer consumes at most 262144 bytes of UTF-8 JSON, checks
the externally approved whole-package SHA-256 and each file's size/hash, pins
the two reviewed sources and this document, and verifies that its own executing
`-c` source is the installer in the package.

The installer compiles Python in memory, creates a new 0700 directory, creates
0600 files with exclusive/non-following opens, fsyncs and verifies readback.
It retains every created file and failure receipt. It neither executes the
fixtures nor reads firmware.

The host launcher requires the existing exact `qemu-tb371fc` tmux session.
It creates the exclusive new window `smem-partition-decoder-20261123-01`,
checks the returned window ID and same-name uniqueness, disables automatic
renaming, enables remain-on-exit, then submits only the fixed CT runner.
It does not create a host session, overwrite a window, stop another job, or
remove files. An uncertain launch must be inspected before a retry.

The runner requires `--run-approved-synthetic-fixtures` and checks that its
Python runtime is isolated, unoptimized, without site initialization, and with
bytecode writes disabled. It executes exactly one child command:

```text
python3 -I -B -S /work/smem-partition-decoder-20261123-01/test_smem_partition_candidates.py
```

The actual interpreter path is recorded from `sys.executable`. The fixture's
explicit importlib load resolves only its fixed sibling decoder under isolated
Python. No source body or test method is rewritten at execution time.

The child gets a separate process group and session. The deadline is 60 seconds;
on timeout it receives TERM, with a 2-second grace, then KILL if needed and a
5-second reap wait. A timeout, surviving process group, nonzero exit, missing or
ambiguous summary, skipped tests, changed source identity, or receipt error
fails the job. These are process-management limits, not hardware timing.

## Completion contract and retained evidence

Before and after the fixture run, all six installed source files must match the
installation receipt and the pinned reviewed sources. The actual unittest
output must contain exactly one `Ran 28 tests in ...` summary and one plain
`OK`, with exit code 0. No optional or firmware stage follows.

The runner retains `results/fixtures.stdout`, `results/fixtures.stderr`,
their hashes, actual child command, process-group status, timestamps, exit code
and source identities in `job.result.json`. It writes `job.exit` separately
and retains `job.started.json`. Exclusive creation prevents a retry from
overwriting an earlier result. Failure receipts and output remain available.

The expected result is one fixture phase, 28 methods, and
`fixtures_passed=true` plus `overall_complete=true`. Even that result
leaves firmware input read/execution, native adapter execution, platform
selection, entry reachability, SMEM initialization and handoff generation false.
A synthetic pass validates bounded parser behavior under the authored inputs;
it does not establish an actual XBL configuration or the producer's enum ABI.

## Reviewed template and narrow changes

The process-management code is adapted from the board agent's reviewed
transparent package blob `99a2362bd6a717d2310255d322a71040fba1feec`,
SHA-256 `df93cc0ef49e69135056a7feee7f4c911247cdc314dcae431adab043954d1b11`.
This adaptation changes the fixed work/window names, package/receipt schemas,
approved file identities, explicit launch flags and expected test count.
It removes the pinned firmware path, firmware identity reader, scan command,
scan output parser and firmware before/after checks. It adds an explicit check
of the isolated unoptimized runtime. The original decoder and 28 fixtures are
unchanged.

Installation and execution require root's coordinated device lease after
review of this package. Creating an unattached Git blob is not installation
or evidence of an executed test.
