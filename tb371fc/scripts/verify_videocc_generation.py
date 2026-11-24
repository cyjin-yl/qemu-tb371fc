#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reproduce the two bounded VIDEOCC tables from hash-pinned primary inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import traceback
import urllib.request

import generate_videocc_sm8250 as generator

ROOT = Path(__file__).resolve().parents[2]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"schema": "tb371fc-videocc-generation-v1", "passed": False,
              "source_pins_verified": False, "inputs": [], "checks": []}
    try:
        inputs = out / "inputs"
        for relative, expected in generator.SOURCE_HASHES.items():
            url = ("https://raw.githubusercontent.com/OnePlusOSS/"
                   "android_kernel_oneplus_sm8250/" + generator.REVISION +
                   "/" + relative)
            if args.source_root is None:
                with urllib.request.urlopen(url, timeout=30) as response:
                    content = response.read(512 * 1024 + 1)
            else:
                content = (args.source_root / relative).read_bytes()
            if len(content) > 512 * 1024:
                raise ValueError("Public VIDEOCC input exceeds its size bound")
            actual = digest(content)
            report["inputs"].append({"url": url, "sha256": actual,
                                     "expected_sha256": expected,
                                     "bytes": len(content)})
            if actual != expected:
                raise ValueError("Public source hash differs: " + relative)
            path = inputs / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        report["source_pins_verified"] = True
        generated = out / "generated"
        command = [sys.executable, str(Path(generator.__file__).resolve()),
                   str(inputs), "--output-root", str(generated)]
        process = subprocess.run(command, capture_output=True, text=True,
                                 timeout=60)
        (out / "generation.stdout").write_text(process.stdout)
        (out / "generation.stderr").write_text(process.stderr)
        report["generator_exit"] = process.returncode
        if process.returncode:
            raise RuntimeError("Pinned VIDEOCC regeneration failed")
        for relative in generator.OUTPUTS:
            actual = (generated / relative).read_bytes()
            expected = (ROOT / relative).read_bytes()
            report["checks"].append({
                "name": relative, "passed": actual == expected,
                "generated_sha256": digest(actual),
                "committed_sha256": digest(expected)})
        report["passed"] = (len(report["checks"]) == len(generator.OUTPUTS) and
                            all(item["passed"] for item in report["checks"]))
    except Exception:
        report["error"] = traceback.format_exc()
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]),
                      "out": str(out)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-root", type=Path,
                        help="Use already downloaded bytes, still checking every pin")
    raise SystemExit(run(parser.parse_args()))
