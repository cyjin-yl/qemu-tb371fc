#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent CT102 watchpoint build using exact public Git objects."""
import ast
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback

if not __debug__ or sys.flags.optimize != 0:
    raise RuntimeError("Controller requires assertions; optimized Python refused")

ROOT = Path("/work/aop-reference-watchpoint-attempt-05")
STAGE = Path("/work/aop-reference-watchpoint-attempt-05-stage")
SOURCE = ROOT / "source"
BUILD = ROOT / "build"
RESULTS = ROOT / "results"
DEPENDENCY_SOURCE = Path("/work/tb371fc-verified-root-0820/subprojects")
COMMIT = "fe89e5a553fe038babe07abb279a0975d4306715"
BASE_COMMIT = "567e4f9834547f1445ecf24de96603132df8cfb8"
REMOTE = "https://github.com/cyjin-yl/qemu-tb371fc.git"
GIT_PARENT_SOURCE = Path("/work/aop-reference-root-1230/source")
GIT_OBJECT_PACKET = STAGE / "fe89-exact-objects.json"
GIT_OBJECT_PACKET_SHA256 = (
    "33df667fcc6e0fc072db21a6a303559779724978b51b3bd433de8849da1e72eb")
GIT_OBJECT_PACKET_BYTES = 184744
GIT_OBJECT_TREE = "302cff20495d2b63e49a5bbde8879c028fb583ea"
RAW_COMMIT_SHA256 = (
    "12380467210f7dd9a4dae8a4b68fabbc2c4bfd4c78fd6b9f954901a8c5a3f5f3")
SOURCE_ACQUISITION = "local-parent-fetch-and-exact-public-git-object-import"
C_PATH = "hw/arm/qcom-aop-reference.c"
RUNNER_PATH = "tb371fc/tests/test_aop_entry_reference.py"
UNIT_PATH = "tb371fc/tests/test_aop_entry_xml.py"
EXPECTED = {
    C_PATH: "992a50c18904499d2ad2876a766a1cea69302376472b77c45d52b40afb9ba793",
    RUNNER_PATH: "18896b8b53720df6809441624b68342245bb7e09bfab8eee26b161dbb3ab613b",
    UNIT_PATH: "5dcf89bc1a47bc1e589f39a467c2445d8fe4c4e0027dbda9e3586bb1d324eeeb",
    "hw/arm/Kconfig":
        "54ec7ce1e7db291d4958dab2fe3aeb5ad335b3a0fceb7f40c6cad432b7eb506b",
    "hw/arm/meson.build":
        "3219ce17fc10ab829a52dc4ad38a24425a731ccebc1e2e1a17b838495557e656",
    "configs/devices/aarch64-softmmu/aop-reference.mak":
        "2b2e0bd62de9febac1c71ff80b53160b1637ab90928c2fcb1b4d89e976c0df1a",
    "tb371fc/docs/aop-entry-reference.md":
        "fac70b1b8ea60fbb65fa147df7380ee665d4048c296831d52bad96cf7471066c",
    ".github/workflows/aop-entry-reference.yml":
        "6c61af3463fde200c7fe6623746c590361c97f6ca34fa80f3c764bf06f19def8",
}
EXTERNALS = ("berkeley-softfloat-3", "berkeley-testfloat-3", "packagefiles",
             "keycodemapdb", "libvhost-user", "libvduse")
OLD_ROOTS = {
    "ct1230": Path("/work/aop-reference-root-1230/results"),
    "ct1247": Path("/work/aop-reference-xml-root-1247"),
    "ct04": Path("/work/aop-reference-watchpoint-attempt-04/results"),
}
OLD_EXPECTED = {
    "ct1230/preflight/result.json":
        "a21e7f04dcad28108f9f8d0f149c5cc833ab12235832a737d99cb11044f9ecfd",
    "ct1230/qemu-system-aarch64":
        "cf8ca6bca81246e4e696a62bc793d1386af39841b39d30333dd1630921c6edf5",
    "ct1247/preflight/result.json":
        "0fe99fb1593be95a24aa7474ae2b3eb9dca7cad90c070385c4b7e782dc02ef5c",
    "ct1247/result.json":
        "10f2d606c5106591d0504404211f2562018756dc5e7d7411cd14f3d9e0ebf2ab",
}
OLD_EXPECTED.update({
    "ct04/result.json":
        "a5a7c31a5968c1980c17f651752dabea934064b424ffede40632e429b8b8c87d",
    "ct04/02-git-fetch.json":
        "834040d0e01eb99fb6aecbfc5c313ded82be4e177aad38dc7923d7968d8050b7",
    "ct04/02-git-fetch.log":
        "1efababbd9d23f753872be4dcc13639d96900b98f49a1a6537959e88bee76272",
})
COMMANDS = []


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def not_symlink(path):
    for part in (path, *path.parents):
        require(not part.is_symlink(), "Symlink path refused: " + str(part))


def tree_hashes(path):
    not_symlink(path)
    require(path.is_dir(), "Missing directory: " + str(path))
    result = {}
    for current, dirs, files in os.walk(path, followlinks=False):
        current = Path(current)
        dirs[:] = sorted(name for name in dirs if name != ".git")
        for name in dirs + files:
            if name != ".git":
                require(not (current / name).is_symlink(),
                        "Symlink entry refused: " + str(current / name))
        for name in sorted(files):
            if name != ".git":
                item = current / name
                result[str(item.relative_to(path))] = sha(item)
    return result


def history_hashes():
    return {label: tree_hashes(path) for label, path in OLD_ROOTS.items()}


def group_exists(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def run(name, argv, cwd=None, seconds=120):
    """Use a new process group, retain all files, terminate only that group."""
    require(seconds > 0, "Deadline exhausted before " + name)
    stem = "%02d-%s" % (len(COMMANDS), name)
    log_path = RESULTS / (stem + ".log")
    info = {"name": name, "argv": [str(x) for x in argv],
            "cwd": str(cwd) if cwd else None, "started_utc": now(),
            "timeout_seconds": seconds, "log": log_path.name,
            "python_optimize_removed": True}
    COMMANDS.append(info)
    save(RESULTS / (stem + "-started.json"), info)
    process = None
    started = time.monotonic()
    try:
        with log_path.open("xb") as log:
            env = os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"
            env.pop("PYTHONOPTIMIZE", None)
            process = subprocess.Popen(argv, cwd=cwd, stdout=log,
                                       stderr=subprocess.STDOUT,
                                       start_new_session=True, env=env)
            info["process_group"] = process.pid
            try:
                info["exit_code"] = process.wait(timeout=seconds)
            except subprocess.TimeoutExpired as exc:
                info["timeout"] = {"type": type(exc).__name__,
                                   "message": str(exc)}
                raise
            info["group_present_after_wait"] = group_exists(process.pid)
            require(not info["group_present_after_wait"],
                    "Command left its process group alive: " + name)
            require(process.returncode == 0, "Command failed: " + name)
    except BaseException:
        info["exception"] = traceback.format_exc()
        raise
    finally:
        if process is not None:
            if group_exists(process.pid):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    info["own_group_sigkill_sent"] = True
                except ProcessLookupError:
                    info["group_exited_before_cleanup"] = True
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired as exc:
                    info["post_kill_wait_failure"] = str(exc)
            info["exit_code"] = process.poll()
            info["group_present_after_cleanup"] = group_exists(process.pid)
        info["wall_seconds"] = time.monotonic() - started
        info["finished_utc"] = now()
        save(RESULTS / (stem + ".json"), info)
    return log_path


def preflight_body(text):
    module = ast.parse(text)
    matches = [node for node in module.body
               if isinstance(node, ast.FunctionDef) and node.name == "run_preflight"]
    require(len(matches) == 1, "Expected one top-level preflight function")
    return ast.get_source_segment(text, matches[0])


def git_object_id(kind, raw):
    header = ("%s %d\0" % (kind, len(raw))).encode("ascii")
    return hashlib.sha1(header + raw).hexdigest()


def acquire_exact_source():
    """Populate only the new repository; never run a network fetch."""
    not_symlink(GIT_PARENT_SOURCE)
    not_symlink(GIT_OBJECT_PACKET)
    raw_packet = GIT_OBJECT_PACKET.read_bytes()
    require(len(raw_packet) == GIT_OBJECT_PACKET_BYTES and
            hashlib.sha256(raw_packet).hexdigest() == GIT_OBJECT_PACKET_SHA256,
            "Exact public Git transport packet differs")
    packet = json.loads(raw_packet)
    require(packet["schema"] == "tb371fc-exact-git-object-transport-v1" and
            packet["base_commit"] == BASE_COMMIT and
            packet["commit"] == COMMIT and packet["tree"] == GIT_OBJECT_TREE and
            packet["firmware_payload_included"] is False,
            "Git object packet scope or identity differs")
    files = packet["files"]
    require(isinstance(files, list) and len(files) == 22,
            "Exact twenty-two-object delta required")
    names = set()
    validated = []
    for entry in files:
        name = entry["path"]
        require(isinstance(name, str) and
                re.fullmatch(r"[A-Za-z0-9_./-]+", name) is not None and
                all(part not in ("", ".", "..", ".git")
                    for part in name.split("/")) and name not in names,
                "Unsafe or duplicate Git delta path")
        require(entry["mode"] == "100644" and
                entry["status"] in ("added", "modified"),
                "Only exact regular-file additions and modifications allowed")
        names.add(name)
        content = entry["content"].encode("utf-8")
        require(len(content) == entry["bytes"] and
                hashlib.sha256(content).hexdigest() == entry["sha256"] and
                git_object_id("blob", content) == entry["git_blob_sha"],
                "Git blob content identity differs: " + name)
        validated.append((entry, content))
    raw_commit = packet["raw_commit"].encode("utf-8")
    require(not raw_commit.endswith(b"\n") and
            hashlib.sha256(raw_commit).hexdigest() ==
            packet["raw_commit_sha256"] == RAW_COMMIT_SHA256 and
            git_object_id("commit", raw_commit) == COMMIT,
            "Raw commit bytes differ")
    require(raw_commit.startswith(("tree " + GIT_OBJECT_TREE + "\nparent " +
                                   BASE_COMMIT + "\n").encode("ascii")),
            "Raw commit tree or parent differs")

    # The old repository is read-only; SOURCE and the import index are new.
    run("local-parent-exists", ["git", "-C", str(GIT_PARENT_SOURCE),
                               "cat-file", "-e", BASE_COMMIT + "^{commit}"])
    parent_head = run("local-parent-head", ["git", "-C", str(GIT_PARENT_SOURCE),
                                          "rev-parse", "HEAD"])
    require(parent_head.read_text().strip() == BASE_COMMIT,
            "Preserved parent checkout identity differs")
    run("git-fetch-local-parent",
        ["git", "-c", "credential.helper=", "fetch", "--no-tags", "--depth=1",
         str(GIT_PARENT_SOURCE), BASE_COMMIT], SOURCE, 900)
    imported = RESULTS / "git-object-import"
    imported.mkdir()
    index_env = ["env", "GIT_INDEX_FILE=" + str(imported / "index")]
    run("git-read-parent-tree", index_env + ["git", "read-tree", BASE_COMMIT],
        SOURCE, 900)

    blob_paths = []
    for index, (entry, content) in enumerate(validated):
        path = imported / ("%02d.blob" % index)
        with path.open("xb") as stream:
            stream.write(content)
        blob_paths.append(path)
    blob_log = run("git-write-exact-blobs",
                   ["git", "hash-object", "-w", "--no-filters",
                    *[str(path) for path in blob_paths]], SOURCE)
    require(blob_log.read_text().splitlines() ==
            [entry["git_blob_sha"] for entry, _ in validated],
            "Git did not write the expected blob identities")
    update = index_env + ["git", "update-index", "--add"]
    for entry, _ in validated:
        update.extend(["--cacheinfo", entry["mode"] + "," +
                       entry["git_blob_sha"] + "," + entry["path"]])
    run("git-update-exact-index", update, SOURCE, 900)
    tree_log = run("git-write-exact-tree", index_env + ["git", "write-tree"],
                   SOURCE, 900)
    require(tree_log.read_text().strip() == GIT_OBJECT_TREE,
            "Reconstructed full Git tree differs")

    commit_path = imported / "fe89.commit"
    with commit_path.open("xb") as stream:
        stream.write(raw_commit)
    commit_log = run("git-write-exact-commit",
                     ["git", "hash-object", "-w", "-t", "commit",
                      "--no-filters", str(commit_path)], SOURCE)
    require(commit_log.read_text().strip() == COMMIT,
            "Imported commit identity differs")
    readback = run("git-readback-exact-commit",
                   ["git", "cat-file", "commit", COMMIT], SOURCE)
    require(readback.read_bytes() == raw_commit, "Git commit readback differs")
    require(not (SOURCE / ".git/index").exists(),
            "New checkout index unexpectedly exists before checkout")
    save(RESULTS / "source-acquisition.json", {
        "method": SOURCE_ACQUISITION, "network_fetch_used": False,
        "packet_sha256": GIT_OBJECT_PACKET_SHA256,
        "packet_bytes": len(raw_packet), "base_commit": BASE_COMMIT,
        "base_repository": str(GIT_PARENT_SOURCE),
        "old_repository_write_commands_used": False,
        "tree": GIT_OBJECT_TREE, "commit": COMMIT,
        "retained_import_index": str(imported / "index"),
        "retained_import_index_sha256": sha(imported / "index"),
        "raw_commit_sha256": RAW_COMMIT_SHA256,
        "objects": [{key: entry[key] for key in
                     ("path", "mode", "status", "git_blob_sha", "bytes", "sha256")}
                    for entry, _ in validated],
        "firmware_read": False, "firmware_executed": False})


def main():
    require(len(sys.argv) == 1, "Controller accepts no arguments")
    require(Path(__file__).absolute().parent == STAGE,
            "Controller must run from the reviewed stage directory")
    for path in (ROOT, STAGE, Path(__file__)):
        not_symlink(path)
    ROOT.mkdir(exist_ok=False)
    RESULTS.mkdir()
    started = time.monotonic()
    receipt = {"schema": "aop-reference-ct-watchpoint-attempt-05-v1",
               "passed": False, "all_passed": False, "source_commit": COMMIT,
               "base_commit": BASE_COMMIT, "remote": REMOTE, "jobs": 1,
               "source_acquisition": SOURCE_ACQUISITION,
               "git_object_packet_sha256": GIT_OBJECT_PACKET_SHA256,
               "historical_ct04_preservation_required": True,
               "firmware_read": False, "firmware_executed": False,
               "hardware_reset_verified": False, "aop_startup_validated": False,
               "full_tablet_equivalence": False,
               "internal_exception_before_access_proven": False,
               "controller_sha256": sha(Path(__file__)),
               "python_executable": sys.executable, "assertions_enabled": __debug__,
               "python_optimization": sys.flags.optimize,
               "combined_test_timeout_seconds": 180,
               "started_utc": now(), "commands": COMMANDS}
    save(RESULTS / "started.json", receipt)
    dependencies = {}
    history_before = {}
    source_before = {}
    frozen_binary = RESULTS / "qemu-system-aarch64"
    frozen_runner = RESULTS / "test_aop_entry_reference.py"
    frozen_unit = RESULTS / "test_aop_entry_xml.py"
    build_ok = False
    try:
        for path in OLD_ROOTS.values():
            require((path / "job.exit").read_text().strip() == "1",
                    "Old attempt must have completed and retain its failure")
        history_before = history_hashes()
        for name, expected in OLD_EXPECTED.items():
            label, relative = name.split("/", 1)
            require(history_before[label].get(relative) == expected,
                    "Preserved history changed: " + name)
        for label in ("ct1230", "ct1247"):
            proof = json.loads((OLD_ROOTS[label] / "preflight/result.json").read_text())
            require(proof["passed"] is False and proof["all_passed"] is False,
                    "Old failed preflight was relabeled")
            require(proof["firmware_read"] is False and
                    proof["firmware_executed"] is False,
                    "Unexpected historical firmware scope")
        old_fetch = json.loads((OLD_ROOTS["ct04"] / "result.json").read_text())
        require(old_fetch["passed"] is False and
                old_fetch["all_passed"] is False and
                old_fetch["build_succeeded"] is False and
                old_fetch["firmware_read"] is False and
                old_fetch["firmware_executed"] is False and
                old_fetch["source_commit"] == COMMIT,
                "Historical CT04 acquisition failure was relabeled")
        save(RESULTS / "history-before.json", history_before)

        SOURCE.mkdir()
        run("git-init", ["git", "init", str(SOURCE)])
        run("git-remote", ["git", "remote", "add", "origin", REMOTE], SOURCE)
        acquire_exact_source()
        run("git-checkout", ["git", "checkout", "--detach", COMMIT], SOURCE, 900)
        actual = run("source-commit", ["git", "rev-parse", "HEAD"], SOURCE)
        require(actual.read_text().strip() == COMMIT, "Wrong source commit")
        parent = run("source-parent", ["git", "rev-parse", "HEAD^"], SOURCE)
        require(parent.read_text().strip() == BASE_COMMIT, "Wrong fixed base")
        clean = run("source-status-before", ["git", "-c", "diff.ignoreSubmodules=all",
                    "status", "--porcelain=v1", "--untracked-files=no"], SOURCE, 900)
        require(not clean.read_text().strip(), "New checkout is not clean")
        source_before = {path: sha(SOURCE / path) for path in EXPECTED}
        require(source_before == EXPECTED, "Committed input hash mismatch")
        save(RESULTS / "source-inputs.json", {
            "source_commit": COMMIT, "parent_commit": BASE_COMMIT,
            "sha256": source_before, "firmware_read": False,
            "firmware_executed": False})
        baseline = run("original-preflight-source",
                       ["git", "show", BASE_COMMIT + ":" + RUNNER_PATH], SOURCE)
        original_body = preflight_body(baseline.read_text())
        current_body = preflight_body((SOURCE / RUNNER_PATH).read_text())
        require(current_body == original_body, "Original fourteen cases changed")
        save(RESULTS / "fourteen-case-source-identity.json", {
            "byte_identical": True, "base_commit": BASE_COMMIT,
            "source_commit": COMMIT,
            "function_sha256": hashlib.sha256(current_body.encode()).hexdigest()})

        for name in EXTERNALS:
            target = SOURCE / "subprojects" / name
            listing = run("tracked-" + name,
                          ["git", "ls-files", "--stage", "--",
                           "subprojects/" + name], SOURCE).read_text().splitlines()
            if any(not line.startswith("160000 ") for line in listing):
                require(target.is_dir() and any(target.iterdir()), str(target))
                dependencies[name] = {"action": "kept-committed-files"}
                continue
            origin = DEPENDENCY_SOURCE / name
            not_symlink(origin)
            not_symlink(target)
            require(origin.is_dir(), "Missing external source: " + str(origin))
            require(not target.exists() or (target.is_dir() and
                    not any(target.iterdir())), "Occupied external destination")
            before = tree_hashes(origin)
            require(bool(before), "Empty dependency: " + str(origin))
            shutil.copytree(origin, target, symlinks=False, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".git"))
            require(tree_hashes(target) == before, "External copy differs")
            dependencies[name] = {"action": "copied-external-files",
                                  "origin": str(origin), "files": before}
        save(RESULTS / "dependencies-before.json", dependencies)
        for relative in (RUNNER_PATH, UNIT_PATH):
            compile((SOURCE / relative).read_text(), str(SOURCE / relative), "exec")
        save(RESULTS / "python-syntax.json", {
            "compiled_without_execution": [RUNNER_PATH, UNIT_PATH]})
        run("checkpatch", ["perl", str(SOURCE / "scripts/checkpatch.pl"),
                          "--no-tree", "--file", str(SOURCE / C_PATH)])
        for name, command in (("compiler", ["cc", "--version"]),
                              ("linker", ["ld", "--version"]),
                              ("python", [sys.executable, "-E", "-B", "--version"]),
                              ("ninja", ["ninja", "--version"])):
            run(name + "-version", command)
        BUILD.mkdir()
        run("configure", [str(SOURCE / "configure"),
            "--target-list=aarch64-softmmu", "--with-devices-aarch64=aop-reference",
            "--without-default-devices", "--without-default-features",
            "--enable-system", "--enable-tcg", "--enable-fdt", "--disable-docs",
            "--disable-tools", "--disable-guest-agent"], BUILD, 600)
        run("build", ["ninja", "-j1", "qemu-system-aarch64"], BUILD, 7200)
        receipt["qemu_sha256_before"] = sha(BUILD / "qemu-system-aarch64")
        shutil.copyfile(BUILD / "qemu-system-aarch64", frozen_binary)
        frozen_binary.chmod(0o755)
        shutil.copyfile(SOURCE / RUNNER_PATH, frozen_runner)
        shutil.copyfile(SOURCE / UNIT_PATH, frozen_unit)
        require(sha(frozen_binary) == receipt["qemu_sha256_before"],
                "Frozen QEMU copy differs")
        require(sha(frozen_runner) == EXPECTED[RUNNER_PATH] and
                sha(frozen_unit) == EXPECTED[UNIT_PATH], "Frozen test copy differs")
        receipt.update({"frozen_binary": str(frozen_binary),
                        "frozen_runner": str(frozen_runner),
                        "runner_sha256_before": sha(frozen_runner),
                        "xml_test_sha256_before": sha(frozen_unit)})
        build_ok = True
        with (RESULTS / "build.exit").open("x") as stream:
            stream.write("0\n")
        run("qemu-version", [str(frozen_binary), "--version"])
        machines = run("machine-list", [str(frozen_binary), "-machine", "help"])
        require(re.search(r"^aop-entry-reference\s+", machines.read_text(), re.M),
                "AOP reference machine is not registered")
        save(RESULTS / "registration.json", {
            "passed": True, "machine": "aop-entry-reference",
            "qemu_sha256": receipt["qemu_sha256_before"],
            "listing_sha256": sha(machines)})

        test_started = time.monotonic()
        deadline = test_started + 180
        unit_log = run("xml-unit",
                       [sys.executable, "-E", "-B", str(frozen_unit), "-v"],
                       seconds=deadline - time.monotonic())
        text = unit_log.read_text()
        require(len(re.findall(r"^test_.* \.\.\. ok$", text, re.M)) == 11,
                "XML count or per-test verdict differs")
        require(re.search(r"^Ran 11 tests? in ", text, re.M) and
                text.rstrip().endswith("OK"), "XML summary differs")
        receipt["xml_tests_passed"] = 11
        receipt["preflight_started"] = True
        run("preflight", [sys.executable, "-E", "-B", str(frozen_runner), "preflight",
                          "--qemu", str(frozen_binary), "--out",
                          str(RESULTS / "preflight")],
            seconds=deadline - time.monotonic())
        receipt["combined_test_wall_seconds"] = time.monotonic() - test_started
        proof = json.loads((RESULTS / "preflight/result.json").read_text())
        require(proof["passed"] is True and proof["all_passed"] is True,
                "Fourteen-case synthetic preflight failed")
        require(proof["expected_checks"] == len(proof["checks"]) == 14 and
                all(check["passed"] is True for check in proof["checks"]),
                "Exact fourteen-case verdict mismatch")
        require(proof["firmware_read"] is False and
                proof["firmware_executed"] is False, "Unexpected firmware scope")
        require(proof["qemu_sha256"] == receipt["qemu_sha256_before"] and
                proof["runner_sha256"] == EXPECTED[RUNNER_PATH],
                "Preflight input identity mismatch")
        receipt["passed_checks"] = 14
        receipt["preflight_passed"] = True
    except BaseException:
        receipt["exception"] = traceback.format_exc()
    finally:
        try:
            if history_before:
                history_after = history_hashes()
                save(RESULTS / "history-after.json", history_after)
                receipt["history_unchanged"] = history_before == history_after
            if source_before:
                source_after = {path: sha(SOURCE / path) for path in EXPECTED}
                save(RESULTS / "source-inputs-after.json", source_after)
                clean = run("source-status-after",
                            ["git", "-c", "diff.ignoreSubmodules=all", "status",
                             "--porcelain=v1", "--untracked-files=no"], SOURCE, 900)
                receipt["source_unchanged"] = (
                    source_after == source_before and not clean.read_text().strip())
                after = {name: tree_hashes(SOURCE / "subprojects" / name)
                         for name, item in dependencies.items() if "files" in item}
                save(RESULTS / "dependencies-after.json", after)
                receipt["dependencies_unchanged"] = all(
                    after[name] == item["files"] for name, item in dependencies.items()
                    if "files" in item)
            if frozen_binary.exists():
                receipt["qemu_sha256_after"] = sha(frozen_binary)
                receipt["binary_unchanged"] = (
                    receipt["qemu_sha256_after"] == receipt["qemu_sha256_before"] ==
                    sha(BUILD / "qemu-system-aarch64"))
            if frozen_runner.exists() and frozen_unit.exists():
                receipt["runner_sha256_after"] = sha(frozen_runner)
                receipt["xml_test_sha256_after"] = sha(frozen_unit)
                receipt["tests_unchanged"] = (
                    receipt["runner_sha256_after"] == EXPECTED[RUNNER_PATH] and
                    receipt["xml_test_sha256_after"] == EXPECTED[UNIT_PATH])
            receipt["git_object_packet_unchanged"] = (
                sha(GIT_OBJECT_PACKET) == GIT_OBJECT_PACKET_SHA256)
            receipt["controller_unchanged"] = (
                sha(Path(__file__)) == receipt["controller_sha256"])
            proof_path = RESULTS / "preflight/result.json"
            if proof_path.exists():
                receipt["preflight_result_sha256"] = sha(proof_path)
                proof = json.loads(proof_path.read_text())
                checks = proof.get("checks", [])
                receipt["preflight_observed"] = {
                    "expected_checks": proof.get("expected_checks"),
                    "recorded_checks": len(checks),
                    "passed_checks": sum(c.get("passed") is True for c in checks),
                    "failed_checks": sum(c.get("passed") is False for c in checks),
                    "passed": proof.get("passed"), "all_passed": proof.get("all_passed")}
            receipt["passed"] = all(receipt.get(key) is True for key in (
                "preflight_passed", "history_unchanged", "source_unchanged",
                "dependencies_unchanged", "binary_unchanged", "tests_unchanged",
                "controller_unchanged", "git_object_packet_unchanged"
            )) and "exception" not in receipt
        except BaseException:
            receipt["final_verification_exception"] = traceback.format_exc()
            receipt["passed"] = False
        receipt["all_passed"] = receipt["passed"]
        receipt["build_succeeded"] = build_ok
        receipt["wall_seconds"] = time.monotonic() - started
        receipt["finished_utc"] = now()
        save(RESULTS / "result.json", receipt)
        if not build_ok:
            with (RESULTS / "build.exit").open("x") as stream:
                stream.write("1\n")
        with (RESULTS / "job.exit").open("x") as stream:
            stream.write("0\n" if receipt["passed"] else "1\n")
    print(json.dumps({key: receipt[key] for key in
                     ("passed", "build_succeeded", "source_commit", "wall_seconds")},
                     sort_keys=True))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
