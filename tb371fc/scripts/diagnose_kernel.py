#!/usr/bin/env python3
"""Bounded diagnostic execution of a supplied kernel, preserving QMP events."""
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import select
import socket
import subprocess
import time
from legacy_printk import capture_kernel_log
from observe_aop import capture_aop_peer, initial_observation, reference_profile_selected

def sha256(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--qemu",type=Path,required=True)
    p.add_argument("--machine",default="tb371fc",
                   help="TB371FC machine plus explicit device-profile properties")
    p.add_argument("--global-property", action="append", default=[],
                   metavar="DRIVER.PROPERTY=VALUE",
                   help="Explicit QEMU device property; recorded in the diagnostic")
    p.add_argument("--kernel",type=Path,required=True)
    p.add_argument("--initrd",type=Path,required=True)
    p.add_argument("--dtb",type=Path)
    p.add_argument("--append",required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--seconds",type=float,default=15)
    p.add_argument("--trace-code",action="store_true")
    p.add_argument("--legacy-printk-symbols",type=Path,
                   help="Matching Linux 4.x kallsyms; capture stopped guest log privately")
    p.add_argument("--observe-smem-probe", action="store_true",
                   help="Read the verified production kernel's published SMEM driver object")
    p.add_argument("--observe-aop-peer", action="store_true",
                   help="Read stopped reference AOP transport and clock-vote state")
    a=p.parse_args()
    if a.seconds<=0 or a.seconds>120:
        p.error("--seconds must be in (0, 120]")
    if a.machine.split(",", 1)[0] != "tb371fc":
        p.error("--machine must select tb371fc")
    if a.observe_aop_peer and not reference_profile_selected(a.machine):
        p.error("--observe-aop-peer requires exactly one explicit "
                "aop-profile=reference-qdss-vote machine option")
    if a.observe_smem_probe and (not a.legacy_printk_symbols or
                                 "nokaslr" not in a.append.split()):
        p.error("--observe-smem-probe requires matching symbols and nokaslr")
    for name in ("qemu", "kernel", "initrd", "dtb", "legacy_printk_symbols"):
        path = getattr(a, name)
        if path is not None and not path.is_file():
            p.error(f"--{name.replace('_', '-')} is not a regular file: {path}")
    out=a.out.resolve()
    out.mkdir(parents=True,exist_ok=False)
    qmp=out/"monitor.sock"
    if len(str(qmp).encode())>=104:
        p.error("--out is too long for a Unix socket")
    cmd=[str(a.qemu.resolve()),"-M",a.machine,"-accel","tcg,thread=multi",
         "-m","8G","-smp","8","-display","none","-monitor","none",
         "-serial","stdio","-no-reboot","-no-shutdown","-S",
         "-qmp",f"unix:{qmp},server=on,wait=off",
         "-kernel",str(a.kernel.resolve()),"-initrd",str(a.initrd.resolve()),
         "-append",a.append,"-d","unimp,guest_errors,int,cpu_reset"+
         (",in_asm" if a.trace_code else ""),"-D",str(out/"qemu.log")]
    if a.dtb:
        cmd+=["-dtb",str(a.dtb.resolve())]
    for value in a.global_property:
        cmd += ["-global", value]
    report={"schema_version":1,"scope":"Diagnostic only; no success verdict",
            "global_properties":a.global_property,
            "command":cmd,"kernel_sha256":sha256(a.kernel),
            "qemu_sha256":sha256(a.qemu),"machine":a.machine,"events":[]}
    report["aop_peer_requested"] = a.observe_aop_peer
    if a.observe_aop_peer:
        report["aop_peer"] = initial_observation(a.machine)
    report["smem_probe_requested"] = a.observe_smem_probe
    if a.observe_smem_probe:
        report["smem_probe"] = {
            "success_path_observed": False,
            "verdict": "observation-not-performed",
            "reason": "Guest did not reach a stopped observation point",
        }
    if a.dtb:
        report["dtb_sha256"]=sha256(a.dtb)
    start=time.monotonic()
    sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    buffer=b""
    next_id=0
    def send(name,args=None):
        nonlocal next_id
        next_id+=1
        obj={"execute":name,"id":next_id}
        if args is not None: obj["arguments"]=args
        sock.sendall(json.dumps(obj).encode()+b"\n")
        return next_id
    def receive(wait):
        nonlocal buffer
        if not select.select([sock],[],[],wait)[0]: return []
        part=sock.recv(65536)
        if not part: return None
        buffer+=part
        messages=[]
        while b"\n" in buffer:
            line,buffer=buffer.split(b"\n",1)
            if line.strip(): messages.append(json.loads(line))
        return messages
    def command(name,args=None):
        ident=send(name,args)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            messages=receive(.1)
            if messages is None: raise RuntimeError("QMP closed")
            report["events"]+=messages
            for message in messages:
                if message.get("id")==ident:
                    if "error" in message: raise RuntimeError(str(message["error"]))
                    return message["return"]
        raise TimeoutError("QMP command: "+name)
    with (out/"serial.log").open("wb") as serial:
        proc=subprocess.Popen(cmd,stdout=serial,stderr=subprocess.STDOUT)
        try:
            deadline=start+5
            while True:
                try:
                    sock.connect(str(qmp));break
                except (FileNotFoundError,ConnectionRefusedError):
                    if proc.poll() is not None or time.monotonic()>deadline: raise
                    time.sleep(.025)
            sock.setblocking(True)
            ready=False
            while not ready:
                messages=receive(1)
                if messages is None: raise RuntimeError("QMP closed before greeting")
                ready=any("QMP" in x for x in messages)
                report["events"]+=messages
            send("qmp_capabilities")
            while True:
                messages=receive(1)
                if messages is None: raise RuntimeError("QMP closed during negotiation")
                report["events"]+=messages
                if any(x.get("id")==1 and "return" in x for x in messages):break
                if any("error" in x for x in messages):raise RuntimeError("QMP negotiation failed")
            send("cont")
            inspected=False
            report["stopped_by_timeout"]=False
            while proc.poll() is None:
                elapsed=time.monotonic()-start
                if not inspected and elapsed>1:
                    send("human-monitor-command",{"command-line":"info registers"})
                    send("query-cpus-fast")
                    inspected=True
                if elapsed>a.seconds:
                    report["stopped_by_timeout"]=True
                    command("stop")
                    send("human-monitor-command",{"command-line":"info registers"})
                    for _ in range(4):
                        messages=receive(.1)
                        if messages is None:break
                        report["events"]+=messages
                    if a.legacy_printk_symbols:
                        try:
                            report["legacy_printk"]=capture_kernel_log(
                                command,a.legacy_printk_symbols,out)
                        except Exception as exc:
                            report["legacy_printk_error"]=str(exc)
                    if a.observe_smem_probe:
                        from observe_smem import capture_smem_probe
                        report["smem_probe"] = capture_smem_probe(
                            command, a.legacy_printk_symbols, a.kernel)
                    if a.observe_aop_peer:
                        report["aop_peer"] = capture_aop_peer(command, a.machine)
                    send("quit")
                    break
                messages=receive(.1)
                if messages is None:break
                report["events"]+=messages
        except Exception as exc:
            report["observer_error"]=str(exc)
        finally:
            sock.close()
            if proc.poll() is None:
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill();proc.wait()
            report["exit_code"]=proc.returncode
    report["elapsed_seconds"]=round(time.monotonic()-start,3)
    for name in ("serial.log","qemu.log"):
        path=out/name
        if path.exists():
            report[name+"_bytes"]=path.stat().st_size
            report[name+"_sha256"]=sha256(path)
    (out/"diagnostic.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({key:value for key,value in report.items()
                      if key not in ("command","events")},indent=2))
    print(json.dumps([x for x in report["events"] if "event" in x],indent=2))
    return 0 if "observer_error" not in report else 1

if __name__=="__main__":
    raise SystemExit(main())
