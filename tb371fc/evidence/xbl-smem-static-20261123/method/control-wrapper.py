import datetime
import hashlib
import json
import os
import stat
import sys
import re
import resource
import selectors
import struct
import subprocess
import time

ARCHIVE_PATH = "/data/data/com.termux/files/home/qemu-tb371fc-probe/private-xbl-20261123-01/active-a.tar.gz"
EXPECTED_SIZE = 2849209
EXPECTED_SHA = "30a30d8670bd69579d2a250ddcc542b99f3140d062a8b95cb61c09bfd8f1671e"
PARSER_SHA = "7d1e2a9cfaece31e3d5f6dbf4b4ffce84561f913f1b493e7110f930912493ad1"
ARCHIVE_VALIDATOR_SHA = "24f3cd6446a9e25ef2b41f66b38d66e6f89cef687a08dd90104fe3ef727eff7e"
OUTPUT_LIMIT = 12288

def sha(b):
    return hashlib.sha256(b).hexdigest()
def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
def require(ok, code):
    if not ok:
        raise RuntimeError(code)
def key(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
def compact(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")

start=now()
overall_deadline=time.monotonic()+17.0
positive_control=None
private_submitted=False
subprocess_receipts=[]
stage="verify_reused_sources"
archive_ok=False
result=None
try:
    require(sha(PARSER_SOURCE.encode())==PARSER_SHA,"parser_source_hash_mismatch")
    require(sha(ARCHIVE_VALIDATOR_SOURCE.encode())==ARCHIVE_VALIDATOR_SHA,"archive_validator_source_hash_mismatch")
    p={"__name__":"xbl_metadata_readonly_functions"}
    a={"__name__":"xbl_archive_readonly_validation"}
    exec(compile(PARSER_SOURCE,"<frozen-xbl-metadata-functions>","exec"),p)
    exec(compile(ARCHIVE_VALIDATOR_SOURCE,"<bounded-xbl-archive-validation>","exec"),a)
    stage="read_existing_archive"
    require(hasattr(os,"O_NOATIME"),"O_NOATIME_unavailable")
    fd=os.open(ARCHIVE_PATH,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NOATIME)
    try:
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode),"archive_not_regular")
        require(before.st_size==EXPECTED_SIZE,"archive_size_mismatch")
        chunks=[];remaining=EXPECTED_SIZE+1
        while remaining:
            chunk=os.read(fd,min(remaining,1024*1024))
            if not chunk:
                break
            chunks.append(chunk);remaining-=len(chunk)
        archive=b"".join(chunks)
        after=os.fstat(fd)
        require(key(before)==key(after),"archive_changed_during_read")
    finally:
        os.close(fd)
    require(len(archive)==EXPECTED_SIZE and sha(archive)==EXPECTED_SHA,"archive_hash_mismatch")
    stage="validate_three_members"
    members,capture,tar_size=a["validate_archive"](archive)
    archive_ok=True
    stage="validate_original_slice_geometry"
    data=members["xbl_a.bin"]
    expected_size,expected_sha=p["EXPECTED"]["xbl_a"]
    require(len(data)==expected_size and sha(data)==expected_sha,"input_member_mismatch")
    elf=p["elf_metadata"](data)
    require(elf.get("header_decoded") and elf.get("program_table_valid") and not elf.get("errors"),"invalid_elf")
    require(elf.get("elf_class")==2 and elf.get("byte_order")==1 and elf.get("machine")==183,"elf_arch_mismatch")
    segments=elf["segments"]
    original=next(s for s in segments if s["index"]==2)
    require(original["type"]==1 and original["flags"]==5 and original["file_offset"]==12288 and
            original["filesz"]==388492 and original["vaddr"]=="0x14815000","original_load_mismatch")
    source_offset=24336
    source_size=4096
    source_va=0x14817f10
    require(source_offset-original["file_offset"]+int(original["vaddr"],16)==source_va and
            original["file_offset"]<=source_offset and
            source_offset+source_size<=original["file_offset"]+original["filesz"],"slice_outside_load")

    def analysis_elf(code,va):
        require(len(code)>0 and len(code)%4==0 and va%4==0,"analysis_geometry")
        names=b"\0.text\0.shstrtab\0"
        textoff=64
        nameoff=textoff+len(code)
        shoff=(nameoff+len(names)+7)&~7
        ident=b"\x7fELF"+bytes((2,1,1,0,0))+bytes(7)
        header=struct.pack("<16sHHIQQQIHHHHHH",ident,2,183,1,0,0,shoff,0,64,0,0,64,3,2)
        section0=bytes(64)
        section1=struct.pack("<IIQQQQIIQQ",1,1,6,va,textoff,len(code),0,0,4,0)
        section2=struct.pack("<IIQQQQIIQQ",7,3,0,0,nameoff,len(names),0,0,1,0)
        derived=header+code+names+bytes(shoff-nameoff-len(names))+section0+section1+section2
        require(derived[textoff:textoff+len(code)]==code,"analysis_code_changed")
        require(len(derived)==shoff+3*64,"analysis_elf_size")
        return derived

    LLVM="/data/data/com.termux/files/usr/bin/llvm-objdump"
    DISASM_ARGS=[LLVM,"-d","--no-show-raw-insn","--print-imm-hex","--disassemble-zeroes","-"]
    TEXT_LIMIT=256*1024

    def no_core_dump():
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))

    def bounded_llvm(label,args,payload):
        begin=time.monotonic()
        deadline=min(begin+5.0,overall_deadline)
        require(deadline>begin,"overall_deadline_expired")
        proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              env={"PATH":"/data/data/com.termux/files/usr/bin","LANG":"C","LC_ALL":"C"},
                              close_fds=True,preexec_fn=no_core_dump)
        sel=selectors.DefaultSelector()
        parts={"stdout":[],"stderr":[]}
        total=0;sent=0
        try:
            for stream in (proc.stdin,proc.stdout,proc.stderr):
                os.set_blocking(stream.fileno(),False)
            if payload:
                sel.register(proc.stdin,selectors.EVENT_WRITE,"stdin")
            else:
                proc.stdin.close()
            sel.register(proc.stdout,selectors.EVENT_READ,"stdout")
            sel.register(proc.stderr,selectors.EVENT_READ,"stderr")
            while sel.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    raise TimeoutError("llvm_deadline")
                events=sel.select(min(remaining,0.2))
                for sk,mask in events:
                    if sk.data=="stdin":
                        try:
                            n=os.write(sk.fd,payload[sent:sent+4096])
                        except BlockingIOError:
                            continue
                        sent+=n
                        if sent==len(payload):
                            sel.unregister(sk.fileobj)
                            sk.fileobj.close()
                    else:
                        try:
                            part=os.read(sk.fd,min(4096,TEXT_LIMIT-total+1))
                        except BlockingIOError:
                            continue
                        if not part:
                            sel.unregister(sk.fileobj)
                            sk.fileobj.close()
                        else:
                            total+=len(part)
                            if total>TEXT_LIMIT:
                                raise RuntimeError("llvm_output_limit")
                            parts[sk.data].append(part)
            remaining=deadline-time.monotonic()
            require(remaining>0,"llvm_deadline_after_io")
            rc=proc.wait(timeout=remaining)
            stdout=b"".join(parts["stdout"])
            stderr=b"".join(parts["stderr"])
            receipt={"label":label,"exit_code":rc,"elapsed_seconds":round(time.monotonic()-begin,6),
                     "stdout_bytes":len(stdout),"stderr_bytes":len(stderr),
                     "stdout_sha256":sha(stdout),"stderr_sha256":sha(stderr)}
            subprocess_receipts.append(receipt)
            require(rc==0,"llvm_exit_nonzero")
            return stdout
        finally:
            sel.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=1.0)
            for stream in (proc.stdin,proc.stdout,proc.stderr):
                if not stream.closed:
                    stream.close()

    line_re=re.compile(r"^\s*([0-9a-fA-F]+):\s+(\S+)(?:\s+(.*))?$")
    def parse_lines(raw,va,size):
        text=raw.decode("utf-8","strict")
        rows=[]
        seen=set()
        for line in text.splitlines():
            m=line_re.match(line)
            if m is None:
                continue
            pc=int(m.group(1),16)
            require(va<=pc<va+size and (pc-va)%4==0,"disassembly_pc_outside_declared_range")
            require(pc not in seen,"duplicate_disassembly_pc")
            seen.add(pc)
            rows.append((pc,m.group(2).lower(),(m.group(3) or "").strip()))
        rows.sort()
        return rows

    number_re=re.compile(r"^\s*#?(0x[0-9a-fA-F]+|[0-9]+)(?:\s+<[^>]*>)?\s*$")
    def parse_number(text):
        m=number_re.match(text)
        return int(m.group(1),0) if m else None

    stage="read_llvm_version"
    version_bytes=bounded_llvm("version",[LLVM,"--version"],b"")
    vm=re.search(r"LLVM version\s+([0-9]+(?:\.[0-9]+){1,3})",version_bytes.decode("utf-8","strict"))
    require(vm is not None,"llvm_version_not_recognized")
    version=vm.group(1)

    stage="positive_control_before_private_disassembly"
    control_base=0x12345000
    # Self-authored A64 BL +8, RET, RET. These bytes are not device firmware.
    control_code=struct.pack("<III",0x94000002,0xd65f03c0,0xd65f03c0)
    control_elf=analysis_elf(control_code,control_base)
    control_stdout=bounded_llvm("self_authored_positive_control",DISASM_ARGS,control_elf)
    control_rows=parse_lines(control_stdout,control_base,len(control_code))
    control_pass=(len(control_rows)==3 and
                  [r[0] for r in control_rows]==[control_base,control_base+4,control_base+8] and
                  [r[1] for r in control_rows]==["bl","ret","ret"] and
                  parse_number(control_rows[0][2])==control_base+8)
    positive_control={"passed":control_pass,"self_authored_instruction_count":3,
                      "base":hex(control_base),"expected_direct_call_target":hex(control_base+8),
                      "observed_instruction_count":len(control_rows),
                      "address_and_bl_ret_format_verified":control_pass,
                      "input_code_sha256":sha(control_code),"derived_elf_sha256":sha(control_elf),
                      "covers_conditional_branches_or_adr_forms":False,
                      "firmware_executed":False}
    require(control_pass,"positive_control_failed")

    stage="disassemble_only_original_4k_candidate"
    code=data[source_offset:source_offset+source_size]
    require(len(code)==source_size,"slice_size")
    derived=analysis_elf(code,source_va)
    private_submitted=True
    disassembly=bounded_llvm("private_4k_candidate",DISASM_ARGS,derived)
    rows=parse_lines(disassembly,source_va,source_size)
    row_by_pc={r[0]:r for r in rows}
    load_ranges=[s for s in segments if s["type"]==1 and s["memsz"]>0]
    def target_map(value):
        found=[s for s in load_ranges if int(s["vaddr"],16)<=value<int(s["vaddr"],16)+s["memsz"]]
        if len(found)!=1:
            return None
        s=found[0];delta=value-int(s["vaddr"],16)
        mapped={"target_vaddr":hex(value),"target_segment":s["index"],
                "target_file_backed":delta<s["filesz"],
                "target_executable_flag":bool(s["flags"]&1),"target_in_fv_container":s["index"]==8}
        if delta<s["filesz"]:
            mapped["target_file_offset"]=s["file_offset"]+delta
        return mapped

    stage="filter_numeric_control_flow_and_address_candidates"
    edge_details=[]
    edge_counts={}
    direct_unmapped=0
    branch_parse_failures=0
    unknown_count=0
    indirect_count=0
    range_escape_count=0
    adr_details=[]
    adr_counts={"adr":0,"adjacent_adrp_add":0}
    adr_unmapped=0
    adr_parse_failures=0
    reg_target_re=re.compile(r"^x([0-9]{1,2}),\s*(.*)$")
    add_re=re.compile(r"^x([0-9]{1,2}),\s*x([0-9]{1,2}),\s*#(0x[0-9a-fA-F]+|[0-9]+)(?:,\s*lsl\s*#(0x[0-9a-fA-F]+|[0-9]+))?$")
    for pc,op,operands in rows:
        kind=None;target=None
        if op=="bl":
            kind="direct_call";target=parse_number(operands)
        elif op=="b":
            kind="direct_branch";target=parse_number(operands)
        elif op.startswith("b.") or op in ("cbz","cbnz","tbz","tbnz"):
            kind="conditional_branch";target=parse_number(operands.split(",")[-1].strip())
        elif op in ("ret","retaa","retab"):
            kind="return"
        elif op in ("br","braa","brab","braaz","brabz"):
            kind="indirect_branch";indirect_count+=1
        elif op in ("blr","blraa","blrab","blraaz","blrabz"):
            kind="indirect_call";indirect_count+=1
        elif op in ("eret","eretaa","eretab","drps","svc","hvc","smc","brk","hlt"):
            kind="exception_or_trap"
        if op=="<unknown>" or op.startswith("."):
            unknown_count+=1
        if kind:
            edge_counts[kind]=edge_counts.get(kind,0)+1
            item={"pc":hex(pc),"kind":kind}
            if kind in ("direct_call","direct_branch","conditional_branch"):
                if target is None:
                    branch_parse_failures+=1
                    item["numeric_target_parsed"]=False
                else:
                    item["numeric_target_parsed"]=True
                    item["target_inside_candidate_window"]=source_va<=target<source_va+source_size
                    if not item["target_inside_candidate_window"]:
                        range_escape_count+=1
                    mapped=target_map(target)
                    item["target_maps_known_load"]=mapped is not None
                    if mapped:
                        item.update(mapped)
                    else:
                        direct_unmapped+=1
            if len(edge_details)<64:
                edge_details.append(item)
        if op not in ("adr","adrp"):
            continue
        m=reg_target_re.match(operands)
        if m is None:
            adr_parse_failures+=1
            continue
        target=parse_number(m.group(2))
        if target is None:
            adr_parse_failures+=1
            continue
        record=None
        if op=="adr":
            record={"pc":hex(pc),"kind":"adr"}
        else:
            next_row=row_by_pc.get(pc+4)
            if next_row is None or next_row[1]!="add":
                continue
            am=add_re.match(next_row[2])
            if am is None or int(am.group(2))!=int(m.group(1)):
                continue
            immediate=int(am.group(3),0)
            shift=int(am.group(4),0) if am.group(4) else 0
            if immediate>0xfff or shift not in (0,12) or target%4096:
                adr_parse_failures+=1
                continue
            target+=immediate<<shift
            record={"pc":hex(pc),"add_pc":hex(pc+4),"kind":"adjacent_adrp_add"}
        mapped=target_map(target)
        adr_counts[record["kind"]]+=1
        if mapped is None:
            adr_unmapped+=1
            continue
        record.update(mapped)
        if len(adr_details)<32:
            adr_details.append(record)

    result={
        "schema":"tb371fc-xbl-readonly-control-candidates-v1","started_utc":start,"finished_utc":now(),
        "archive_and_members_verified":True,"archive_size":len(archive),"archive_sha256":sha(archive),
        "capture_json_sha256":sha(members["capture.json"]),
        "input_member":"xbl_a.bin","input_sha256":sha(data),"input_size":len(data),
        "parser_source_sha256":PARSER_SHA,"archive_validator_source_sha256":ARCHIVE_VALIDATOR_SHA,
        "wrapper_source_sha256":WRAPPER_SOURCE_SHA256,
        "new_wrapper_independently_tested_before_execution":False,
        "existing_parser_ct_unit_tests":12,"existing_parser_tests_cover_this_wrapper":False,
        "positive_control":positive_control,"llvm":{"path":LLVM,"version":version,"argv":DISASM_ARGS,
                                                   "max_seconds_per_subprocess":5,
                                                   "combined_text_limit_bytes_per_subprocess":TEXT_LIMIT},
        "subprocess_receipts":subprocess_receipts,
        "source_range":{"file_offset":source_offset,"size":source_size,"vaddr":hex(source_va),
                        "original_segment":2,"original_segment_flags":original["flags"],
                        "original_load_file_offset":original["file_offset"],
                        "original_load_size":original["filesz"],"original_load_vaddr":original["vaddr"],
                        "slice_sha256":sha(code)},
        "analysis_object":{"derived_analysis_elf":True,"original_header":False,
                           "source_code_bytes_unchanged":True,"size":len(derived),"sha256":sha(derived),
                           "synthetic_text_vaddr":hex(source_va),"section_count":3,
                           "program_header_count":0,"input_never_executed":True},
        "disassembly_statistics":{"instruction_rows":len(rows),"expected_4byte_slots":source_size//4,
                                  "all_slots_have_rows":len(rows)==source_size//4,
                                  "unknown_or_data_directive_rows":unknown_count},
        "control_flow_counts":edge_counts,"control_flow_candidates":edge_details,
        "control_flow_detail_cap":64,"direct_targets_outside_known_loads":direct_unmapped,
        "branch_target_parse_failures":branch_parse_failures,"indirect_edges":indirect_count,
        "direct_edges_outside_candidate_window":range_escape_count,
        "address_reference_counts":adr_counts,"address_reference_candidates":adr_details,
        "address_reference_detail_cap":32,"address_targets_outside_known_loads":adr_unmapped,
        "address_reference_parse_failures":adr_parse_failures,
        "entry_reachability_proven":False,"candidate_function_extent_proven":False,
        "whole_window_is_code_proven":False,"active_configuration_proven":False,
        "writes_to_smem_proven":False,"metadata_complete":True,
        "firmware_execution_performed":False,"filesystem_writes_requested":False,
        "new_block_device_reads":False,"new_identity_or_configuration_reads":False,
        "private_instruction_bytes_exported":False,"private_disassembly_exported":False,
        "output_limit_bytes_including_newline":OUTPUT_LIMIT,
        "interpretation":"Linear static decode of a candidate window only. RX ranges can contain literal pools. The positive control checks BL/RET output and addresses, not every parser rule. No path from the real firmware entry, function boundary, selected DAL configuration, or SMEM writes is proven.",
    }
    def update_counts():
        result["control_flow_candidates_reported"]=len(result["control_flow_candidates"])
        result["control_flow_candidates_omitted"]=sum(edge_counts.values())-len(result["control_flow_candidates"])
        result["address_candidates_reported"]=len(result["address_reference_candidates"])
        result["address_candidates_omitted"]=sum(adr_counts.values())-adr_unmapped-len(result["address_reference_candidates"])
        result["details_truncated"]=result["control_flow_candidates_omitted"]>0 or result["address_candidates_omitted"]>0
    update_counts()
    while len(compact(result))+1>OUTPUT_LIMIT and result["address_reference_candidates"]:
        result["address_reference_candidates"].pop()
        update_counts()
    while len(compact(result))+1>OUTPUT_LIMIT and result["control_flow_candidates"]:
        result["control_flow_candidates"].pop()
        update_counts()
    encoded=compact(result)
    require(len(encoded)+1<=OUTPUT_LIMIT,"metadata_size_limit")
except Exception as error:
    result={"schema":"tb371fc-xbl-readonly-control-candidates-v1","started_utc":start,
            "finished_utc":now(),"stage":stage,"error_type":type(error).__name__,
            "archive_and_members_verified":archive_ok,"metadata_complete":False,
            "wrapper_source_sha256":WRAPPER_SOURCE_SHA256,
            "new_wrapper_independently_tested_before_execution":False,
            "positive_control":positive_control,"private_slice_submitted_to_disassembler":private_submitted,
            "subprocess_receipts":subprocess_receipts,
            "firmware_execution_performed":False,"filesystem_writes_requested":False,
            "private_instruction_bytes_exported":False,"private_disassembly_exported":False}
    encoded=compact(result)
sys.stdout.write(encoded.decode("utf-8")+"\n")
sys.exit(0 if result.get("metadata_complete") is True else 1)
