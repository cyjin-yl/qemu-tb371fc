import hashlib, json, os, stat
from pathlib import Path
root = Path("/work")
entries = [["smem-observed-slot-20261123-01/install-receipt.json",32768],["smem-observed-slot-20261123-01/job.result.json",32768],["smem-observed-slot-20261123-01/job.exit",16],["smem-observed-slot-20261123-01/results/fixtures.stdout",16384],["smem-observed-slot-20261123-01/results/fixtures.stderr",16384],["smem-observed-slot-20261123-01/results/scan.stdout",12288],["smem-observed-slot-20261123-01/results/scan.stderr",16384],["android-boot-header-20261123-01/result.json",32768],["android-boot-header-20261123-01/job.exit",16],["android-boot-header-20261123-01/installation.json",32768],["android-boot-header-20261123-01/fixtures.stderr",16384],["android-boot-header-20261123-01/fixtures.stdout",16384]]
rows = []
for name, limit in entries:
    path = root / name
    row = {"name": name, "source_path": str(path)}
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        row["missing"] = True
        rows.append(row)
        continue
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise RuntimeError("receipt_type_or_size")
        raw = bytearray()
        while len(raw) < limit + 1:
            part = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
            before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size,
                                   after.st_mtime_ns, after.st_ctime_ns):
            raise RuntimeError("receipt_changed_during_read")
        if len(raw) != before.st_size:
            raise RuntimeError("receipt_read_length")
        value = bytes(raw)
        row.update(bytes=len(value), sha256=hashlib.sha256(value).hexdigest(),
                   content=value.decode("utf-8", errors="strict"))
    finally:
        os.close(fd)
    rows.append(row)
print(json.dumps({"schema": "tb371fc-smem-observed-slot-and-boot-header-original-readback-v1",
                  "files": rows, "read_only": True, "job_launched": False},
                 ensure_ascii=True, sort_keys=True, separators=(",", ":")))
