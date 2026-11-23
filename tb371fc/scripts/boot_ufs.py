#!/usr/bin/env python3
"""Validate real qcom UFS driver block I/O, ending QEMU through its QMP socket."""
# SPDX-License-Identifier: GPL-2.0-only
import argparse, datetime, gzip, hashlib, json, re, subprocess, time
from pathlib import Path
from boot_smoke import cpio_entry, sha256
from validate_machine import QMP

def rpmh_snapshot(qmp):
    """Observe the completed real driver's requests without MMIO writes.

    The guest IRQ count establishes interrupt delivery. Reading the RSC's
    drained status and completed command establishes its acknowledgement,
    independently of Linux's cached clock-rate report and firmware XO seed.
    """
    def prop(name):
        return qmp.command("qom-get", {"path": "/machine/rpmh", "property": name})

    def words(address, count):
        raw = qmp.command("human-monitor-command", {
            "command-line": f"xp /{count}wx {address:#x}"})
        values = []
        for line in raw.splitlines():
            if ":" in line:
                values.extend(int(x, 16) for x in
                              re.findall(r"0x[0-9a-fA-F]+", line.split(":", 1)[1]))
        if len(values) != count:
            raise ValueError(f"RSC read {address:#x} returned {raw!r}")
        return {"address": address, "values": values, "raw": raw}

    result = {"observation": "Stopped guest; QOM and physical-memory reads only",
              "command_db_sha256": prop("cmd-db-sha256"),
              "reference_profile": prop("reference-cmd-db"),
              "xo": {field: prop("res.xo.lvl." + field)
                     for field in ("present", "address", "value", "enabled")},
              "irq": words(0x18220d00, 2),
              "active_commands": [words(0x18220d30 + tcs * 0x2a0, 4)
                                  for tcs in range(2)]}
    xo = result["xo"]
    checks = {
        "rpmh_xo_request_applied": xo["present"] == 1 and
            xo["value"] == 3 and xo["enabled"] == 1,
        "rpmh_active_irq_enabled_and_acknowledged":
            result["irq"]["values"] == [3, 0],
        "rpmh_completed_xo_command": any(
            address == xo["address"] and data == 3 and
            message == 0x10108 and status == 0x10100
            for message, address, data, status in
            (entry["values"] for entry in result["active_commands"])),
    }
    result["checks"] = checks
    result["passed"] = all(checks.values())
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--qemu",type=Path,required=True)
    p.add_argument("--kernel",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--timeout",type=float,default=60)
    p.add_argument("--machine",default="tb371fc")
    p.add_argument("--require-rpmh",action="store_true")
    a=p.parse_args()
    if not 0 < a.timeout <= 300:
        p.error("--timeout must be in (0, 300]")
    a.out=a.out.resolve();a.out.mkdir(parents=True,exist_ok=False)
    source=Path(__file__).resolve().parents[1]/"tests/ufs-guest.c"
    init=a.out/"init"
    compile_cmd=["aarch64-linux-gnu-gcc","-static","-Os","-Wall","-Wextra","-Werror",str(source),"-o",str(init)]
    if a.require_rpmh:compile_cmd.insert(1,"-DTB371FC_REQUIRE_RPMH")
    subprocess.run(compile_cmd,check=True)
    archive=cpio_entry("init",init.read_bytes(),0o100755,1)
    for i,name in enumerate(("dev","proc","sys"),2):
        archive+=cpio_entry(name,b"",0o040755,i)
    archive+=cpio_entry("dev/console",b"",0o020600,5,5,1)
    archive+=cpio_entry("TRAILER!!!",b"",0,6)
    initrd=a.out/"initramfs.cpio.gz"
    initrd.write_bytes(gzip.compress(archive,mtime=0))
    disk=a.out/"disk.raw"
    with disk.open("xb") as f:f.truncate(64*1024*1024)
    cmd=[str(a.qemu.resolve()),"-M",a.machine,"-accel","tcg,thread=multi","-m","8G","-smp","8",
         "-display","none","-monitor","none","-serial","stdio","-no-reboot",
         "-qmp",f"unix:{a.out}/monitor.sock,server=on,wait=off",
         "-kernel",str(a.kernel.resolve()),"-initrd",str(initrd),
         "-append","earlycon=qcom_geni,0xa90000 console=ttyMSM0,115200n8 rdinit=/init loglevel=7 panic=-1 nokaslr",
         "-blockdev",json.dumps({"driver":"file","node-name":"f0","filename":str(disk)}),
         "-blockdev","driver=raw,node-name=d0,file=f0",
         "-device","ufs-lu,bus=ufs0,drive=d0,lun=0",
         "-d","unimp,guest_errors","-D",str(a.out/"qemu-debug.log")]
    result={"schema_version":1,"started_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope":"Real Linux qcom UFS/QMP/GCC I/O on a synthetic empty disk; host QMP ends the run after guest verdict",
        "guest_shutdown_tested":False,
        "require_rpmh":a.require_rpmh,
        "expected_guest_checks":23 if a.require_rpmh else 19,
        "qemu_sha256":sha256(a.qemu),"kernel_sha256":sha256(a.kernel),
        "guest_source_sha256":sha256(source),"init_sha256":sha256(init),
        "initramfs_sha256":sha256(initrd),"command":cmd,"compile_command":compile_cmd,"passed":False}
    start=time.monotonic()
    qmp=None
    with (a.out/"serial.log").open("wb") as log:
        proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
        try:
            qmp=QMP(a.out/"monitor.sock",proc,10)
            end=time.monotonic()+a.timeout
            text=""
            while time.monotonic()<end and proc.poll() is None:
                text=(a.out/"serial.log").read_text(errors="replace")
                if "TB371FC_UFS_RESULT " in text:
                    break
                time.sleep(.05)
            result["timed_out"]="TB371FC_UFS_RESULT " not in text and proc.poll() is None
            if proc.poll() is None:
                if result["timed_out"]:
                    result["timeout_status"]=qmp.command("query-status")
                    result["timeout_cpu_registers"]=qmp.command(
                        "human-monitor-command",{"command-line":"info registers"})
                    result["timeout_clocks"]={}
                    for clock in ("se-clk","m-ahb","s-ahb"):
                        try:
                            result["timeout_clocks"][clock]=qmp.command("qom-get",
                                {"path":"/machine/uart/"+clock,"property":"qtest-clock-period"})
                        except Exception as e:
                            result["timeout_clocks"][clock]=str(e)
                if a.require_rpmh and not result["timed_out"]:
                    qmp.command("stop")
                    result["rpmh_host_observation"] = rpmh_snapshot(qmp)
                qmp.command("quit")
            proc.wait(timeout=5)
        except Exception as e:
            result["error"]=f"{type(e).__name__}: {e}"
        finally:
            if qmp:qmp.close()
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
    result["exit_code"]=proc.returncode
    result["elapsed_seconds"]=round(time.monotonic()-start,3)
    text=(a.out/"serial.log").read_text(errors="replace")
    result["guest_checks"]=re.findall(r"TB371FC_UFS_CHECK (PASS|FAIL) (\w+)",text)
    result["guest_verdicts"]=re.findall(r"TB371FC_UFS_RESULT (PASS|FAIL) checks=(\d+) failures=(\d+)",text)
    expected=bytes((i*37+0x61)&255 for i in range(32768))
    with disk.open("rb") as f:f.seek(4*1024*1024);written=f.read(32768)
    result["host_verified_disk_payload"]=written==expected
    result["disk_payload_sha256"]=hashlib.sha256(written).hexdigest()
    result["passed"]="error" not in result and not result.get("timed_out",True) and proc.returncode==0 and \
        len(result["guest_checks"])==result["expected_guest_checks"] and all(c[0]=="PASS" for c in result["guest_checks"]) and \
        result["guest_verdicts"]==[("PASS",str(result["expected_guest_checks"]),"0")] and result["host_verified_disk_payload"] and \
        (not a.require_rpmh or result.get("rpmh_host_observation", {}).get("passed", False))
    for name in ("serial.log","qemu-debug.log"):
        if (a.out/name).exists():result[name+"_sha256"]=sha256(a.out/name)
    (a.out/"result.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))
    return 0 if result["passed"] else 1

if __name__=="__main__":raise SystemExit(main())
