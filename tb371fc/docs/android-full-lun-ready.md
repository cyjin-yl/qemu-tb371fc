# Consume a complete original-storage assembly

`boot_android_from_ready.py` connects a completed six-LUN assembly to the
existing `boot_android.py` observer. Earlier no-LUN runs remain useful for
locating a kernel's first hardware fault, but cannot establish an Android boot
with the original storage. This entry point rejects those inputs.

The coordinator operates on captured local files only. It does not connect to
the tablet, acquire blocks, freeze a filesystem, change partitions, or select a
new device model. Its binding supplies the exact machine configuration and
previously tested executables. The actual observation, writable overlays and
QMP shutdown still belong to the existing Android runner.

This handoff uses the generation2 freezer layout: completed new jobs are stored
under `ready.json`'s parent directory at `captures/<job-name>/manifest.json`.
The retained super capture may remain in the earlier verified baseline tree.
Original capture paths recorded by the host and frozen container paths differ;
their ordered manifest hashes, source identity and exact byte coverage bind
them to the same acquisition. The coordinator does not require those two path
names to be equal.

## Bind the completed inputs

The private binding has schema `tb371fc-full-lun-boot-binding-v1`. It is an
execution manifest prepared by the orchestration process from completed
receipts, not a human approval requirement. File paths directly supplied by the
binding refer to the filesystem in which the coordinator will execute. Paths
inside historical receipts retain their original host provenance. In a
container, copy receipt bytes
without rewriting their recorded provenance and bind the copied files' hashes.

| Field | Required identity |
| --- | --- |
| `ready` | Exact ready receipt path and SHA-256 |
| `prepared_manifest` | Exact prepared manifest path and SHA-256 |
| `lun0_assembly`, `baseline_verification`, `generation_completion` | Completed receipt paths and SHA-256 values |
| `generation_plan_sha256`, `source_binding_sha256` | The selected capture generation and original source binding |
| `capture_counts` | Explicit `jobs`, `assembly_manifests` and `chunks` counts |
| `inputs` | Original `kernel`, `initrd`, `dtb`, `bootargs` and `boot_image` identities |
| `luns` | Exactly LUNs 0 through 5, with raw paths, supplied digests, full source identities and measured geometry |
| `executables` | Exact `qemu` and `qemu_img` paths and SHA-256 values |
| `runner_dependencies` | Digests of the deployed Android runner and its three supporting modules |
| `machine`, `global_properties`, `resources` | Explicit machine settings, device properties and all referenced input files, including AOP data |
| `observation`, `printk` | Bounded observation settings and, when used, printk symbols tied to the original kernel |

An assembler's ready flag alone is insufficient. The coordinator checks the
generation and source chain, the complete LUN0 assembly and the previously
verified remaining LUNs, and rejects a missing, duplicated or differently bound
LUN. The original input loader performs structural validation; the existing
runner performs the complete digest readback before creating overlays and
starting QEMU. Supplying a digest records an expected identity, not proof that
the file has already passed that readback.

The binding must be complete before invocation. A bounded wait can accommodate
an already bound ready receipt being delivered to the execution filesystem;
it does not guess hashes or pick the newest available generation.

## Explicit mapped generation2 handoff

An assembly that explicitly selects replacement captures uses binding schema
`tb371fc-full-lun-boot-binding-v2`. Select that schema and one exact ready path
in the private binding; the consumer does not infer an upgrade from directory
names or search for a latest ready file. The original v1 binding, receipt checks
and runner behavior remain available unchanged.

The v2 ready schema is `tb371fc-mapped-generation2-ready-v1`. Its completion is
`tb371fc-generation2-mapped-verification-v1`, published as
`ready.parent/mapped-generation2-complete.json`. It records explicit selected
captures and byte-verification results. Neither a legacy
`generation2_capture_complete` marker nor a `generation_completion` binding may
be substituted for it.

V2 keeps the common boot inputs, six LUNs, baseline and LU0 assembly bindings
above, and replaces the legacy completion binding with these fields:

| Field | Required identity |
| --- | --- |
| `mapped_generation_completion` | Full `{path, bytes, sha256}` pin of the new completion in the selected assembly |
| `selection_map` | Full pin of the frozen selection-map bytes selected by the evidence index |
| `frozen_evidence_index` | Full pin of `ready.parent/selected-evidence-index.json` |
| `evidence_dependencies` | Explicit one-element list containing the full original pin of the reviewed `prepare_batch_attempt_v3.py` source |

The completion pins its original selection map and the index digest. Each index
row maps a canonical historical `original` full pin to a unique
`frozen_path` under `ready.parent/selected-evidence/NNNN.bin`. Host paths in
these receipts are provenance identifiers. The consumer resolves them through
the exact index, opens only the frozen files in its execution filesystem, and
requires every indexed file to be consumed by the receipt chain or explicitly
declared in `evidence_dependencies`. Missing, conflicting, duplicate or
unreferenced mappings fail preflight. All frozen files join the existing input
identity/digest recheck before and after the observation.

Evidence dependencies are read as bytes only. The consumer does not import or
execute the frozen assembler, acquisition runtime or provenance preparer.
They are separate from `runner_dependencies`: the existing four-module runner
execution boundary is unchanged. The preparer and both admitted acquisition
runtime profiles must match the reviewed byte lengths and SHA-256 values;
rebuilding request and index hashes cannot admit a substituted runtime.

This mapped profile requires all 107 ordered jobs, 108 assembly manifests and
1,842 chunks: 1,794 job chunks plus 48 retained super chunks. Prefix and tails
000 through 080 remain at their original selected locations; tails 081 through
105 use explicit fresh-capture locations and complete acquisition evidence.
Chunk counts follow each exact range list. The first tail has 128 separate
8 MiB ranges, while the retained super capture uses its own 256 MiB chunk
geometry; neither is treated as a single 64 MiB chunk sequence. Replacement
jobs retain the original 64 MiB per-chunk bound.

Each selected manifest is checked against the original job, the logical frozen
capture path, its saved plan and frozen-copy provenance, the full worker-result
pin and the worker's hash-bound plan. Replacement evidence binds the request,
typed one- or four-chunk budget, final receiver acknowledgement, drained relay,
RAM source result and exact source/controller/SSH termination identities, plus
the retained host observation and its endpoint identities. A paused attempt is
insufficient. The common whole-LUN range coverage and six-LUN baseline gates
still run after this metadata validation.

V2 emits `tb371fc-full-lun-boot-plan-v2` and
`tb371fc-full-lun-boot-result-v2`, retaining the common outcome fields and adding
the explicit binding schema. The plan also records the selected-map, mapped
completion and frozen-index digests. A successful metadata dry run proves this
handoff's consistency; complete raw-byte digest verification remains the
existing runner's responsibility before QEMU can start.

The private native coordinator needs a separate owner-selected v2 binding and
once-run invocation for this exact ready path. Publishing this consumer or a
mapped ready receipt does not wire that coordinator automatically, authorize a
retry, or establish a device boot result.

## Execute once under the native-run lock

Use new private output paths for every attempt:

```sh
python3 tb371fc/scripts/boot_android_from_ready.py \
  --ready /private/assembly/ready.json \
  --binding /private/full-lun-binding.json \
  --lock-file /private/shared-native-run.lock \
  --out /private/full-lun-attempt-001 \
  --dry-run
```

Remove `--dry-run` for the actual bounded run. The coordinator does not add
boot arguments, replace the ramdisk, trim the device tree, or change the
requested model properties. Each original raw backing remains read-only;
guest writes go to fresh per-LUN qcow2 overlays.

All native-run coordinators must contend on the same lock inode. When another
coordinator owns a host lock outside a container mount, hold that host lock
around the entire container invocation as well; a similarly named file inside
the container is a different lock. A busy lock is a refusal to start, not a
reason to terminate another run.

`attempt.json` records an attempted invocation, `plan.json` records its bound
inputs, `runner/` contains the existing runner's evidence, and `result.json`
records the final outcome. A pre-existing output directory is rejected.
Interrupted or ambiguous attempts require inspection of their existing
receipts; the coordinator never retries a possibly executed guest implicitly.

## Interpret the result

A dry run checks the handoff and records `executed=false`; it creates no guest
and proves no Android boot. Its `full_lun_digests_verified` remains false; only
the real runner's six completed digest checks can set that field true. An
actual successful handoff requires a consistent
runner exit code and result, all six LUNs in the real execution plan, the
expected executable and input hashes, preserved originals, and the runner's
Android boot-completion evidence. A kernel failure remains a failed boot even
when input and overlay checks passed.

All capture qualifications remain visible: complete byte coverage does not
make live storage an atomic snapshot, establish pristine factory provenance,
verify AVB, or prove a restore. The machine enters the supplied kernel directly;
it does not execute the OEM PBL/XBL/ABL boot chain. These limitations also apply
after Android userspace is observed.

The dedicated CI workflow uses small public fixtures to test this handoff and
its rejection paths. Its success is independent of the private original-device
boot result.
