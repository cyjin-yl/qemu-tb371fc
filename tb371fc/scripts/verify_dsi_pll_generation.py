#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reproduce both DSI PLL v4.1 tables from thirteen hash-pinned inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import traceback
import urllib.request

import generate_dsi_pll_7nm as generator

ROOT = Path(__file__).resolve().parents[2]
MAX_INPUT_BYTES = 512 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"schema": "tb371fc-dsi-pll-generation-v1", "passed": False,
              "source_revision": generator.REVISION,
              "source_pins_verified": False, "inputs": [], "checks": []}
    try:
        inputs = out / "inputs"
        for relative, expected in generator.SOURCE_HASHES.items():
            url = ("https://raw.githubusercontent.com/" + generator.REPOSITORY + "/" +
                   generator.REVISION + "/" + relative)
            if args.source_root is None:
                with urllib.request.urlopen(url, timeout=30) as response:
                    raw = response.read(MAX_INPUT_BYTES + 1)
            else:
                raw = (args.source_root / relative).read_bytes()
            if len(raw) > MAX_INPUT_BYTES:
                raise ValueError("Public DSI PLL input exceeds its size bound: " + relative)
            actual = digest(raw)
            report["inputs"].append({"path": relative, "url": url, "sha256": actual,
                                     "expected_sha256": expected, "bytes": len(raw)})
            if actual != expected:
                raise ValueError("Public source hash differs: " + relative)
            path = inputs / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        report["source_pins_verified"] = True
        generated = out / "generated"
        command = [sys.executable, str(Path(generator.__file__).resolve()),
                   str(inputs), "--output-root", str(generated)]
        process = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (out / "generation.stdout").write_text(process.stdout, encoding="utf-8")
        (out / "generation.stderr").write_text(process.stderr, encoding="utf-8")
        report["generator_exit"] = process.returncode
        if process.returncode:
            raise RuntimeError("Pinned DSI PLL regeneration failed")
        for relative in generator.OUTPUTS:
            actual = (generated / relative).read_bytes()
            committed = (ROOT / relative).read_bytes()
            report["checks"].append({"name": relative, "passed": actual == committed,
                                     "generated_bytes": len(actual),
                                     "committed_bytes": len(committed),
                                     "generated_sha256": digest(actual),
                                     "committed_sha256": digest(committed)})
        report["passed"] = (len(report["inputs"]) == len(generator.SOURCE_HASHES) == 13
                            and len(report["checks"]) == len(generator.OUTPUTS) == 2
                            and all(item["passed"] for item in report["checks"]))
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
                        help="Use local primary bytes, still checking every source pin")
    raise SystemExit(run(parser.parse_args()))
