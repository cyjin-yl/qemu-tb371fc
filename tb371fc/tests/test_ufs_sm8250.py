#!/usr/bin/env python3
"""Execute SM8250 UFS/PHY/GCC and real block DMA checks through QTest."""
# SPDX-License-Identifier: GPL-2.0-only
from __future__ import annotations
import argparse, datetime, hashlib, json, socket, struct, subprocess, time
from pathlib import Path

HCI, PHY, GCC = 0x1d84000, 0x1d87000, 0x100000
UTRL, CMD, DATA = 0x80010000, 0x80012000, 0x80020000
ROOT = Path(__file__).resolve().parents[2]

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def connect(path, process):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if process.poll() is not None:
            raise RuntimeError(f"QEMU exited before connect: {process.returncode}")
        s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(str(path));return s
        except (FileNotFoundError,ConnectionRefusedError):
            s.close();time.sleep(.01)
    raise TimeoutError(str(path))

class Rig:
    def __init__(self,qemu,directory):
        self.d=directory
        self.irq={}
        self.events=[]
        self.connections=[]
        self.log=(directory/"qemu.stderr").open("wb")
        self.disk=directory/"disk.raw"
        with self.disk.open("xb") as f:
            f.truncate(64*1024*1024)
        self.cmd=[str(qemu),"-M","tb371fc","-accel","qtest","-m","128M",
            "-display","none","-serial","null","-monitor","none",
            "-qtest",f"unix:{directory}/qtest,server=on,wait=off",
            "-qtest-log","/dev/null",
            "-qmp",f"unix:{directory}/qmp,server=on,wait=off",
            "-blockdev",f"driver=file,node-name=file0,filename={self.disk}",
            "-blockdev","driver=raw,node-name=disk0,file=file0",
            "-device","ufs-lu,bus=ufs0,drive=disk0,lun=0",
            "-d","unimp,guest_errors"]
        self.p=subprocess.Popen(self.cmd,stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,stderr=self.log)
        try:
            q=connect(directory/"qtest",self.p);self.connections.append(q)
            self.q=q.makefile("rwb",buffering=0)
            m=connect(directory/"qmp",self.p);self.connections.append(m)
            self.m=m.makefile("rwb",buffering=0)
            assert "QMP" in json.loads(self.m.readline())
            self.monitor("qmp_capabilities")
            self.command("irq_intercept_out /machine/ufs sysbus-irq")
            if not self.monitor("query-status")["running"]:
                self.monitor("cont")
        except BaseException:
            self.close();raise
    def command(self,line):
        self.q.write((line+"\n").encode())
        while True:
            v=self.q.readline().decode().strip()
            if not v: raise RuntimeError("QTest EOF")
            if v.startswith("IRQ "):
                _,state,n=v.split();self.irq[int(n)]=state=="raise";continue
            assert v.startswith("OK"),(line,v)
            return v.split()[1:]
    def monitor(self,name,args=None):
        msg={"execute":name}
        if args is not None:msg["arguments"]=args
        self.m.write((json.dumps(msg)+"\n").encode())
        while True:
            r=json.loads(self.m.readline())
            if "event" in r:self.events.append(r);continue
            assert "error" not in r,r
            return r["return"]
    def r(self,addr):return int(self.command(f"readl {addr:#x}")[0],0)
    def w(self,addr,value):self.command(f"writel {addr:#x} {value:#x}")
    def write(self,addr,data):
        self.command(f"write {addr:#x} {len(data):#x} 0x{data.hex()}")
    def read(self,addr,n):
        return bytes.fromhex(self.command(f"read {addr:#x} {n:#x}")[0].removeprefix("0x"))
    def step(self,ns=20000):
        return int(self.command(f"clock_step {ns}")[0])
    def wait(self,predicate,label):
        end=time.monotonic()+5
        while time.monotonic()<end:
            self.step()
            if predicate():return
            time.sleep(.001)
        raise AssertionError("timeout: "+label)
    def uic(self,cmd,attr=0,value=0,selector=0):
        self.w(HCI+0x20,0xffffffff)
        self.w(HCI+0x94,(attr<<16)|selector)
        self.w(HCI+0x98,0)
        self.w(HCI+0x9c,value)
        self.w(HCI+0x90,cmd)
        self.wait(lambda:bool(self.r(HCI+0x20)&(1<<10)),"UIC completion")
        result=self.r(HCI+0x98)&0xff
        result_value=self.r(HCI+0x9c)
        self.w(HCI+0x20,0xffffffff)
        return result,result_value
    def transfer(self,req,*,data=None,buffer_size=0,direction=0,expected=0):
        self.w(HCI+0x20,0xffffffff)
        self.write(CMD,bytes(4096))
        self.write(CMD,req)
        if buffer_size:
            self.write(DATA, data if data is not None else b"\xcc"*buffer_size)
            self.write(CMD+2048,struct.pack("<QII",DATA,0,buffer_size-1))
        desc=struct.pack("<6I4H",0x11000000|direction,0,15,0,CMD,0,
                         128,256,1 if buffer_size else 0,512)
        self.write(UTRL,desc)
        self.w(HCI+0x58,1)
        self.wait(lambda:not(self.r(HCI+0x58)&1),"transfer DMA")
        status=struct.unpack("<I",self.read(UTRL+8,4))[0]&0xff
        rsp=self.read(CMD+1024,512)
        payload=self.read(DATA,buffer_size) if buffer_size else b""
        return status,rsp,payload
    def scsi(self,cdb,*,write=None,buffer_size=0,expected=None,lun=0):
        size=len(write) if write is not None else buffer_size
        edt=size if expected is None else expected
        direction=0x02000000 if write is not None else (0x04000000 if size else 0)
        flags=0x20 if write is not None else (0x40 if size else 0)
        req=bytes([1,flags,lun,0,0,0,0,0,0,0,0,0])+struct.pack(">I",edt)
        req+=bytes(cdb).ljust(16,b"\0")
        return self.transfer(req,data=write,buffer_size=size,direction=direction,expected=edt)
    def query(self, opcode, idn, value=0):
        function=1 if opcode in (1,3,5) else 0x81
        req=bytes([0x16,0,0,0,0,function,0,0,0,0,0,0])
        req+=struct.pack(">BBBBHHI2I",opcode,idn,0,0,0,0,value,0,0)
        status,rsp,_=self.transfer(req)
        return status,rsp[6],struct.unpack_from(">I",rsp,20)[0]
    def close(self):
        if hasattr(self,"m"):
            try:self.monitor("quit")
            except (OSError,ValueError,AssertionError):pass
        if hasattr(self,"p"):
            try:self.p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.p.terminate()
                try:self.p.wait(timeout=5)
                except subprocess.TimeoutExpired:self.p.kill();self.p.wait()
        for f in ("q","m"):
            if hasattr(self,f):getattr(self,f).close()
        for c in self.connections:c.close()
        self.log.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--qemu",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False)
    result={"schema_version":1,"started_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope":"Actual QTest GCC/UFS/PHY, UIC, DMA and SCSI behavior; not Android or full hardware equivalence",
        "qemu_sha256":sha(a.qemu),"test_sha256":sha(__file__),"checks":[],"passed":False}
    rig=None
    start=time.monotonic()
    def check(name,condition,detail=None):
        row={"name":name,"passed":bool(condition)}
        if detail is not None:row["detail"]=detail
        result["checks"].append(row)
        assert condition,name+": "+str(detail)
    try:
        r=rig=Rig(a.qemu.resolve(),a.out.resolve())
        result["command"]=r.cmd
        # Exercise real GCC power and clock registers; no force-ready model API.
        r.w(GCC+0x77004,0);r.step(30000)
        check("gcc_ufs_power_on",bool(r.r(GCC+0x77004)&0x80000000))
        for off in (0x77018,0x77010,0x770cc,0x7705c,0x7709c,0x8c000):
            r.w(GCC+off,1)
        r.step()
        check("hci_reference_version",r.r(HCI+8)==0x300)
        check("phy_starts_reset",r.r(PHY+0xc08)==1 and r.r(PHY+0xd80)==0)
        r.w(HCI+0x34,1);r.w(HCI+0x24,0x71fff)
        check("link_startup_without_phy_fails",r.uic(0x16)[0]!=0)
        r.w(PHY+0xc04,1);r.w(PHY+0xc08,0);r.w(PHY+0xc00,1);r.step(50000)
        check("unprogrammed_phy_not_ready",r.r(PHY+0xd80)==0)
        rows=json.loads((ROOT/"tb371fc/docs/ufs-qmp-registers.json").read_text())["registers"]
        for entry in rows:
            if entry["documented_driver_values"]:
                r.w(PHY+entry["offset"],entry["documented_driver_values"][0])
        r.step(50000)
        check("programmed_phy_ready",r.r(PHY+0xd80)==1)
        check("phy_symbols_propagate",r.monitor("qom-get",{
            "path":"/machine/ufs/tx-lane0","property":"qtest-clock-period"})>0)
        check("tx_fsm_hibern8",r.uic(1,0x41)==(0,1))
        check("link_without_symbol_clocks_fails",r.uic(0x16)[0]!=0)
        for off in (0x7701c,0x77020,0x770b8):r.w(GCC+off,1)
        r.step()
        check("link_startup",r.uic(0x16)[0]==0 and r.r(HCI+0x30)&7==7)
        check("two_connected_lanes",r.uic(1,0x1561)==(0,2) and r.uic(1,0x1581)==(0,2))
        check("unknown_dme_rejected",r.uic(1,0xbadc)[0]==1)
        check("readonly_dme_rejected",r.uic(2,0x1520,1)[0]==3)
        check("invalid_lane_count_rejected",r.uic(2,0x1560,3)[0]==2)
        check("dme_write_read_retained",r.uic(2,0x9007,12)[0]==0 and r.uic(1,0x9007)==(0,12))
        for attr,value in ((0xd041,8191),(0xd042,65535),(0xd043,32767)):
            check(f"local_timeout_{attr:x}_programming",
                  r.uic(2,attr,value)[0]==0 and r.uic(1,attr)==(0,value))
        check("power_mode_change",r.uic(2,0x1571,0x11)[0]==0 and r.uic(1,0x1571)==(0,0x11))
        r.w(HCI+0x50,UTRL);r.w(HCI+0x54,0);r.w(HCI+0x60,1)
        status,rsp,_=r.transfer(bytes(32))
        check("nop_dma_and_response",status==0 and rsp[0]==0x20,{"ocs":status,"response":rsp[:12].hex()})
        req=bytes([0x16,0,0,0,0,1,0,0,0,0,0,0])+struct.pack(">BBBBHHI2I",1,0,0,0,0,255,0,0,0)
        status,rsp,_=r.transfer(req)
        check("device_descriptor",status==0 and rsp[6]==0 and
              struct.unpack_from(">H",rsp,32+16)[0]==0x310 and
              struct.unpack_from(">H",rsp,32+24)[0]==0x1ce,
              {"ocs":status,"header":rsp[:12].hex(),"descriptor":rsp[32:64].hex()})
        for _ in range(3):
            status,rsp,_=r.scsi([0,0,0,0,0,0])
            if status==0 and rsp[7]==0:break
            r.scsi([3,0,0,0,18,0],buffer_size=4096)
        check("scsi_lun_ready",status==0 and rsp[7]==0,{"ocs":status,"status":rsp[7]})
        status,rsp,buf=r.scsi([0x12,0,0,0,36,0],buffer_size=36,lun=1)
        check("absent_lun_standard_inquiry",status==0 and rsp[0]==0x21 and
              rsp[7]==0 and buf==bytes([0x7f])+bytes(35),
              {"ocs":status,"status":rsp[7],"payload":buf.hex()})
        status,rsp,buf=r.scsi([3,0,0,0,18,0],buffer_size=18,lun=1)
        check("absent_lun_request_sense",status==0 and rsp[7]==0 and
              buf[0]==0x70 and buf[2]==5 and buf[12:14]==bytes([0x25,0]),
              {"ocs":status,"status":rsp[7],"payload":buf.hex()})
        status,rsp,buf=r.scsi([0x25]+[0]*9,buffer_size=8,lun=1)
        check("absent_lun_scsi_error_not_transport_failure",status==0 and
              rsp[7]==2 and rsp[36]==5 and rsp[46:48]==bytes([0x25,0]) and
              struct.unpack_from(">I",rsp,12)[0]==8 and buf==b"\xcc"*8,
              {"ocs":status,"response":rsp[:52].hex()})
        high_luns=[]
        for lun in (32,0x7f,0xff):
            status,rsp,_=r.scsi([0]*6,lun=lun)
            high_luns.append({"lun":lun,"ocs":status,"status":rsp[7],
                              "sense":rsp[34:52].hex()})
            assert status==0 and rsp[7]==2 and rsp[36]==5 and rsp[46]==0x25
        check("absent_high_and_unknown_wlun_rejected",True,high_luns)
        invalid_fields=[]
        for flags in (1,2):  # EVPD and the unsupported CMDT bit
            status,rsp,buf=r.scsi([0x12,flags,0,0,36,0],buffer_size=36,lun=1)
            invalid_fields.append({"flags":flags,"passed":status==0 and
                rsp[7]==2 and rsp[36]==5 and rsp[46:48]==bytes([0x24,0]) and
                buf==b"\xcc"*36})
        check("absent_lun_invalid_inquiry_field",
              all(row["passed"] for row in invalid_fields),invalid_fields)
        status,rsp,buf=r.scsi([0x12,0,0,0,36,0],buffer_size=16,expected=36,lun=1)
        check("absent_lun_short_prdt_rejected",status==3 and buf==b"\xcc"*16,
              {"ocs":status,"payload":buf.hex()})
        status,rsp,buf=r.scsi([0x12,0,0,0,36,0],buffer_size=64,lun=1)
        check("absent_lun_inquiry_dma_bounds",status==0 and rsp[7]==0 and
              rsp[1]&0x20 and struct.unpack_from(">I",rsp,12)[0]==28 and
              buf==bytes([0x7f])+bytes(35)+b"\xcc"*28)
        status,rsp,buf=r.scsi([0x12,0,0,0,4,0],buffer_size=8,lun=1)
        check("absent_lun_inquiry_allocation_bounds",status==0 and rsp[7]==0 and
              struct.unpack_from(">I",rsp,12)[0]==4 and
              buf==bytes([0x7f,0,0,0])+b"\xcc"*4)
        status,rsp,buf=r.scsi([0x25,0,0,0,0,0,0,0,0,0],buffer_size=8)
        check("read_capacity",status==0 and rsp[7]==0 and struct.unpack(">II",buf)==(16383,4096),
              {"ocs":status,"data":buf.hex()})
        check("enable_write_booster",r.query(6,0x0e)==(0,0,1))
        check("wb_hibern8_flush_flag_set",r.query(6,0x10)==(0,0,1))
        check("wb_hibern8_flush_flag_read",r.query(5,0x10)==(0,0,1))
        check("wb_hibern8_flush_flag_clear",r.query(7,0x10)==(0,0,0))
        check("wb_hibern8_flush_flag_toggle",r.query(8,0x10)==(0,0,1))
        pattern=bytes((i*37+11)&255 for i in range(4096))
        write_cdb=[0x2a,0,0,0,0,4,0,0,1,0]
        read_cdb=[0x28,0,0,0,0,4,0,0,1,0]
        status,rsp,_=r.scsi(write_cdb,write=pattern)
        check("write10",status==0 and rsp[7]==0,{"ocs":status,"status":rsp[7]})
        status,rsp,buf=r.scsi(read_cdb,buffer_size=4096)
        check("read10_exact_payload",status==0 and rsp[7]==0 and buf==pattern,
              {"sha256":hashlib.sha256(buf).hexdigest()})
        r.scsi([0x35,0,0,0,0,0,0,0,0,0])
        with r.disk.open("rb") as f:f.seek(4*4096);host=f.read(4096)
        check("host_backing_file_contains_write",host==pattern)
        status,rsp,_=r.scsi([0x2a,0,0,0,0,6,0,0,2,0],write=pattern,expected=8192)
        check("short_prdt_rejected",status==3,{"ocs":status})
        status,rsp,_=r.scsi([0x2a,0,0,0,0,8,0,0,1,0],write=pattern*2)
        check("long_prdt_reports_underflow",status==0 and rsp[7]==0 and rsp[1]&0x20 and
              struct.unpack_from(">I",rsp,12)[0]==4096,{"ocs":status,"response":rsp[:32].hex()})
        status,rsp,buf=r.scsi([0x28,0,0,0,0,9,0,0,1,0],buffer_size=4096)
        check("long_prdt_did_not_write_adjacent_lba",status==0 and buf==bytes(4096))
        status,rsp,buf=r.scsi([0x28,0,0,0,0,6,0,0,2,0],buffer_size=8192)
        check("short_prdt_did_not_write_any_lba",status==0 and buf==bytes(8192))
        status,rsp,_=r.scsi([0xff]+[0]*15)
        check("unsupported_scsi_check_condition",status==0 and rsp[7]==2)
        # The upstream WB timer uses VIRTUAL_RT, not the QTest virtual clock.
        # Keep the VM running for three real timer periods; do not alter it.
        time.sleep(.35)
        check("hibern8_only_flush_stays_pending_while_active",r.query(3,0x1d)==(0,0,9))
        r.w(GCC+0x7701c,0);r.step()
        r.w(HCI+0x58,1)
        check("gated_tx_sync_clock_blocks_dma",r.r(HCI+0x58)==0)
        r.w(GCC+0x7701c,1);r.step()
        status,rsp,_=r.transfer(bytes(32))
        check("restored_tx_sync_clock_allows_dma",status==0 and rsp[0]==0x20)
        check("enter_hibern8",r.uic(0x17)[0]==0 and r.uic(1,0x41)==(0,1))
        r.w(PHY+0xc08,1);r.w(PHY+0xc00,0);r.w(PHY+0xc04,0)
        r.w(GCC+0x8c000,0);r.step()
        check("hibern8_retains_link_across_phy_power_down",
              r.r(HCI+0x30)&1 and r.r(PHY+0xd80)==0)
        time.sleep(.35)
        r.w(GCC+0x8c000,1);r.step()
        r.w(PHY+0xc04,1);r.w(PHY+0xc08,0);r.w(PHY+0xc00,1);r.step(50000)
        check("hibern8_phy_restored",r.r(PHY+0xd80)==1)
        r.w(HCI+0x58,1)
        check("transfer_in_hibern8_rejected",r.r(HCI+0x58)==0)
        check("exit_hibern8",r.uic(0x18)[0]==0)
        wb_available=r.query(3,0x1d)
        check("hibern8_flushed_write_booster",wb_available==(0,0,10),wb_available)
        r.w(GCC+0x8c000,0);r.step()
        check("gated_qref_stops_phy_and_link",r.r(PHY+0xd80)==0 and not(r.r(HCI+0x30)&1))
        r.w(GCC+0x8c000,1);r.step(50000)
        check("restored_qref_restarts_phy",r.r(PHY+0xd80)==1 and r.uic(0x16)[0]==0)
        r.w(GCC+0x77000,1);r.w(GCC+0x77000,0);r.step()
        check("gcc_bcr_resets_hci_and_phy",r.r(HCI+0x34)==0 and r.r(PHY+0xd80)==0 and r.r(PHY+0xc08)==1)
        result["passed"]=True
    except Exception as e:
        result["error"]=f"{type(e).__name__}: {e}"
    finally:
        if rig:
            rig.close()
            result["qemu_exit_code"]=rig.p.returncode
            result["stderr_sha256"]=sha(a.out/"qemu.stderr")
        result["elapsed_seconds"]=round(time.monotonic()-start,3)
        result["passed_checks"]=sum(c["passed"] for c in result["checks"])
        result["total_checks"]=len(result["checks"])
        (a.out/"result.json").write_text(json.dumps(result,indent=2)+"\n")
        print(json.dumps(result,indent=2))
    return 0 if result["passed"] else 1

if __name__=="__main__":raise SystemExit(main())
