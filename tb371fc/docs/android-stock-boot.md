# Original Android input runs

`android_boot_inputs.py` and `boot_android.py` provide a separate path for
observing original Android boot inputs. They do not use the smoke-test initramfs.
The current machine enters a Linux kernel directly; it does not execute the
Qualcomm PBL, XBL, ABL, AOP or secure-world firmware.

## Capture missing original ranges

`acquire_block_ranges.py` streams explicit read-only block-device ranges to the
same script's `receive` command over an existing, verified SSH connection. Keep
the SSH prefix and remote receiver argv in separate private JSON files. Preserve
host-key verification and use the account's existing identity environment.

The sender requires the observed device byte count, 4096-byte block geometry,
explicit offsets/lengths and `immutable` or `live-mutable` classification. It
does not write, freeze, unmount or repair the source. `--gpt` can capture both
actual GPT copies with CRC and byte-identity checks. A partition range is not
an entire LUN.

Use 64 or 256 MiB chunks. Data flows through bounded memory directly to the
receiver, without creating large intermediate files on the tablet. The receiver
checks compressed and raw hashes and lengths, and the sender independently
reads each source range before, during and after transmission. Each accepted
chunk produces a progress line. Receipts and the final manifest are published
only after verification and filesystem synchronization.

For an interrupted run, add `--resume` to the receiver argv and preserve the
same source, ranges and geometry. Both sides revalidate completed chunks before
skipping them. Keep the SSH idle timeout long enough for that validation.
Use an exclusively owned temporary directory for interrupted files; resume
only removes files recognized as its own uncommitted output.

The resulting manifest always records `snapshot_consistent=false` and does
not authenticate factory origin. `metadata` and `userdata` are live mutable
inputs. Stable per-chunk rereads do not make a running filesystem an atomic
snapshot. Only a complete, nonoverlapping set of original ranges can supply a
full LUN image for the Android runner.

## Assemble complete raw LUNs

`assemble_lun.py` consumes completed range-capture manifests and their chunks.
It requires matching source identity and geometry, and checks both the original
ranges and chunk layout for exact, nonoverlapping coverage of the whole LUN.
It verifies compressed and decoded hashes/lengths before copying each chunk,
then reads back the entire raw output. A super-only capture cannot pass this
check. Do not also supply separate GPT captures when those bytes are already
included in the full-LUN ranges.

The retained 2026-11-24 read-only GPT inventory confirms this device mapping;
its capacities agree with the [public native unit descriptors][native-units].
This table records observed geometry, not completed captures or restore tests.
Recheck it against the selected capture manifests before using another device
or inventory. All six ordinary LUs use 4096-byte logical blocks.

| LU | Recorded source | Logical blocks | Full raw bytes |
| --- | --- | ---: | ---: |
| 0 | `/dev/block/sda` | 30693376 | 125720068096 |
| 1 | `/dev/block/sdb` | 2048 | 8388608 |
| 2 | `/dev/block/sdc` | 2048 | 8388608 |
| 3 | `/dev/block/sdd` | 8192 | 33554432 |
| 4 | `/dev/block/sde` | 524288 | 2147483648 |
| 5 | `/dev/block/sdf` | 8192 | 33554432 |

Set `TB371FC_SUPER_CAPTURE` and `TB371FC_LU0_REMAINDER_CAPTURE` to the actual
receiver directories. The latter must cover both `[0, 35684352)` and
`[12920586240, 125720068096)`; super covers the intervening 12884901888 bytes.
If prefix and suffix have separate manifests, pass each with `--manifest`.
Set `TB371FC_CAPTURE_ROOT` to a private directory whose `lun1` through `lun5`
subdirectories hold the corresponding complete captures; adjust these paths
explicitly if the real layout differs. Choose a new `TB371FC_RAW_DIR`:

```bash
set -euo pipefail
mkdir -m 700 -- "$TB371FC_RAW_DIR"
python3 tb371fc/scripts/assemble_lun.py \
  --manifest "$TB371FC_SUPER_CAPTURE/manifest.json" \
  --manifest "$TB371FC_LU0_REMAINDER_CAPTURE/manifest.json" \
  --source /dev/block/sda --source-bytes 125720068096 \
  --out "$TB371FC_RAW_DIR/lun0.raw"
while read -r lun source bytes; do
  python3 tb371fc/scripts/assemble_lun.py \
    --manifest "$TB371FC_CAPTURE_ROOT/lun$lun/manifest.json" \
    --source "$source" --source-bytes "$bytes" \
    --out "$TB371FC_RAW_DIR/lun$lun.raw"
done <<'LUNS'
1 /dev/block/sdb 8388608
2 /dev/block/sdc 8388608
3 /dev/block/sdd 33554432
4 /dev/block/sde 2147483648
5 /dev/block/sdf 33554432
LUNS
```

Run these sequentially with no other writers in the output directory. Each
completed raw has a `lunN.raw.manifest.json` receipt recording its full SHA-256
and source manifest hashes. Per-chunk progress goes to stderr; the final whole
raw readback also has to finish. Linux `renameat2(RENAME_NOREPLACE)` publishes
without deleting or overwriting files. Failed work is retained; an interrupted
publication can leave a raw without its completed receipt. Keep both the raw
and receipt, and do not treat the raw filename alone as completion. The result
retains `snapshot_consistent=false` and `pristine_factory=false`, including for
live-mutable metadata/userdata ranges.

## Prepare private inputs

There is no `--lun` option in `android_boot_inputs.py`. Pass one storage JSON
with `--storage-manifest`. The following metadata-only template checks the six
assembly receipts against the table, carries their full raw digests into that
JSON, and refuses an existing output file. Set `TB371FC_STORAGE_MANIFEST` to a
new private path; keep the original assembly receipts and capture manifests.
The `_a` value below records the observed slot for this capture; it does not
select a slot or verify that the supplied boot image and bootargs match it.

```bash
python3 - "$TB371FC_RAW_DIR" "$TB371FC_STORAGE_MANIFEST" <<'PY'
import hashlib, json, os, re, sys
from pathlib import Path

root = Path(sys.argv[1]).resolve(strict=True)
expected = [(0, "/dev/block/sda", 125720068096),
            (1, "/dev/block/sdb", 8388608),
            (2, "/dev/block/sdc", 8388608),
            (3, "/dev/block/sdd", 33554432),
            (4, "/dev/block/sde", 2147483648),
            (5, "/dev/block/sdf", 33554432)]
luns, provenance = [], []
for lun, source, size in expected:
    path = root / f"lun{lun}.raw"
    receipt_path = root / f"lun{lun}.raw.manifest.json"
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    raw, recorded = receipt["raw"], receipt["source"]
    if not (receipt.get("schema") == "tb371fc-whole-lun-assembly-v1"
            and receipt.get("status") == "verified-whole-lun-assembly"
            and receipt.get("whole_source_bytes_captured") is True
            and receipt.get("coverage_nonoverlapping") is True
            and receipt.get("raw_readback_verified") is True
            and receipt.get("snapshot_consistent") is False
            and receipt.get("pristine_factory") is False
            and receipt.get("pristine_factory_images") is False
            and recorded.get("kind") == "block-device"
            and recorded.get("path") == source and recorded.get("bytes") == size
            and recorded.get("logical_block_size") == 4096
            and raw.get("format") == "raw" and raw.get("bytes") == size
            and raw.get("path") == str(path.resolve(strict=True))
            and path.is_file() and not path.is_symlink() and path.stat().st_size == size
            and re.fullmatch(r"[0-9a-f]{64}", raw.get("sha256", ""))):
        raise SystemExit(f"LU {lun}: receipt, identity or geometry mismatch")
    count = size // 4096
    luns.append({"lun": lun, "path": str(path), "format": "raw", "size": size,
                 "readonly_original": True, "logical_block_size": 4096,
                 "logical_block_count": count, "sha256": raw["sha256"],
                 "descriptor": {"logical_block_size_exponent": 12,
                                "logical_block_count": count}})
    provenance.append({"lun": lun, "path": str(receipt_path),
                       "sha256": hashlib.sha256(receipt_bytes).hexdigest()})
storage = {"schema": "tb371fc-android-storage-v1", "slot": "_a", "luns": luns,
           "assembly_manifests": provenance, "snapshot_consistent": False,
           "pristine_factory": False}
fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    json.dump(storage, stream, indent=2)
    stream.write("\n")
print("Prepared storage metadata for six observed LUs; no boot was executed")
PY
```

Keep device images and private reports outside the source repository. Use a new
output directory for every preparation and run. This next step verifies the
supplied raw SHA-256 values and therefore reads every complete LUN:

```sh
python3 tb371fc/scripts/android_boot_inputs.py \
  --boot-image /private/boot_a.img \
  --dtb /private/full-live-device-tree.dtb \
  --storage-manifest "$TB371FC_STORAGE_MANIFEST" \
  --hash-luns \
  --out /private/prepared-unique
```

The preparation tool validates Android boot image versions 0, 1 and 2 and
extracts the kernel and ramdisk bytes without rebuilding them. The full DT is
copied byte for byte. Its `/chosen/bootargs` is retained; an explicit
`--bootargs-file` is required when that property is absent. Boot-header command
lines are recorded separately and are not substituted for the actual bootloader
arguments.

Structurally bounded nonzero DT alignment padding is retained and recorded as
`format_compliant=false`. This permits a faithful diagnostic run of the captured
input without representing it as a conforming or authenticated factory image.

The storage manifest must identify complete raw LUN files and their observed
geometry. It does not infer partitions or manufacture missing media:
use the measured descriptor and file length. Existing capture hashes and
provenance must accompany the private inputs; hashing a file now does not
authenticate its factory origin or make a live filesystem capture consistent.
`--hash-luns` verifies digests already supplied in each LUN entry; it does not
compute or insert missing digests. The prepare tool and runner permit other
unique LUN sets and geometries, so the six-LU completeness check above is an
explicit workflow requirement. They do not interpret assembly receipts or the
storage JSON's additional qualification fields. Preparation records the storage
JSON's path, length and hash as provenance; retain that JSON and its referenced
receipts to preserve the non-snapshot/non-factory qualifications.

## Run and observe

Pass the exact reviewed machine string and globals for the binary under test:

```sh
python3 tb371fc/scripts/boot_android.py \
  --manifest /private/prepared-unique/manifest.json \
  --qemu /private/verified/qemu-system-aarch64 \
  --qemu-img /usr/bin/qemu-img \
  --machine "$REVIEWED_MACHINE" \
  --configuration-evidence /private/reviewed-machine.json \
  --out /private/run-unique \
  --seconds 120
```

Add each reviewed device property with `--global-property`. Profiles are
explicit reference contracts; they do not establish the physical device's
register values or secure services. In particular, the two TBU providers cannot
be enabled together. Input-dependent machine resources such as the AOP-derived
command database need their own before/after hash checks.

Each LUN gets a fresh qcow2 overlay. Both the original file and its raw backing
node are read-only. Before starting CPUs, the runner checks the live QMP block
graph. It preserves the original kernel, ramdisk, full DT and command line and
rechecks those four input hashes after the bounded observation. LUNs receive
before/after file-identity checks, not another full raw digest readback after
the run (`full_base_digest_rechecked_after_run=false`). `--dry-run` writes a
validated plan without creating overlays or starting QEMU.

For Linux 4.x, `--legacy-printk-symbols` can read a stopped guest's printk ring.
Bind it with `--symbols-kernel-sha256`, and supply symbols with the actual runtime
virtual addresses. The runner does not add `nokaslr`; an address mismatch is a
capture error, not proof that the kernel has no failures.

Every requested printk capture must succeed for a successful boot observation.
Symbol parsing, virtual-memory reads, or ring decoding can fail while QMP stays
healthy; those failures remain in the snapshot receipts even if a later capture
succeeds. The runner continues its bounded observation and normal shutdown, but
reports `diagnostic-failure` with exit 1 when no higher-priority input, kernel or
runner failure applies. Serial boot-completion messages cannot override a failed
capture.

For faster repeated diagnostics, add `--stop-on-kernel-failure` together with
`--legacy-printk-symbols /private/runtime-symbols.txt` and
`--symbols-kernel-sha256 "$KERNEL_SHA256"`. This option is off by default; without
it the observation time remains `--seconds 120` unless explicitly changed.
After a successful scheduled stopped-guest printk capture (every 15 seconds by
default, controlled by `--snapshot-interval`), the existing log classifier can
end observation when it identifies an Internal error/Oops/panic or another
recognized kernel failure. WARN messages, hardware diagnostics alone, and failed
captures do not trigger this early stop. The guest stays paused while the runner
takes its normal final capture, quits QEMU, drains QMP, collects logs, and checks
the original inputs and backing files. A normally finalized early stop records
`stop_reason` and `outcome` as `kernel-failure`; preservation and runner errors
retain their existing failure handling. An early stop never claims a successful
boot. This changes feedback speed only; it preserves boot arguments, read-only
backing protection, and the existing success criteria.

## Read the result conservatively

Exit 0 from a dry-run means that input planning succeeded. A real run requires
Android second-stage and actual boot-completed action evidence, no observed
kernel failure, stable inputs, verified backing-node protection, and a clean
host-controlled stop. Unexpected QMP closure, process exit, guest panic or
shutdown, host termination, log truncation, and a run without original LUNs all
prevent a successful boot observation.

Even a successful boot observation does not attest AVB, secure-world behavior,
factory provenance, usable graphics, or complete-device equivalence. Preserve
the detailed stage evidence and qualify those capabilities separately. The
tools' host tests use synthetic inputs and fake process/QMP objects; CI passing
those tests is not an Android boot result.

[native-units]: ../evidence/expansion-20261123/android-ufs-units.json
