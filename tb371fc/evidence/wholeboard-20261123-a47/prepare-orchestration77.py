#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate reviewed 77-gate orchestration files without executing any of them.

The root agent supplies the actual source/run and reviewed WAIT verifier hash.
This generator performs local text, AST and inverse-diff checks only. Generated
worker/launcher files contain the existing later execution flow, but this
generator never imports or invokes them, the stage helper, or a verifier.
"""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import re

ROOT = Path("/run/tb371fc-root77-a47")
OLDROOT = Path("/run/tb371fc-root76-a47")
STAGE_PATH = Path(
    "/run/tb371fc-actual-dt-native8-adapter-a47/stage-actual-dt-fixture.py")
TEST_PATH = Path(
    "/run/tb371fc-rpmh-wait-tests-a47/tb371fc/tests/test_rpmh_wait_mask.py")
OLD_SOURCE = "3b31e83dc412962826ada872e361a1d32ea4e463"
OLD_RUN = 45255339714
OLD_LABEL = "native-3b31e8"
RUNNER_SHA = "491e94f0a8c81efe62c5e62acb0f07a52ec4140ba31d166a420524cdeae555fc"
TEST_SHA = "b72b503c8c968310ea435dc058c6db1a072cad2a20281761696e2ffb3bd16032"
OBSERVER_SHA = "20f589dd4e0b21748d7ddccbacd44980f9ccef28cf13134394c9648ee9370da9"
OLD_OBSERVER_SHA = "3ba7fb09cf6d8bbdffc52d13b50ddf6ff325ae5bd7867168452a99eca3c5cba7"
STAGE_SHA = "3615728f8f69d19e19cecbf9dcc6c0b2b45e273a1a155aa2175438f4cdcbe320"
PLAN_SHA = "72e61e8089d97191be3bcfb8ccfcaef211555a2ead1092440e252911ad66e00f"
BASELINE_PINS = {
    "prepare-orchestration76.py": "f42ba59c639b332f7aed4be8d3aff8fab5f7d35cf4ca436088b0159e4c3dfc3a",
    "worker-template76.py": "4570152f5417ad41261f972f79a7fa60f288f350b1ad5bf91a9e2db86f62b6ea",
    "native-inner76.py": "3212bc9e6336e159a7429fc55e12ab827c3c284c457296810079f6f4c49780e5",
    "native-3b31e8-watcher.py": "28a249fd3ef672259a743f49aefe9b5653071907dcb3f66c77e77ba7b1ae54cc",
    "launcher76.py": "9bbc6c24d333d48ddf351ef5e3355ba52328d3de1ca88a84b8f2ab5dc97e1d1b",
    "plan76-preflight.json": "69b441044227d1d0d6b23abcf20f2007b83008c950c28edfc661d0eeb0da02cd",
}
WAIT_GLOBAL = "qcom-sm8250-rpmh.reference-wait-mask-reserved=on"
WAIT_GATE = "rpmh-wait-mask-reference"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def pinned_text(path, expected):
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    require(actual == expected, f"input identity mismatch: {path}: {actual}")
    text = raw.decode("utf-8")
    require(text.encode("utf-8") == raw, f"non-canonical UTF-8: {path}")
    return text


def parsed(text, filename):
    tree = ast.parse(text, filename=str(filename))
    compile(tree, str(filename), "exec")
    return tree


def function_source(text, filename, name, arguments):
    tree = parsed(text, filename)
    matches = [node for node in tree.body
               if isinstance(node, ast.FunctionDef) and node.name == name]
    require(len(matches) == 1, f"unique {name} function required")
    node = matches[0]
    require(not node.decorator_list and
            [arg.arg for arg in node.args.args] == arguments and
            not node.args.posonlyargs and not node.args.kwonlyargs and
            node.args.vararg is None and node.args.kwarg is None,
            f"unexpected callable interface: {name}")
    source = ast.get_source_segment(text, node) + "\n"
    parsed(source, name)
    return source, node


def replace_once(text, old, new, changes):
    require(text.count(old) == 1, "replacement is not unique: " + old[:140])
    changes.append((old, new))
    return text.replace(old, new)


def prove_inverse(text, original, changes, name):
    reverse = text
    for old, new in reversed(changes):
        require(reverse.count(new) == 1,
                name + " inverse replacement is not unique: " + new[:100])
        reverse = reverse.replace(new, old)
    require(reverse == original, name + " did not preserve the pinned baseline")
    return digest(reverse)


def make_worker(old, verifier_function, stage_function):
    changes = []
    worker = old

    def alter(before, after):
        nonlocal worker
        worker = replace_once(worker, before, after, changes)

    alter("noc-sbm-reference|gpucc-sm8250|",
          "noc-sbm-reference|rpmh-wait-mask-reference|gpucc-sm8250|")
    needle = (
        "   if '-noc-sbm-reference/' in info.filename:\n"
        "    assert data['qemu_sha256_before']==data['qemu_sha256_after'] and data['binary_unchanged'] is True\n"
        "    reported_hash=data['qemu_sha256_before']\n")
    alter(needle, needle + (
        "   if '-rpmh-wait-mask-reference/' in info.filename:\n"
        "    assert data['qemu_sha256_before']==data['qemu_sha256_after'] and data['binary_unchanged'] is True\n"
        "    reported_hash=data['qemu_sha256_before']\n"
        "    outcome=data['success']\n"))
    needle = (
        "   if '-noc-sbm-reference/' in info.filename:\n"
        "    compact.update(verify_noc_sbm_streams(z,info,data))\n")
    alter(needle, needle + (
        "   if '-rpmh-wait-mask-reference/' in info.filename:\n"
        "    compact.update(verify_rpmh_wait_streams(z,info,data))\n"))
    alter(" assert len(selected)==15,sorted(selected)\n",
          " assert len(selected)==16,sorted(selected)\n")
    needle = (
        " assert stream_counts=={'counter_streams_from_zip':3,'noc_streams_from_zip':4,"
        "'smmuv500_streams_from_zip':5,'tsens24xx_streams_from_zip':5,"
        "'cpufreq_hw_streams_from_zip':5,'devfreq_fw_streams_from_zip':5,"
        "'noc_sbm_streams_from_zip':5},stream_counts\n")
    replacement = needle.replace(
        "'noc_sbm_streams_from_zip':5}",
        "'noc_sbm_streams_from_zip':5,'rpmh_wait_streams_from_zip':5}")
    alter(needle, replacement)
    alter(" assert sum(stream_counts.values())==32\n",
          " assert sum(stream_counts.values())==37\n")

    # This is deliberately after every existing report/stream assertion and
    # the durable CI_AUDIT write, and before the original binary install.
    needle = " print('CI_AUDIT',json.dumps(audit),flush=True)\n"
    stage_call = (
        " # Stage the separate CI-built full-DT userspace fixture only after the complete ZIP audit.\n"
        " fixture_expected={'source_commit':cfg['source'],'workflow_run':cfg['run'],"
        "'artifact_id':cfg['artifact'],'artifact_sha256':digest,'qemu_sha256':qhash,"
        "'runner_sha256':cfg['runner_sha256'],'expected_gate_order':cfg['gate_order']}\n"
        " fixture_stage=stage_actual_dt_fixture(z,receipt,fixture_expected,"
        "p/(cfg['label']+'-full-dtb-fixture-stage'),"
        "p/(cfg['label']+'-full-dtb-fixture-stage.json'))\n"
        " assert fixture_stage['whole_zip_and_binary_audit_reused_from_outer_worker'] is True\n"
        " assert fixture_stage['validated_selected_zip_members']==8 and fixture_stage['fixture_build_completed'] is True\n"
        " assert fixture_stage['qemu_or_guest_executed'] is False and fixture_stage['toolchain_or_subprocess_invoked'] is False\n"
        " assert fixture_stage['userspace_passed'] is None and fixture_stage['android_boot_passed'] is None\n"
        " fixture_stage_audit_path=p/(cfg['label']+'-full-dtb-fixture-stage-audit.json')\n"
        " fixture_stage_audit_path.write_text(json.dumps(fixture_stage,indent=2)+'\\n')\n"
        " print('ACTUAL_DT_FIXTURE_STAGED',json.dumps(fixture_stage),flush=True)\n")
    alter(needle, needle + stage_call)
    # Embed only the two reviewed functions, never a module's top-level code.
    alter("cfg=__CONFIG__", verifier_function + "\n" + stage_function + "\ncfg=__CONFIG__")
    proof = prove_inverse(worker, old, changes, "worker")
    parsed(worker, "worker-template77.py")
    require(worker.count("__CONFIG__") == worker.count("__NATIVE__") == 1,
            "worker placeholders changed")
    require(worker.index(" assert sum(stream_counts.values())==37\n") <
            worker.index(" fixture_stage=stage_actual_dt_fixture(") <
            worker.index(" subprocess.run(base+[install,"),
            "stage operation must follow all audits and precede installation")
    return worker, proof, changes


def make_inner(old, observer):
    tree = parsed(old, "native-inner76.py")
    embedded_nodes = [
        node for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and len(node.value.args) == 1 and isinstance(node.value.args[0], ast.Constant)
        and isinstance(node.value.args[0].value, str)
        and node.value.args[0].value.startswith("#!/usr/bin/env python3\n")
    ]
    require(len(embedded_nodes) == 1, "unique embedded diagnostic wrapper required")
    node = embedded_nodes[0]
    require(node.lineno == node.end_lineno, "embedded wrapper line layout changed")
    old_observer = ast.literal_eval(node.value.args[0])
    require(digest(old_observer) == OLD_OBSERVER_SHA, "old observer identity changed")
    lines = old.splitlines(True)
    index = node.lineno - 1
    old_line = lines[index]
    new_line = "(scripts/'diagnose_private_aop.py').write_text(" + repr(observer) + ")\n"
    changes = []
    inner = replace_once(old, old_line, new_line, changes)
    inner = replace_once(inner, OLD_OBSERVER_SHA, OBSERVER_SHA, changes)
    needle = "globals+=['qcom-noc-sbm.enabled-reference=on']\n"
    inner = replace_once(inner, needle,
                         needle + "globals+=[" + repr(WAIT_GLOBAL) + "]\n", changes)
    proof = prove_inverse(inner, old, changes, "native inner")
    parsed(inner, "native-inner77.py")
    require(inner.count("globals+=[" + repr(WAIT_GLOBAL) + "]") == 1,
            "WAIT global must be appended exactly once")
    return inner, proof, changes


def make_watcher(old, cfg, worker, inner):
    lines = old.splitlines(True)
    require(lines[2].startswith("cfg="), "old watcher config position changed")
    old_cfg = ast.literal_eval(lines[2][4:].strip())
    require(old_cfg["source"] == OLD_SOURCE and old_cfg["run"] == OLD_RUN and
            old_cfg["label"] == OLD_LABEL and old_cfg["components"] == 72,
            "old watcher provenance differs")
    changes = []
    watcher = replace_once(old, lines[2], "cfg=" + repr(cfg) + "\n", changes)
    # Each file path is a distinct checked substitution; do not broadly replace
    # a source prefix inside another manifest, body or evidence string.
    for suffix in ("-ci-worker-template.py", "-inner.py"):
        watcher = replace_once(watcher, repr(OLD_LABEL + suffix),
                               repr(cfg["label"] + suffix), changes)
    watcher = replace_once(watcher, BASELINE_PINS["worker-template76.py"],
                           digest(worker), changes)
    watcher = replace_once(watcher, BASELINE_PINS["native-inner76.py"],
                           digest(inner), changes)
    proof = prove_inverse(watcher, old, changes, "watcher")
    parsed(watcher, cfg["label"] + "-watcher.py")
    return watcher, proof, changes


def make_launcher(old, cfg, watcher):
    changes = []
    launcher = old
    for suffix in ("-watcher.py", ".log", "-watcher.pid"):
        launcher = replace_once(launcher, repr(OLD_LABEL + suffix),
                                repr(cfg["label"] + suffix), changes)
    launcher = replace_once(launcher, BASELINE_PINS["native-3b31e8-watcher.py"],
                            digest(watcher), changes)
    launcher = replace_once(launcher, repr(OLD_SOURCE), repr(cfg["source"]), changes)
    launcher = replace_once(launcher, "'workflow_run':" + str(OLD_RUN),
                            "'workflow_run':" + str(cfg["run"]), changes)
    proof = prove_inverse(launcher, old, changes, "launcher")
    parsed(launcher, "launcher77.py")
    return launcher, proof, changes


def change_receipt(changes):
    return [{"old_bytes": len(before.encode()), "new_bytes": len(after.encode()),
             "old_sha256": digest(before), "new_sha256": digest(after)}
            for before, after in changes]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Actual 40-hex unified source commit")
    parser.add_argument("--run", required=True, type=int, help="Actual GitHub workflow run ID")
    parser.add_argument("--wait-verifier", type=Path,
                        default=ROOT / "rpmh-wait-artifact-verifier.py")
    parser.add_argument("--wait-verifier-sha256", required=True,
                        help="Final independently reviewed verifier identity; no draft is accepted implicitly")
    parser.add_argument("--observer", type=Path, default=ROOT / "native-rpmh-wait-observer.py")
    parser.add_argument("--stage-helper", type=Path, default=STAGE_PATH)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    require(re.fullmatch(r"[0-9a-f]{40}", args.source) is not None and
            args.source != OLD_SOURCE, "new exact source commit required")
    require(args.run > 0 and args.run != OLD_RUN, "new positive workflow run required")
    require(re.fullmatch(r"[0-9a-f]{64}", args.wait_verifier_sha256) is not None,
            "exact final WAIT verifier SHA256 required")
    label = "native-" + args.source[:6]
    require(label != OLD_LABEL, "new six-digit source prefix must be unambiguous")

    baseline = {name: pinned_text(OLDROOT / name, sha)
                for name, sha in BASELINE_PINS.items()}
    plan = json.loads(pinned_text(ROOT / "plan77-preflight.json", PLAN_SHA))
    old_plan = json.loads(baseline["plan76-preflight.json"])
    runner = pinned_text(ROOT / "tb371fc/scripts/ci_device_tests.py", RUNNER_SHA)
    parsed(runner, "ci_device_tests.py")  # No import or plan/test invocation.
    require(plan["runner_sha256"] == RUNNER_SHA and plan["test_sha256"] == TEST_SHA,
            "plan input identity mismatch")
    require({key: value["count"] for key, value in plan["suites"].items()} ==
            {"components": 73, "linux": 4, "all": 77}, "new plan counts differ")
    for key in ("components", "linux", "all"):
        names = plan["suites"][key]["names"]
        require(len(names) == len(set(names)) == plan["suites"][key]["count"],
                "duplicate or missing ordered plan names: " + key)
    new_names = plan["suites"]["components"]["names"]
    require([name for name in new_names if name != WAIT_GATE] ==
            old_plan["suites"]["components"]["names"], "old component order changed")
    require(new_names.index(WAIT_GATE) == new_names.index("rpmh-display-migration") + 1,
            "WAIT gate insertion point changed")
    require(plan["suites"]["linux"] == old_plan["suites"]["linux"],
            "original four Linux gates changed")
    require(plan["suites"]["all"]["names"] ==
            new_names + plan["suites"]["linux"]["names"], "all-suite concatenation changed")
    require(plan["prior_76_contracts_unchanged"] is True, "prior gate-contract review missing")
    gate = plan["rpmh_wait_gate"]
    require(gate["verdict"] == "success" and gate["expected_count"] == 17 and
            gate["expected_lengths"] == {"migrations": 1, "rejected_migrations": 2,
                                        "corrupted_stream_rejections": 2, "source_streams": 3},
            "WAIT acceptance contract changed")
    test = pinned_text(TEST_PATH, TEST_SHA)
    parsed(test, TEST_PATH)

    verifier = pinned_text(args.wait_verifier, args.wait_verifier_sha256)
    verifier_function, verifier_node = function_source(
        verifier, args.wait_verifier, "verify_rpmh_wait_streams", ["z", "info", "data"])
    require(TEST_SHA in verifier_function and
            "rpmh_wait_streams_from_zip" in verifier_function,
            "WAIT verifier is not bound to the final test/stream inventory")
    require(isinstance(verifier_node.body[-1], ast.Return),
            "unfinished WAIT verifier: final return is absent")
    observer = pinned_text(args.observer, OBSERVER_SHA)
    parsed(observer, args.observer)
    stage = pinned_text(args.stage_helper, STAGE_SHA)
    stage_function, stage_node = function_source(
        stage, args.stage_helper, "stage_actual_dt_fixture",
        ["z", "accepted_artifact", "expected", "stage_dir", "receipt_path"])
    require(isinstance(stage_node.body[-1], ast.Return), "incomplete stage helper")
    cfg = {
        "source": args.source, "run": args.run, "components": 73,
        "label": label, "binary": "qemu-" + args.source[:6],
        "runner_sha256": RUNNER_SHA,
        "gate_order": {key: plan["suites"][key]["names"] for key in ("components", "linux")},
    }
    worker, worker_reverse, worker_changes = make_worker(
        baseline["worker-template76.py"], verifier_function, stage_function)
    inner, inner_reverse, inner_changes = make_inner(baseline["native-inner76.py"], observer)
    watcher, watcher_reverse, watcher_changes = make_watcher(
        baseline["native-3b31e8-watcher.py"], cfg, worker, inner)
    launcher, launcher_reverse, launcher_changes = make_launcher(
        baseline["launcher76.py"], cfg, watcher)

    output = args.output_root.absolute()
    output.mkdir(parents=True, exist_ok=True)
    require(output.is_dir() and not output.is_symlink(), "output directory is not a real directory")
    files = {
        "worker-template77.py": worker, "native-inner77.py": inner,
        label + "-watcher.py": watcher, "launcher77.py": launcher,
    }
    host = Path("/tank/shared/projects/tb371fc-wholeboard-a47")
    metadata = {
        "schema": "tb371fc-orchestration77-generation-v1",
        "source": cfg["source"], "run": cfg["run"], "cfg": cfg,
        "generator_path": str(Path(__file__).absolute()),
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "baseline_input_sha256": BASELINE_PINS,
        "plan_sha256": PLAN_SHA, "runner_sha256": RUNNER_SHA,
        "worker_template_sha256": digest(worker), "native_inner_sha256": digest(inner),
        "watcher_sha256": digest(watcher), "launcher_sha256": digest(launcher),
        "rpmh_wait_verifier_path": str(args.wait_verifier),
        "rpmh_wait_verifier_sha256": digest(verifier),
        "embedded_rpmh_wait_verifier_sha256": digest(verifier_function),
        "rpmh_wait_test_sha256": TEST_SHA,
        "observer_path": str(args.observer), "observer_sha256": OBSERVER_SHA,
        "actual_dt_stage_helper_path": str(args.stage_helper),
        "actual_dt_stage_helper_sha256": STAGE_SHA,
        "embedded_actual_dt_stage_helper_sha256": digest(stage_function),
        "selected_receipts_expected": 16, "actual_stream_files_expected": 37,
        "component_gates_expected": 73, "linux_gates_expected": 4,
        "additional_native_global": WAIT_GLOBAL, "native_globals_expected_count": 18,
        "native_global_count_observed": False,
        "stage_after_all_report_and_stream_audits": True,
        "stage_before_binary_install": True,
        "stage_directory": str(host / (label + "-full-dtb-fixture-stage")),
        "stage_receipt": str(host / (label + "-full-dtb-fixture-stage.json")),
        "stage_audit": str(host / (label + "-full-dtb-fixture-stage-audit.json")),
        "stage_receipt_has_actual_manifest_and_member_hashes_when_executed": True,
        "stage_is_not_a_userspace_or_android_success_verdict": True,
        "prior_worker_preserved_outside_wait_guards_verifier_and_fixture_stage": True,
        "prior_inner_preserved_outside_observer_hash_and_one_global": True,
        "inverse_sha256": {
            "worker": worker_reverse, "inner": inner_reverse,
            "watcher": watcher_reverse, "launcher": launcher_reverse,
        },
        "checked_text_changes": {
            "worker": change_receipt(worker_changes), "inner": change_receipt(inner_changes),
            "watcher": change_receipt(watcher_changes), "launcher": change_receipt(launcher_changes),
        },
        "deployment_files": [
            {"local": str(output / "worker-template77.py"),
             "host": str(host / (label + "-ci-worker-template.py")), "sha256": digest(worker)},
            {"local": str(output / "native-inner77.py"),
             "host": str(host / (label + "-inner.py")), "sha256": digest(inner)},
            {"local": str(output / (label + "-watcher.py")),
             "host": str(host / (label + "-watcher.py")), "sha256": digest(watcher)},
            {"local": str(output / "launcher77.py"), "host": None, "sha256": digest(launcher)},
        ],
        "generated_scripts_executed": False, "stage_helper_executed": False,
        "verifiers_executed": False, "remote_actions_executed": False,
        "native_executed": False, "public_tests_executed": False, "build_executed": False,
    }
    files["orchestration77-metadata.json"] = json.dumps(metadata, indent=2) + "\n"
    for name in files:
        path = output / name
        require(not path.exists() and not path.is_symlink(), "refusing to replace output: " + str(path))
    for name, body in files.items():
        with (output / name).open("x", encoding="utf-8", newline="") as stream:
            stream.write(body)
    print(json.dumps({key: value for key, value in metadata.items()
                      if key not in ("cfg", "checked_text_changes")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

