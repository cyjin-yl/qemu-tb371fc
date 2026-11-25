#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify four pinned inputs and both generated RPM statistics offset outputs."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import traceback

from source_download import read_source
import gen_rpm_stats_offset as generator

ROOT = Path(__file__).resolve().parents[2]
MAX_INPUT_BYTES = 256 * 1024


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"schema": "tb371fc-rpm-stats-offset-generation-v1", "passed": False,
              "source_revision": generator.VENDOR_REVISION,
              "geometry_source_revision": generator.UPSTREAM_REVISION,
              "source_pins_verified": False, "inputs": [], "checks": []}
    try:
        inputs = out / "inputs"
        for prefix, repository, revision, relative, size, blob, expected in generator.SOURCE_PINS:
            local = prefix + "/" + relative
            url = f"https://raw.githubusercontent.com/{repository}/{revision}/{relative}"
            item = {"path": local, "source_path": relative, "repository": repository,
                    "revision": revision, "url": url, "expected_bytes": size,
                    "expected_sha256": expected, "expected_git_blob_sha": blob,
                    "transport": "shared-source-download" if args.source_root is None else "local"}
            report["inputs"].append(item)
            if args.source_root is None:
                raw = read_source(url, max_bytes=MAX_INPUT_BYTES)
            else:
                raw = (args.source_root / local).read_bytes()
            # Identity validation stays outside the helper's transport retry loop.
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
        command = [sys.executable, "-I", "-S", "-B",
                   str(Path(generator.__file__).resolve()), "--source-root", str(inputs),
                   "--output-root", str(generated)]
        process = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (out / "generation.stdout").write_text(process.stdout, encoding="utf-8")
        (out / "generation.stderr").write_text(process.stderr, encoding="utf-8")
        report["generator_exit"] = process.returncode
        if process.returncode:
            raise RuntimeError("Pinned RPM statistics offset regeneration failed")
        for relative in generator.OUTPUTS:
            actual, committed = (generated / relative).read_bytes(), (ROOT / relative).read_bytes()
            report["checks"].append({"name": relative, "passed": actual == committed,
                "generated_bytes": len(actual), "committed_bytes": len(committed),
                "generated_sha256": generator.digest(actual),
                "committed_sha256": generator.digest(committed)})
        manifest = json.loads((generated / generator.OUTPUTS[1]).read_text())
        expected = {"source_input_count": 4, "register_count": 1, "window_count": 1,
                    "mapped_bytes": 4, "physical_base": 0x0c3f0004,
                    "offset_value": 0x000f0048}
        report["generated_contract"] = {key: manifest[key] for key in expected}
        report["passed"] = (
            len(report["inputs"]) == len(generator.SOURCE_PINS) == 4 and
            len(report["checks"]) == len(generator.OUTPUTS) == 2 and
            all(item["passed"] is True for item in report["checks"]) and
            report["generated_contract"] == expected and
            manifest["reference_scope"]["statistics_payload_implemented"] is False and
            manifest["reference_scope"]["native_reset_value_known"] is False and
            manifest["upstream_layout"]["payload_mapped_by_this_device"] is False)
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
                        help="Local primary-r11/ and primary-upstream/; all complete pins still apply")
    raise SystemExit(run(parser.parse_args()))
