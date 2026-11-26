#!/usr/bin/env python3
"""Compile the actual handoff helpers against libfdt and run host fixtures.

The temporary include contains unmodified function definitions from QEMU.
Only the surrounding object/CPU plumbing is stubbed by the C harness. This
checks the producer on older system libfdt without requiring a guest, kernel,
or private board input. It is not a Linux boot or native-hardware result.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
FUNCTIONS = {
    "system/device_tree.c": (
        "findnode_nofail", "qemu_fdt_setprop", "qemu_fdt_setprop_cell",
        "qemu_fdt_setprop_u64", "qemu_fdt_setprop_string",
        "qemu_fdt_nop_node", "qemu_fdt_add_subnode",
    ),
    "hw/arm/boot.c": ("fdt_add_psci_node",),
}


def extract(source, name):
    pattern = (rf"^(?:static )?(?:int|void) {re.escape(name)}"
               rf"\([^;{{}}]*\)\n\{{.*?^\}}")
    matches = re.findall(pattern, source, flags=re.M | re.S)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one definition of {name}")
    return matches[0] + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--libfdt-include", type=Path)
    parser.add_argument("--libfdt-library", type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    receipt = {"status": "failed", "scope": "host_producer_fixture",
               "guest_executed": False, "native_hardware_pass": False}
    try:
        files = list(FUNCTIONS) + [
            "include/hw/arm/boot.h", "hw/arm/lenovo-tb371fc.c",
            "target/arm/kvm-consts.h", "tb371fc/tests/boot_fdt_handoff_test.c",
            "tb371fc/tests/test_boot_fdt_handoff.py",
        ]
        raw = {name: (ROOT / name).read_bytes() for name in files}
        receipt["source_sha256"] = {
            name: hashlib.sha256(data).hexdigest() for name, data in raw.items()
        }
        # Prevent a helper-only test from overlooking the board opt-in wiring.
        wiring = {
            "include/hw/arm/boot.h": "bool preserve_psci_node;",
            "hw/arm/lenovo-tb371fc.c":
                "s->bootinfo.preserve_psci_node = true;",
            "hw/arm/boot.c":
                "fdt_add_psci_node(fdt, cpu, binfo->preserve_psci_node);",
        }
        for name, required in wiring.items():
            if raw[name].decode().count(required) != 1:
                raise ValueError(f"missing or ambiguous board wiring in {name}")
        generated = "\n".join(
            extract(raw[name].decode(), function)
            for name, functions in FUNCTIONS.items() for function in functions
        )
        (out / "handoff-functions.inc").write_text(generated)
        receipt["extracted_functions_sha256"] = hashlib.sha256(
            generated.encode()).hexdigest()
        command = [args.cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
                   "-I", str(ROOT), "-I", str(out)]
        if args.libfdt_include:
            command += ["-I", str(args.libfdt_include.resolve())]
        command += [str(ROOT / "tb371fc/tests/boot_fdt_handoff_test.c")]
        if args.libfdt_library:
            library = args.libfdt_library.resolve()
            receipt["libfdt_library_sha256"] = hashlib.sha256(
                library.read_bytes()).hexdigest()
            command += [str(library)]
        else:
            command += ["-lfdt"]
        command += ["-o", str(out / "boot-fdt-handoff-test")]
        receipt["compile_argv"] = command
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=60, check=False)
        (out / "compile.stdout").write_text(result.stdout)
        (out / "compile.stderr").write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(f"compiler failed ({result.returncode}): "
                               f"{result.stderr.strip()}")
        result = subprocess.run([str(out / "boot-fdt-handoff-test")],
                                capture_output=True, text=True, timeout=30,
                                check=False)
        (out / "fixture.stdout").write_text(result.stdout)
        (out / "fixture.stderr").write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(f"fixture failed ({result.returncode}): "
                               f"{result.stderr.strip()}")
        fixture = json.loads(result.stdout)
        if fixture.get("status") != "passed" or fixture.get("cases") != 17:
            raise ValueError(f"incomplete fixture result: {fixture}")
        receipt.update(status="passed", fixture=fixture)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        receipt["error"] = str(exc)
    (out / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
