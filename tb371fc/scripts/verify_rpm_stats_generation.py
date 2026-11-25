#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify six complete RPM ABI source pins and both deterministic outputs."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import traceback

import generate_rpm_stats_reference as generator
from source_download import read_source


ROOT = Path(__file__).resolve().parents[2]


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {
        "schema": "tb371fc-rpm-stats-generation-v1",
        "passed": False,
        "contract_verified": False,
        "source_pins_verified": False,
        "public_source_count": 0,
        "private_firmware_evidence_verified": False,
        "firmware_is_pristine_stock": False,
        "input_mode": "pinned-network" if args.source_root is None else "local-verified",
        "inputs": [],
        "checks": [],
    }
    try:
        contract_path = ROOT / generator.CONTRACT_PATH
        contract = generator.load_contract(contract_path)
        report.update({"contract_verified": True,
                       "contract_sha256": generator.CONTRACT_SHA256,
                       "contract_bytes": generator.CONTRACT_BYTES,
                       "private_research_receipt_sha256":
                           contract["evidence_binding"]["private_research_receipt_sha256"]})
        retained_contract = out / "reference-contract.json"
        retained_contract.write_bytes(contract_path.read_bytes())
        inputs = out / "inputs"
        for pin in contract["public_sources"]["pins"]:
            local, url = generator.source_path(pin), generator.raw_url(pin)
            item = {"path": local, "source_path": pin["path"],
                    "repository": pin["repository"], "commit": pin["commit"], "url": url,
                    "expected_bytes": pin["bytes"], "expected_sha256": pin["sha256"],
                    "expected_git_blob_sha1": pin["git_blob_sha1"], "verified": False}
            report["inputs"].append(item)
            if args.source_root is None:
                raw = read_source(url, max_bytes=pin["bytes"], timeout=30)
            else:
                raw = generator.bounded_read(args.source_root / local, pin["bytes"])
            # Identity errors are final and never enter the transport retry loop.
            item.update({"bytes": len(raw), "sha256": generator.digest(raw),
                         "git_blob_sha1": generator.git_blob(raw)})
            generator.verify_source(raw, pin)
            item["verified"] = True
            path = inputs / local
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            report["public_source_count"] += 1
        report["source_pins_verified"] = report["public_source_count"] == 6
        generated = out / "generated"
        command = [sys.executable, "-B", str(Path(generator.__file__).resolve()),
                   str(inputs), "--contract", str(retained_contract),
                   "--output-root", str(generated)]
        process = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (out / "generation.stdout").write_text(process.stdout, encoding="utf-8")
        (out / "generation.stderr").write_text(process.stderr, encoding="utf-8")
        report["generator_exit"] = process.returncode
        if process.returncode:
            raise RuntimeError("Pinned RPM statistics reference regeneration failed")
        for relative in generator.OUTPUTS:
            actual = (generated / relative).read_bytes()
            committed = (ROOT / relative).read_bytes()
            report["checks"].append({"name": relative, "passed": actual == committed,
                "generated_bytes": len(actual), "committed_bytes": len(committed),
                "generated_sha256": generator.digest(actual),
                "committed_sha256": generator.digest(committed)})
        evidence = json.loads((generated / generator.OUTPUTS[1]).read_text(encoding="utf-8"))
        report["public_abi_verified"] = evidence["public_facts"]
        report["passed"] = (report["source_pins_verified"] and
            len(report["inputs"]) == 6 and len(report["checks"]) == 2 and
            all(item["verified"] is True for item in report["inputs"]) and
            all(item["passed"] is True for item in report["checks"]) and
            evidence["contract_sha256"] == generator.CONTRACT_SHA256 and
            evidence["source_input_count"] == 6 and
            evidence["source_pins_verified"] is True and
            evidence["private_firmware_evidence_verified"] is False and
            evidence["firmware_is_pristine_stock"] is False)
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
                        help="Local vendor/ and mainline/ tree; all complete identities are still verified")
    raise SystemExit(run(parser.parse_args()))
