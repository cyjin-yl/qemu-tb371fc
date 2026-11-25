# Public CI log recovery for DDR offset source 7d01e5e

This evidence branch preserves four already-public GitHub Actions job logs for source commit `7d01e5e294b788f97a8120c2d34122ddb25e6343`. The push jobs checked out that source commit; the PR 38 jobs checked out synthetic merge `da1fd3e190faeb5e2cf4dfb3634c54c18987fae8`, whose source tree was `5c25fff35bb41c3a8b018f0be965b4a9d3e9d5c4`.

The execution environment was lost after the original log observations. These four terminal job logs were fetched again, and every complete UTF-8 byte count and SHA-256 matched the values recorded before the interruption. This recovery contains the public logs, their chunk manifest, and this README. It makes no new CI qualification or native/Android outcome claim. Missing prior observer receipt bytes are not reconstructed or represented as restored here.

## Exact byte restoration

`manifest.json` lists each log's chunks in order, with full repository paths, byte offsets, byte counts and SHA-256 hashes. Each chunk contains at most 120,000 Unicode characters. Concatenate the listed files as bytes with no separators or added newlines. The original UTF-8 BOM and all original line endings are preserved. Do not normalize or decode/re-encode the files while joining them.

From the repository root, this example validates every chunk and complete log:

```python
import hashlib
import json
from pathlib import Path

manifest = json.loads(Path("tb371fc/evidence/ddr-stats-offset-7d01e5e-log-recovery-20261125-6a5/manifest.json").read_text())
for log in manifest["logs"]:
    pieces = []
    for chunk in log["chunks"]:
        data = Path(chunk["path"]).read_bytes()
        assert len(data) == chunk["bytes"]
        assert hashlib.sha256(data).hexdigest() == chunk["sha256"]
        pieces.append(data)
    raw = b"".join(pieces)
    assert len(raw) == log["original_bytes"]
    assert hashlib.sha256(raw).hexdigest() == log["original_sha256"]
    Path("restored-" + log["role"] + ".log").write_bytes(raw)
```

Before publication, all four complete logs were scanned for unmasked credential formats, private key headers, authorization values, credential assignments, URL user/password credentials and signed or credential query parameters. No findings were detected. Existing GitHub credential masks remain unchanged. No original firmware, private native logs, host control files or artifact download redirects are included.
