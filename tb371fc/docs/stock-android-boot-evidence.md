# Offline stock Android evidence contract

`scripts/verify_stock_android_boot_evidence.py` checks local artifacts from one
already collected Android framework run. It uses only Python's standard library,
reads ordinary files, and never executes QEMU, an image, a guest command, or a
device operation. Run it from any directory:

```sh
python3 tb371fc/scripts/verify_stock_android_boot_evidence.py /bundle/manifest.json
python3 -m unittest discover -s tb371fc/tests -p 'test_stock_android_boot_evidence.py' -v
```

Exit 0 means the supplied artifacts are internally consistent with this contract.
Exit 1 means missing, malformed, or inconsistent evidence. JSON output always has
`scope: offline-artifact-consistency`, `stock_android_boot_pass: false`, and
`native_pass: false`. OEM boot-chain, display, and input results remain
`not-evaluated`. A successful check adds **no stock or native boot pass**.

Synthetic bundles are rejected by default. `--allow-synthetic` is for unit tests;
their only successful status is `synthetic-consistent`, and their run ID must
start with `synthetic-`. The tests create conspicuously labelled placeholder
bytes in temporary directories. They contain no real firmware or successful
guest capture. Non-synthetic consistency is not source authenticity or runtime
attestation: a producer could fabricate mutually consistent artifacts.

## Bundle and manifest

All artifact references are `{ "path": "relative/path", "sha256": "..." }`.
SHA-256 is lowercase hex and covers the exact file bytes. Paths must stay inside
the manifest directory; absolute paths, `..`, symlinks (including parent
directories), and non-regular files are rejected. JSON/JSONL is bounded to
16 MiB per file. Hashing and parsing use the same captured bytes. Large binary
inputs are hashed in chunks with file identity/change checks around the read.
Verify a frozen capture, including frozen writable images, rather than files
that another process is updating.

The manifest requires these fields:

| Field | Meaning |
| --- | --- |
| `schema_version`, `kind` | `1`, `tb371fc-stock-android-boot-evidence` |
| `synthetic` | Explicit boolean; also present in inventory and every capture record |
| `run_id`, `boot_id` | Collector run identifier and canonical guest `/proc/sys/kernel/random/boot_id` UUID |
| `scope`, `evidence_kind` | `android-framework`, `guest-runtime` |
| `entry_path` | `direct-kernel` or `bootloader`; neither certifies an OEM boot chain |
| `slot` | `a`, `b`, or `none` |
| `kernel_cmdline` | Captured guest `/proc/cmdline`, also present in the properties record |
| `inventory`, `capture` | Artifact references to the original-input inventory and capture JSONL |
| `inputs` | Selected original inputs, as described below; other backup partitions are not implicitly used |
| `userdata` | Explicit data-source declaration, as described below |
| `qemu` | Full `source_commit`, boolean `source_dirty`, binary artifact, and actual `argv` array |

For dirty QEMU source, `qemu.source_diff` is a required hashed artifact covering
the changes used by the build. For clean source it is absent or null. The source
and binary association is the producer's captured build declaration; this tool
does not rebuild QEMU or independently establish that association. `argv` is the
actual argument vector, not a shell string, and is never evaluated.

## Original input identity, slots, and storage declarations

The inventory has `kind: tb371fc-stock-input-inventory`, `synthetic`, `backup_id`,
and an `inputs` object indexed by input ID. Each selected inventory entry records
`role`, `slot`, `sha256`, and either `partition` or a whole-LUN `lun` number.
Prepare it from the original backup;
kernel/ramdisk entries identify the original extracted component bytes and their
source partition. Its authenticity and the correctness of extraction remain
outside this checker. Unselected inventory entries do not imply use in the run.

Each manifest input has unique `id` and `role`, matching inventory identity and
`slot`, a `base` artifact, `base_read_only: true`, and explicit `overlay` (null or
an artifact). The base hash must equal the inventory hash. An input's slot is
the selected slot or `shared`; `_a`/`_b` partition suffixes must agree. An overlay
also records `run_id` and `base_sha256`, and must be a distinct file from its base.
Kernel, ramdisk, and DTB inputs cannot be overlays.

Original `kernel` and `ramdisk` are required. System/vendor coverage can be
separate inputs or a single `super` input with `logical_partitions`, for example:

```json
[
  {"role": "system", "name": "system_a", "slot": "a"},
  {"role": "vendor", "name": "vendor_a", "slot": "a"}
]
```

This declaration must match the hashed inventory and capture summary; each
logical slot must match the selected slot. The physical `super` input can be
`shared`. Separate and logical roles cannot overlap. This supports the original
whole-super backend without extracting system/vendor images. The checker does
**not** parse LP metadata, GPT, filesystems, or overlay headers, and does not prove
that the declared logical layout is correct. AOSP documents that super contains
dynamic partitions and handles A/B metadata internally:
[Implement dynamic partitions](https://source.android.com/docs/core/ota/dynamic_partitions/implement).

Complete raw LUN backends use `role: lunN`, integer `lun: N` (nonnegative and
unique), `slot: shared`, and **no `partition` field**. Their inventory entries
use the same identity. A `contained_partitions` array, identical in inventory,
manifest and capture, declares coverage for the **selected boot slot**, not a
complete GPT/LP inventory. Each entry has `role`, `gpt_partition`, optional
`logical_partition`, and `slot`. System/vendor entries must use the selected
slot; userdata may use `shared`. An empty array is valid for a LUN providing no
such coverage. Duplicate or overlapping roles, LUN numbers, and contradictory
slot suffixes are rejected. This represents original whole LUNs and their
overlays without manufacturing extracted partition images. The declaration
does not prove where partitions actually lie or whether GPT/LP metadata is valid.

`userdata.mode` is one of:

- `omitted`: no original userdata input or contained userdata is selected.
- `fresh-image`: a private `image` artifact and matching `run_id`; it cannot
  alias any original base file.
- `original-read-only`: an original input with role `userdata`, without overlay.
- `private-overlay`: an original userdata input with a run-bound overlay.

For contained userdata, `original-read-only` and `private-overlay` require
`userdata.input_id` to select the LUN declaring it. Its existing base and overlay
checks apply to that entire LUN; no separate userdata file is required.

Read-only bases and private overlays are **declarations compared with the
captured configuration**, not proof that QEMU enforced write protection at
runtime. The checker does not parse arbitrary QEMU block arguments or authorize
another boot. It only reads the frozen local files supplied to it.

## Capture JSONL

Exactly three records occur in this order: `launch`, `processes`, `properties`.
Every record has the same `run_id`, `boot_id`, and boolean `synthetic`.

The `launch` record is a **post-collection configuration summary**, not a claim
that the guest boot ID or final overlay hash was known at launch time. It repeats
the manifest's `qemu`, `slot`, `entry_path`, `inputs`, `userdata`, and `inventory`
objects exactly. Preserve the actual source/argv/configuration from original
launch records when preparing this summary; do not rewrite those original logs
to fit the schema. Writable overlay hashes identify their **frozen evidence
state after collection**, and need not equal their hashes before the run.

The `processes` record has `probe: /proc`, integer `exit_code: 0`, positive guest
`uptime_ns`, and a `processes` list of `pid`, `ppid`, `exe`, and `cmdline` arrays.
The snapshot must show PID 1 init (`/init` or `/system/bin/init`), at least one
init-spawned zygote (`zygote`, `zygote32`, or `zygote64` using app_process), and a
system_server child of one observed zygote. A dual ABI system can have multiple
zygotes. These are values to capture, not PIDs to hardcode in a collector.
[AOSP Zygote documentation](https://source.android.com/docs/core/runtime/zygote)
describes init spawning zygote and the possible 32-/64-bit pair.

The `properties` record contains `command: ["getprop"]` (or
`["/system/bin/getprop"]`), integer `exit_code: 0`, captured `stdout`,
`kernel_cmdline`, and guest `uptime_ns` no earlier than the process snapshot.
Its stdout must be the command's property listing (`[name]: [value]` lines),
with `sys.boot_completed` equal to `1` and `ro.boot.slot_suffix` agreeing with
the selected slot. Duplicate properties are rejected. A console line mentioning
boot completion, a failed command, CI success, a kernel init-exec message, or the
synthetic native8 RAM record cannot satisfy this contract.

The collector must obtain the guest boot ID from the guest around these
observations and keep only one boot per bundle. Matching self-reported IDs
cannot establish that capture actually happened. Android 12's
[ActivityManagerService.finishBooting](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android12-release/services/core/java/com/android/server/am/ActivityManagerService.java)
sets `sys.boot_completed` after framework boot-phase handling; this property is
a framework milestone. Direct-kernel framework evidence, the complete OEM
ROM/bootloader chain, and working display/input require separate conclusions.
