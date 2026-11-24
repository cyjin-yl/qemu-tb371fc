# Source77 userspace fixture preparation — unexecuted draft

The consumer and ZIP-stage helper remain unchanged. This additional host script
only maps accepted receipts and local copies of the primary evidence into a
candidate configuration and a copy inventory. It does not call either helper,
copy files remotely, invoke a compiler, or start QEMU. It is not an authorization
or a userspace verdict. Root must separately review the candidate, verify fresh
CT destinations, and decide whether to execute the reviewed consumer.

## Inputs and direct field mapping

| Consumer field | Actual source |
|---|---|
| `source_commit` | `<label>-artifact.json.source`, compared to the explicitly supplied source |
| `workflow_run`, `artifact_id`, `artifact_sha256`, `qemu_sha256` | Same-named artifact receipt fields, compared to explicit expectations |
| `runner_sha256` | Both `artifact.suites[].runner_sha256`, the independent plan and explicit expectation must match |
| `expected_gate_order` | `plan77-preflight.json.suites.{components,linux}.names`; it is **not** inferred from the artifact being checked |
| Receipt/configuration `*_sha256` | SHA256 of the actual file bytes, without JSON reserialization |
| `fixture_stage` | New CT destination for the actual host stage; retain every exact ZIP relative path |
| `manifest_sha256` | Actual stage receipt, rechecked against staged `manifest.json` bytes |
| `baseline_diagnostic` | Root supplies the unchanged CT primary directory; `diagnostic.json` is verified, not overwritten |
| `baseline_diagnostic_sha256` | Actual local copy, equal to `summary.diagnostic_sha256` and the extra raw-evidence entry |
| Complete primary kernel log | `extra.raw_evidence[name=kernel-printk.log]` binds its size/hash; local full file must be supplied beside the diagnostic |
| `primary_summary`, `primary_extra` | New CT copies of the exact host `<label>-summary.json` and `<label>-extra.json` |
| `qemu`, `expected_primary_machine` | Actual `diagnostic.command[0]` and the unique `-M` argument; both must match explicit reviewed expectations |
| `additional_reference_globals` | Explicit expected JSON list only: empty, or the one known RPMh global; command must contain fixed17 plus that exact list in order |
| `helpers` | Fresh CT support directory with the five hash-pinned Python files |
| Native probe and direct DT identity | Exact pinned JSON files copied to fresh CT support paths |
| `out` | Fresh, short CT output directory; no existing primary evidence is repurposed |

Source77 expectations already known are source
`09c638d6a6d4fa4a4e4e332a7b39aa7bf7f5418e`, run `45264653096`, and runner SHA256
`491e94f0a8c81efe62c5e62acb0f07a52ec4140ba31d166a420524cdeae555fc`.
Binary, artifact and primary values remain placeholders until actual accepted
files exist. The script rejects those placeholders. It requires precisely
73 component names and the unchanged four Linux names from the independent plan.

## Required host layout

Under `--hostroot`:

- `<label>-artifact.json`
- `<label>-summary.json`
- `<label>-extra.json`
- `<label>-full-dtb-fixture-stage.json`
- `<label>-full-dtb-fixture-stage/tb371fc-results/full-dtb-user-fixture/`,
  containing the eight stage members.

Under `--primary-dir`, root provides byte-exact local copies of the CT
`diagnostic.json` and **complete** `kernel-printk.log`. The separate
`--ct-primary-dir` is their original CT location. The copy inventory marks
these two as `verify_only_do_not_overwrite`.

Under `--support`, provide:

- `helpers/boot_smoke_gdb.py`, `boot_smoke.py`, `boot_qmp.py`,
  `boot_verdict.py`, and `boot_verdict_actual_dt.py`; no bytecode cache.
- `android-arch-probe.json`.
- `actual-dt-model-3b31e8.json`.

Supply the original reviewed consumer as `--consumer`; its SHA256 must be
`7466f3dc6ffffeaa40585d9f48429d5e74bb9d0b0144cd76ed5bf688f41db72b`.
Only literal constants are read from its AST; its module/functions are never
imported or executed by preparation.

## Future local invocation for root review

Fill the expected template with actual accepted values first, then use a fresh
host output directory. This command has **not** been executed:

```sh
python3 prepare-userspace8-staging77.py \
  --hostroot /tank/shared/projects/tb371fc-wholeboard-a47 \
  --label native-09c638 \
  --expected userspace8-staging77-expected.json \
  --plan plan77-preflight.json \
  --primary-dir PRIMARY_EVIDENCE_LOCAL_COPY \
  --ct-primary-dir ORIGINAL_PRIMARY_CT_DIRECTORY \
  --ctroot /work/wholeboard-a47 \
  --support PINNED_SUPPORT_LOCAL_DIRECTORY \
  --consumer native-userspace8-actual-dt-draft.py \
  --out FRESH_HOST_PREPARATION_DIRECTORY
```

No config is written unless artifact/source/plan, actual stage bytes and the
same-binary primary checks all succeed. Full-log and milestone checks reject
`Kernel panic`, `Internal error` or `Unable to handle`; observer errors must
be absent. Device-error count is deliberately not a fatal gate.

The only outputs are `consumer-config.json` and `copy-inventory.json`.
Copy destinations are required to be absent; the inventory instructs root to
create fresh CT stage/support directories and preserve all bytes. Existing
binary/kernel/DT/command database/old primary initrd are not recopied or altered;
the consumer independently hashes them before launch. The inventory lists a
consumer invocation for review only and does not execute it. It is intentionally
not a shell script that performs copies.

## Preparation validation status

The source was parsed with AST and compiled to a code object only. Neither
`main()` nor any preparation/consumer function was called. Actual source77
build/ZIP stage, primary readiness, EL0 eight-check results and shutdown remain
separate evidence to obtain. This draft does not replace the already reviewed
raw ELF/CPIO validation in the ZIP-stage helper and consumer.
