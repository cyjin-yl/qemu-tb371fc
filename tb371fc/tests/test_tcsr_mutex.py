#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the actual board TCSR mutex and VMState through QMP and qtest.

Unknown/error accesses are observed through callback diagnostics and absence of
state mutation. qtest's wire-level OK is not treated as a MemTxResult proof.
This gate does not run a guest CPU exception handler or model remote firmware.
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import socket
import subprocess
import tempfile
import time
import traceback

BASE = 0x01F40000
STRIDE = 0x1000
COUNT = 32
MARKER_ADDR = 0x80002000
EXPECTED_CHECKS = 24

def sha256(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()

class Rig:
    def __init__(self, qemu, directory, reference=False, incoming=False):
        self.directory = pathlib.Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.socket_dir = pathlib.Path(tempfile.mkdtemp(prefix="tb371fc-mutex-"))
        self.qmp_path = self.socket_dir / "qmp"
        self.qtest_path = self.socket_dir / "qtest"
        self.stderr_path = self.directory / "stderr.log"
        self.diag_path = self.directory / "mmio.log"
        self.stdout_file = (self.directory / "stdout.log").open("wb")
        self.stderr_file = self.stderr_path.open("wb")
        self.buffers = {}
        self.events = []
        self.serial = 0
        self.qmp = None
        self.qtest = None
        self.closed = False
        command = [
            str(qemu), "-machine", "tb371fc,ufs-enabled=off",
            "-m", "128M", "-accel", "qtest", "-S",
            "-display", "none", "-serial", "null", "-monitor", "none",
            "-qmp", "unix:" + str(self.qmp_path) + ",server=on,wait=off",
            "-qtest", "unix:" + str(self.qtest_path) + ",server=on,wait=off",
            "-qtest-log", "/dev/null", "-d", "unimp,guest_errors",
            "-D", str(self.diag_path),
        ]
        if reference:
            command += ["-global", "qcom-tcsr-mutex.boot-unlocked-reference=on"]
        if incoming:
            command += ["-incoming", "defer"]
        self.command_line = command
        (self.directory / "command.json").write_text(
            json.dumps(command, indent=2) + "\n", encoding="utf-8")
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=self.stdout_file,
                                        stderr=self.stderr_file)
        try:
            self.qmp = self.connect(self.qmp_path)
            self.greeting = json.loads(self.readline(self.qmp))
            if "QMP" not in self.greeting:
                raise RuntimeError("missing QMP greeting")
            self.command("qmp_capabilities")
            self.qtest = self.connect(self.qtest_path)
        except BaseException:
            self.close()
            raise

    def connect(self, path):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("QEMU exited before handshake: " +
                                   self.stderr_path.read_text(errors="replace"))
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(str(path))
                s.settimeout(10)
                self.buffers[s] = b""
                return s
            except (FileNotFoundError, ConnectionRefusedError):
                s.close()
                time.sleep(0.01)
        raise TimeoutError("QEMU socket handshake timed out")

    def readline(self, s):
        b = self.buffers[s]
        while b"\n" not in b:
            chunk = s.recv(65536)
            if not chunk:
                raise EOFError("QEMU socket closed")
            b += chunk
        line, rest = b.split(b"\n", 1)
        self.buffers[s] = rest
        return line.decode("utf-8")

    def command(self, execute, arguments=None):
        self.serial += 1
        ident = self.serial
        request = {"execute": execute, "id": ident}
        if arguments is not None:
            request["arguments"] = arguments
        self.qmp.sendall((json.dumps(request) + "\n").encode())
        while True:
            reply = json.loads(self.readline(self.qmp))
            if "event" in reply:
                self.events.append(reply)
                continue
            if reply.get("id") != ident:
                raise RuntimeError("unexpected QMP response id")
            if "error" in reply:
                raise RuntimeError(json.dumps(reply["error"]))
            return reply["return"]

    def reset(self):
        self.events = [x for x in self.events if x.get("event") != "RESET"]
        self.command("system_reset")
        while not any(x.get("event") == "RESET" for x in self.events):
            reply = json.loads(self.readline(self.qmp))
            if "event" in reply:
                self.events.append(reply)
            else:
                raise RuntimeError("unexpected response while waiting for reset")

    def qt(self, text):
        self.qtest.sendall((text + "\n").encode())
        reply = self.readline(self.qtest).strip()
        if reply != "OK" and not reply.startswith("OK "):
            raise RuntimeError("qtest protocol failure: " + reply)
        return reply[2:].strip()

    def read(self, addr, size=4):
        op = {1: "readb", 2: "readw", 4: "readl", 8: "readq"}[size]
        return int(self.qt(op + " " + hex(addr)), 0)

    def write(self, addr, value, size=4):
        op = {1: "writeb", 2: "writew", 4: "writel", 8: "writeq"}[size]
        self.qt(op + " " + hex(addr) + " " + hex(value))

    def owner(self, slot):
        return self.read(BASE + slot * STRIDE)

    def claim(self, slot, value):
        self.write(BASE + slot * STRIDE, value)

    def device_path(self):
        children = self.command("qom-list", {"path": "/machine"})
        found = [x["name"] for x in children
                 if x["type"] == "child<qcom-tcsr-mutex>"]
        if len(found) != 1:
            raise AssertionError("expected one actual board TCSR mutex child")
        return "/machine/" + found[0]

    def profile(self):
        return self.command("qom-get", {
            "path": self.device_path(), "property": "boot-unlocked-reference"})

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.qmp is not None and self.process.poll() is None:
            try:
                self.command("quit")
            except (EOFError, OSError, RuntimeError):
                pass
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        for s in (self.qmp, self.qtest):
            if s is not None:
                s.close()
        self.stdout_file.close()
        self.stderr_file.close()

    def diagnostic(self):
        return self.diag_path.read_text(errors="replace")

def migrate(source, target, expect_rejection=False):
    uri = "unix:" + str(target.socket_dir / "migration")
    target.command("migrate-incoming", {"uri": uri})
    source.command("migrate", {"uri": uri})
    deadline = time.monotonic() + 20
    outgoing = {}
    incoming = {}
    while time.monotonic() < deadline:
        outgoing = source.command("query-migrate")
        if target.process.poll() is not None:
            if expect_rejection:
                break
            raise RuntimeError("migration destination exited")
        try:
            incoming = target.command("query-migrate")
        except (EOFError, OSError):
            if not expect_rejection:
                raise
        if not expect_rejection:
            if outgoing.get("status") == incoming.get("status") == "completed":
                return {"source": outgoing, "destination": incoming,
                        "expected_rejection": False}
            if outgoing.get("status") in ("failed", "cancelled"):
                raise RuntimeError("migration failed: " + json.dumps(outgoing))
        elif target.process.poll() is not None or incoming.get("status") == "failed":
            break
        time.sleep(0.01)
    else:
        raise TimeoutError("migration did not reach its expected outcome")
    target.close()
    return {"source": outgoing, "destination": incoming,
            "expected_rejection": True,
            "destination_exit": target.process.returncode,
            "destination_stderr": target.stderr_path.read_text(errors="replace")}

def run(args):
    out = pathlib.Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    qemu = pathlib.Path(args.qemu).resolve(strict=True)
    started = time.monotonic()
    result = {
        "schema": "tb371fc-tcsr-mutex-test-v1",
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "qemu_sha256": sha256(qemu),
        "script_sha256": sha256(__file__),
        "checks": [],
        "expected_checks": EXPECTED_CHECKS,
        "migrations": [],
        "native_reset_observed": False,
        "external_processor_emulated": False,
        "memtx_error_observation": (
            "callback diagnostics plus non-mutation; qtest wire OK is not "
            "interpreted as MemTxResult; no guest exception-handler test"),
        "passed": False,
        "all_passed": False,
    }
    rigs = []
    def rig(name, reference=False, incoming=False):
        r = Rig(qemu, out / name, reference, incoming)
        rigs.append(r)
        return r
    def check(name, condition, observed=None):
        item = {"name": name, "passed": bool(condition)}
        if observed is not None:
            item["observed"] = observed
        result["checks"].append(item)
        if not condition:
            raise AssertionError(name)
    try:
        u = rig("unknown-claim")
        result["qmp_version"] = u.command("query-version")
        check("instance_present_and_default_profile_unknown", u.profile() is False)
        # No release/reset may erase an erroneous claim before it is observed.
        # Ignore error-carrier read data: qtest wire OK is not MemTxResult.
        u.owner(3)
        u.claim(3, 1)
        u.owner(3)
        u.close()
        log = u.diagnostic()
        check("unknown_read_and_claim_report_missing_state",
              log.count("owner state unknown for lock 3") == 2 and
              log.count("cannot claim lock 3 with unknown owner state") == 1)

        u = rig("unknown-release-reset")
        u.claim(3, 0)
        a = u.owner(3)
        u.claim(3, 1)
        check("explicit_release_establishes_known_free_slot",
              a == 0 and u.owner(3) == 1)
        u.owner(4)
        u.reset()
        u.owner(3)
        u.close()
        log = u.diagnostic()
        check("other_slot_remains_unknown_after_release",
              log.count("owner state unknown for lock 4") == 1)
        check("unknown_reset_restores_unknown_state",
              log.count("owner state unknown for lock 3") == 1)

        r = rig("reference", reference=True)
        check("explicit_reference_property", r.profile() is True)
        check("all_32_slots_start_known_free",
              [r.owner(i) for i in range(COUNT)] == [0] * COUNT)
        for i in range(COUNT):
            r.claim(i, i + 1)
        check("independent_first_claims",
              [r.owner(i) for i in range(COUNT)] == list(range(1, COUNT + 1)))
        for i in range(COUNT):
            r.claim(i, 0x100 + i)
        check("competing_claim_retains_winner",
              [r.owner(i) for i in range(COUNT)] == list(range(1, COUNT + 1)))
        r.claim(0, 1)
        r.claim(0, 0)
        check("repeated_owner_has_no_recursion", r.owner(0) == 0)
        r.claim(0, 2)
        r.claim(0, 0)
        check("zero_release_has_no_owner_acl", r.owner(0) == 0)
        r.claim(0, 0x80000001)
        check("high_bit_owner_word_roundtrip_reference",
              r.owner(0) == 0x80000001)
        before = [r.owner(i) for i in range(COUNT)]
        for hole in (BASE + 4, BASE + 0xFFC, BASE + 0x1FFFC):
            r.write(hole, 0)
            r.read(hole)
        check("holes_do_not_alias_live_words",
              [r.owner(i) for i in range(COUNT)] == before)
        for addr, width in ((BASE, 1), (BASE, 2), (BASE, 8),
                            (BASE + 1, 4), (BASE + 2, 4)):
            r.write(addr, 0, width)
            r.read(addr, width)
        check("invalid_width_alignment_do_not_mutate_state",
              [r.owner(i) for i in range(COUNT)] == before)
        r.reset()
        check("reference_reset_restores_free_state",
              [r.owner(i) for i in range(COUNT)] == [0] * COUNT)
        # Re-create held/free state for real migration.
        r.claim(3, 1)
        r.claim(31, 2)
        r.write(MARKER_ADDR, 0x6D757478)
        d = rig("reference-incoming", reference=True, incoming=True)
        result["migrations"].append(migrate(r, d))
        check("held_owner_survives_actual_migration",
              d.owner(3) == 1 and d.owner(31) == 2)
        check("known_free_slot_survives_migration", d.owner(4) == 0)
        check("ram_marker_survives_migration", d.read(MARKER_ADDR) == 0x6D757478)
        d.claim(3, 2)
        held = d.owner(3)
        d.claim(3, 0)
        d.claim(3, 2)
        check("post_migration_competition_release", held == 1 and d.owner(3) == 2)
        r.close()
        d.close()
        log = r.diagnostic()
        check("unsupported_access_diagnostics",
              all(x in log for x in (
                  "unsupported offset 0x4 size 4",
                  "unsupported offset 0xffc size 4",
                  "unsupported offset 0x1fffc size 4",
                  "unsupported offset 0x0 size 1",
                  "unsupported offset 0x0 size 2",
                  "unsupported offset 0x0 size 8",
                  "unsupported offset 0x1 size 4",
                  "unsupported offset 0x2 size 4")))

        p = rig("partial")
        p.claim(3, 0)
        p.claim(3, 1)
        p.claim(5, 0)
        pd = rig("partial-incoming", incoming=True)
        result["migrations"].append(migrate(p, pd))
        check("partial_known_bitmap_survives_actual_migration",
              pd.owner(3) == 1 and pd.owner(5) == 0 and pd.profile() is False)
        pd.owner(4)
        pd.claim(4, 2)
        pd.claim(3, 0)
        pd.claim(3, 2)
        check("migrated_known_slot_continues", pd.owner(3) == 2)
        p.close()
        pd.close()
        log = pd.diagnostic()
        check("unknown_slot_still_reports_missing_state_after_migration",
              "owner state unknown for lock 4" in log and
              "cannot claim lock 4 with unknown owner state" in log)

        bad_source = rig("mismatch-source")
        bad_source.claim(3, 0)
        bad_source.claim(3, 1)
        bad_target = rig("mismatch-incoming", reference=True, incoming=True)
        # Verify both endpoints were alive with the intended different profiles.
        if bad_source.profile() is not False or bad_target.profile() is not True:
            raise AssertionError("mismatch endpoints do not have distinct profiles")
        rejection = migrate(bad_source, bad_target, expect_rejection=True)
        result["migrations"].append(rejection)
        err = rejection["destination_stderr"]
        check("different_reset_profile_rejected",
              rejection["destination_exit"] not in (None, 0) and
              "qcom-tcsr-mutex" in err and
              ("load" in err.lower() or "invalid argument" in err.lower()))
        bad_source.close()
        result["all_passed"] = (
            len(result["checks"]) == EXPECTED_CHECKS and
            all(x["passed"] for x in result["checks"]) and
            len(result["migrations"]) == 3 and
            sum(not x["expected_rejection"] for x in result["migrations"]) == 2)
        result["passed"] = result["all_passed"]
    except BaseException as e:
        result["error"] = type(e).__name__ + ": " + str(e)
        result["traceback"] = traceback.format_exc()
    finally:
        for r in reversed(rigs):
            r.close()
        result["elapsed_seconds"] = time.monotonic() - started
        (out / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"],
                      "checks": len(result["checks"]),
                      "expected": EXPECTED_CHECKS,
                      "out": str(out)}, sort_keys=True))
    return 0 if result["passed"] else 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--out", required=True)
    raise SystemExit(run(parser.parse_args()))
