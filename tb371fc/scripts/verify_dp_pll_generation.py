#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reproduce both DP PLL tables from seventeen complete hash-pinned inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import traceback
from source_download import read_source

import generate_dp_pll_7nm as generator

ROOT = Path(__file__).resolve().parents[2]
MAX_INPUT_BYTES = 512 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"schema": "tb371fc-dp-pll-generation-v1", "passed": False,
              "source_revision": generator.REVISION,
              "source_pins_verified": False, "inputs": [], "checks": []}
    try:
        inputs = out / "inputs"
        for relative, expected in generator.SOURCE_HASHES.items():
            url = (f"https://raw.githubusercontent.com/{generator.REPOSITORY}/"
                   f"{generator.REVISION}/{relative}")
            if args.source_root is None:
                raw = read_source(url, max_bytes=MAX_INPUT_BYTES)
            else:
                raw = (args.source_root / relative).read_bytes()
            if len(raw) > MAX_INPUT_BYTES:
                raise ValueError("Public DP PLL input exceeds its size bound: " + relative)
            actual = digest(raw)
            report["inputs"].append({
                "path": relative, "url": url, "sha256": actual,
                "expected_sha256": expected, "bytes": len(raw),
                "git_blob_sha": hashlib.sha1(
                    b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()})
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
            raise RuntimeError("Pinned DP PLL regeneration failed")
        for relative in generator.OUTPUTS:
            actual = (generated / relative).read_bytes()
            committed = (ROOT / relative).read_bytes()
            report["checks"].append({
                "name": relative, "passed": actual == committed,
                "generated_bytes": len(actual), "committed_bytes": len(committed),
                "generated_sha256": digest(actual), "committed_sha256": digest(committed)})
        manifest = json.loads((generated / generator.OUTPUTS[1]).read_text())
        keys = ("source_input_count", "register_count", "required_register_count",
                "window_count", "mapped_bytes", "clock_count")
        report["generated_contract"] = {key: manifest[key] for key in keys}
        expected = dict(zip(keys, (17, 73, 58, 42, 292, 6)))
        report["passed"] = (
            len(report["inputs"]) == len(generator.SOURCE_HASHES) == 17 and
            len(report["checks"]) == len(generator.OUTPUTS) == 2 and
            all(item["passed"] for item in report["checks"]) and
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
                        help="Use local primary bytes, checking every source pin")
    raise SystemExit(run(parser.parse_args()))
