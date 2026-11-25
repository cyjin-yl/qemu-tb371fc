#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify two pinned inputs and byte-compare both generated SPSS mask outputs."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request

import gen_spss_irq_mask as generator

ROOT = Path(__file__).resolve().parents[2]
MAX_INPUT_BYTES = 256 * 1024


def fetch_input(url, attempts):
    """Retry only transient HTTP/timeout failures; never retry identity failures."""
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                raw = response.read(MAX_INPUT_BYTES + 1)
            attempts.append({"attempt": attempt, "success": True, "bytes": len(raw)})
            return raw
        except (urllib.error.URLError, TimeoutError) as error:
            status = error.code if isinstance(error, urllib.error.HTTPError) else None
            reason = getattr(error, "reason", error)
            retryable = (status == 429 or status is not None and 500 <= status <= 599 or
                         isinstance(error, TimeoutError) or isinstance(reason, TimeoutError))
            retry = retryable and attempt < 3
            attempts.append({"attempt": attempt, "success": False,
                             "error_type": type(error).__name__, "error": str(error),
                             "http_status": status, "retryable": retryable,
                             "retry_delay_seconds": attempt if retry else 0})
            if not retry:
                raise
            time.sleep(attempt)
    raise RuntimeError("Unreachable input retry state")


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"schema": "tb371fc-spss-irq-mask-generation-v1", "passed": False,
              "source_revision": generator.REVISION, "source_pins_verified": False,
              "inputs": [], "checks": []}
    try:
        inputs = out / "inputs"
        for prefix, repository, revision, relative, size, blob, expected in generator.SOURCE_PINS:
            local = prefix + "/" + relative
            url = f"https://raw.githubusercontent.com/{repository}/{revision}/{relative}"
            item = {"path": local, "source_path": relative, "repository": repository,
                    "revision": revision, "url": url, "expected_bytes": size,
                    "expected_sha256": expected, "expected_git_blob_sha": blob, "attempts": []}
            report["inputs"].append(item)
            if args.source_root is None:
                raw = fetch_input(url, item["attempts"])
            else:
                raw = (args.source_root / local).read_bytes()
                item["attempts"].append({"attempt": 1, "success": True,
                                         "source": "local", "bytes": len(raw)})
            # Deliberately outside the fetch retry loop: a bad source is final.
            item.update({"bytes": len(raw), "sha256": generator.digest(raw),
                         "git_blob_sha": generator.git_blob(raw)})
            if (len(raw) > MAX_INPUT_BYTES or
                    (len(raw), item["sha256"], item["git_blob_sha"]) != (size, expected, blob)):
                raise ValueError("Complete public source identity differs: " + local)
            path = inputs / local
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        report["source_pins_verified"] = True
        generated = out / "generated"
        command = [sys.executable, "-B", str(Path(generator.__file__).resolve()),
                   str(inputs), "--output-root", str(generated)]
        process = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (out / "generation.stdout").write_text(process.stdout, encoding="utf-8")
        (out / "generation.stderr").write_text(process.stderr, encoding="utf-8")
        report["generator_exit"] = process.returncode
        if process.returncode:
            raise RuntimeError("Pinned SPSS mask regeneration failed")
        for relative in generator.OUTPUTS:
            actual, committed = (generated / relative).read_bytes(), (ROOT / relative).read_bytes()
            report["checks"].append({"name": relative, "passed": actual == committed,
                "generated_bytes": len(actual), "committed_bytes": len(committed),
                "generated_sha256": generator.digest(actual),
                "committed_sha256": generator.digest(committed)})
        manifest = json.loads((generated / generator.OUTPUTS[1]).read_text())
        expected = {"source_input_count": 2, "register_count": 1, "window_count": 1,
                    "mapped_bytes": 4, "physical_base": 0x01881028,
                    "err_ready_bit": 24, "pbl_done_bit": 25, "driver_mask": 0xfcffffff}
        report["generated_contract"] = {key: manifest[key] for key in expected}
        report["passed"] = (len(report["inputs"]) == len(generator.SOURCE_PINS) == 2 and
            len(report["checks"]) == len(generator.OUTPUTS) == 2 and
            all(item["passed"] is True for item in report["checks"]) and
            report["generated_contract"] == expected)
    except Exception:
        report["error"] = traceback.format_exc()
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "inputs": len(report["inputs"]),
                      "checks": len(report["checks"]), "out": str(out)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-root", type=Path,
                        help="Local primary-r11/ directory; complete identities are still verified")
    raise SystemExit(run(parser.parse_args()))
