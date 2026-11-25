#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reproduce both GCC data files from four pinned public source inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import traceback
from source_download import read_source

import generate_gcc_sm8250 as generator

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ("hw/misc/qcom-sm8250-gcc-data.inc",
           "tb371fc/docs/gcc-sm8250-registers.json")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"schema": "tb371fc-gcc-generation-v1", "passed": False,
              "source_pins_verified": False, "inputs": [], "checks": []}
    try:
        manifest = json.loads((ROOT / OUTPUTS[1]).read_text())
        inputs = out / "inputs"
        linux = inputs / "linux-6.12.114"
        vendor = inputs / "gcc-kona.c"
        branch = inputs / "clk-branch.c"
        public = [
            ("https://raw.githubusercontent.com/gregkh/linux/v6.12.114/" + path,
             linux / path, expected)
            for path, expected in manifest["sha256"].items()
        ]
        public.append((
            "https://raw.githubusercontent.com/OnePlusOSS/"
            "android_kernel_oneplus_sm8250/" + generator.VENDOR_REVISION +
            "/" + generator.VENDOR_PATH, vendor, generator.VENDOR_SHA256))
        public.append((
            "https://raw.githubusercontent.com/OnePlusOSS/"
            "android_kernel_oneplus_sm8250/" + generator.VENDOR_REVISION +
            "/" + generator.VENDOR_BRANCH_PATH, branch, generator.VENDOR_BRANCH_SHA256))
        for url, path, expected in public:
            content = read_source(url, max_bytes=512 * 1024)
            if len(content) > 512 * 1024:
                raise ValueError("Public GCC input exceeds its size bound")
            actual = digest(content)
            report["inputs"].append({
                "url": url, "sha256": actual, "expected_sha256": expected,
                "bytes": len(content)})
            if actual != expected:
                raise ValueError("Public source hash differs: " + url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        report["source_pins_verified"] = True
        generated = out / "generated"
        for relative in OUTPUTS:
            (generated / relative).parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable, str(Path(generator.__file__).resolve()),
            str(linux), "--vendor-gcc", str(vendor),
            "--vendor-branch", str(branch),
            "--output-root", str(generated)]
        process = subprocess.run(command, capture_output=True, text=True,
                                 timeout=60)
        (out / "generation.stdout").write_text(process.stdout)
        (out / "generation.stderr").write_text(process.stderr)
        report["generator_exit"] = process.returncode
        if process.returncode:
            raise RuntimeError("Pinned GCC regeneration failed")
        for relative in OUTPUTS:
            actual = (generated / relative).read_bytes()
            expected = (ROOT / relative).read_bytes()
            report["checks"].append({
                "name": relative, "passed": actual == expected,
                "generated_sha256": digest(actual),
                "committed_sha256": digest(expected)})
        report["passed"] = (len(report["checks"]) == len(OUTPUTS) and
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
    raise SystemExit(run(parser.parse_args()))
