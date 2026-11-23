from pathlib import Path
import hashlib,json,re,sys
label,expected_qemu,expected_source=sys.argv[1:4]
assert re.fullmatch(r"native-[a-z0-9-]+",label)
assert re.fullmatch(r"[0-9a-f]{64}",expected_qemu)
assert re.fullmatch(r"[0-9a-f]{40}",expected_source)
root=Path("/work/wholeboard-a47")
p=root/label
d=json.loads((p/"diagnostic.json").read_text())
summary=json.loads((root/(label+"-summary.json")).read_text())
assert d["qemu_sha256"]==summary["qemu_sha256"]==expected_qemu
assert summary["source_commit"]==expected_source and summary["qemu_unchanged"] is True
def sha(path):
 h=hashlib.sha256()
 with path.open("rb") as stream:
  for chunk in iter(lambda:stream.read(1024*1024),b""): h.update(chunk)
 return h.hexdigest()
names=["diagnostic.json","qemu.log","serial.log","kernel-banner.bin","kernel-printk-ring.bin","kernel-printk-metadata.json","kernel-printk.log"]
raw=[{"name":name,"bytes":(p/name).stat().st_size,"sha256":sha(p/name)} for name in names if (p/name).exists()]
kernel=(p/"kernel-printk.log").read_text(errors="replace") if (p/"kernel-printk.log").exists() else ""
lines=(p/"qemu.log").read_text(errors="replace").splitlines()
invalid=[]; errors=[]
for i,line in enumerate(lines):
 if re.search(r"unmodeled|unsupported|unimplemented|failed",line,re.I):
  errors.append({"line":i+1,"text":line})
 if re.search(r"Invalid (read|write)",line):
  invalid.append({"line":i+1,"text":line,"following":lines[i+1:i+10]})
milestone_pattern=r"Registered .*clocks|KPI:|socinfo|msm_bus:|msm_bus_fabric|smmu|tbu|tsens|thermal|videocc|video_cc|noc_qos|devfreq|qcom,devfreq|cpufreq|CPUFreq|cpufreq-hw|cpu-isolat|limits-dcvs|Freeing unused|Run /init|Kernel panic|Internal error|Unable to handle|pc :|PC is at|unknown-block|init process|TB371FC_"
milestones=[line for line in kernel.splitlines() if re.search(milestone_pattern,line,re.I)]
peer=d.get("aop_peer") or {}
report={"schema":"tb371fc-native-diagnostic-extra-v1","source_commit":expected_source,"qemu_sha256":expected_qemu,"device_error_count":len(errors),"device_error_observations":errors[-100:],"invalid_mem_count":len(invalid),"invalid_mem_observations":invalid[-30:],"raw_evidence":raw,"smem_probe":d.get("smem_probe"),"kernel_milestones":milestones,"kernel_tail":kernel[-16000:]}
for key in ["noc_qos_observer","smmuv500_observer","tsens24xx_observer","cpufreq_hw_observer","devfreq_fw_observer"]:
 report[key]=peer.get(key)
 if key+"_error" in peer: report[key+"_error"]=peer[key+"_error"]
out=root/(label+"-extra.json")
data=(json.dumps(report,indent=2)+"\n").encode()
out.write_bytes(data)
print(json.dumps({"path":str(out),"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"invalid_mem_count":len(invalid),"device_error_count":len(errors),"first_invalid_access":invalid[0] if invalid else None}),flush=True)
