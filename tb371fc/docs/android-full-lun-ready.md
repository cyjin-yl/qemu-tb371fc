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
