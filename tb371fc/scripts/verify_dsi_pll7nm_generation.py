#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify 13 pinned primary inputs and reproduce both DSI v4.1 tables."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import traceback
import urllib.request

import generate_dsi_pll7nm_sm8250 as generator

ROOT = Path(__file__).resolve().parents[2]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"schema": "tb371fc-dsi-pll7nm-generation-v1", "passed": False,
              "source_pins_verified": False, "inputs": [], "checks": []}
    try:
        pins = json.loads(generator.PINS.read_text())
        inputs = out / "inputs"
        for group, local in (("module", args.module_root), ("core", args.core_root)):
            info = pins[group]
            for relative, pin in info["files"].items():
                url = f'https://raw.githubusercontent.com/{info["repository"]}/{info["revision"]}/{relative}'
                if local is None:
                    with urllib.request.urlopen(url, timeout=30) as response:
                        content = response.read(512 * 1024 + 1)
                else:
                    content = (local / relative).read_bytes()
                if len(content) > 512 * 1024:
                    raise ValueError("Public DSI source exceeds its size bound")
                actual = digest(content)
                blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
                report["inputs"].append({"url": url, "sha256": actual,
                    "expected_sha256": pin["sha256"], "git_blob": blob, "bytes": len(content)})
                if actual != pin["sha256"] or blob != pin["git_blob"] or len(content) != pin["bytes"]:
                    raise ValueError("Public source pin differs: " + relative)
                path = inputs / group / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        report["source_pins_verified"] = True
        generated = out / "generated"
        command = [sys.executable, str(Path(generator.__file__).resolve()),
                   "--module-root", str(inputs / "module"),
                   "--core-root", str(inputs / "core"),
                   "--output-root", str(generated)]
        process = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (out / "generation.stdout").write_text(process.stdout)
        (out / "generation.stderr").write_text(process.stderr)
        report["generator_exit"] = process.returncode
        if process.returncode:
            raise RuntimeError("Pinned DSI generation failed")
        for relative in generator.OUTPUTS:
            actual = (generated / relative).read_bytes()
            expected = (ROOT / relative).read_bytes()
            report["checks"].append({"name": relative, "passed": actual == expected,
                "generated_sha256": digest(actual), "committed_sha256": digest(expected)})
        report["passed"] = len(report["inputs"]) == 13 and len(report["checks"]) == 2 and all(
            item["passed"] for item in report["checks"])
    except Exception:
        report["error"] = traceback.format_exc()
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "out": str(out)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--module-root", type=Path, help="Local bytes, still hash/blob checked")
    parser.add_argument("--core-root", type=Path, help="Local bytes, still hash/blob checked")
    raise SystemExit(run(parser.parse_args()))
