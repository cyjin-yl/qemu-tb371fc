import datetime
import hashlib
import json
import os
import stat
import sys

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
    stage="parse_elf_geometry"
    data=members["xbl_a.bin"]
    expected_size,expected_sha=p["EXPECTED"]["xbl_a"]
    require(len(data)==expected_size and sha(data)==expected_sha,"xbl_member_hash_mismatch")
    elf=p["elf_metadata"](data)
    require(elf.get("header_decoded") and elf.get("program_table_valid") and not elf.get("errors"),"invalid_elf")
    require(elf.get("elf_class")==2 and elf.get("byte_order")==1 and elf.get("machine")==183,"unexpected_elf_architecture")
    segs={s["index"]:s for s in elf["segments"]}
    expected_scopes={
        4:(400784,42210,0x14874000,4),
        5:(443008,88744,0x14882000,6),
        11:(3462464,29896,0x807a0000,6),
        14:(3500752,11096,0x146b0800,6),
    }
    scopes=[]
    for index,(offset,size,va,flags) in expected_scopes.items():
        s=segs[index]
        require(s["type"]==1 and s["file_offset"]==offset and s["filesz"]==size and
                int(s["vaddr"],16)==va and s["flags"]==flags and not(s["flags"]&1),
                "scope_geometry_mismatch")
        require(0<=offset<=len(data) and offset+size<=len(data),"scope_outside_file")
        scopes.append({"segment":index,"file_offset":offset,"size":size,
                       "vaddr":hex(va),"flags":flags,"sha256":sha(data[offset:offset+size])})
    known=[
        ("boot_smem_init",402690),("smem_vers",404454),
        ("/core/mproc/smem",404665),("smem_xbl_loader",404720),
        ("smem_vers",404738),("smem_vers",404759),
        ("smem_toc_vers",404790),("smem_toc_vers",404815),
        ("smem_partitions",404850),("smem_partitions",404877),
        ("smem_max_items",404914),("smem_max_items",404940),
        ("smem_vers",423870),("smem_toc_vers",423880),
        ("smem_partitions",423894),("smem_max_items",423910),
    ]
    stage="check_known_string_boundaries"
    targets=[]
    by_va={}
    for n,(label,offset) in enumerate(known):
        raw=label.encode("ascii")
        require(data[offset:offset+len(raw)]==raw,"known_marker_mismatch")
        require(400784<=offset and offset+len(raw)<400784+42210,"marker_outside_load4")
        va=0x14874000+offset-400784
        require(va not in by_va,"duplicate_target_address")
        t={"id":n,"label":label,"file_offset":offset,"vaddr":hex(va),"segment":4,
           "left_boundary_nul":data[offset-1]==0,
           "right_boundary_nul":data[offset+len(raw)]==0,
           "total_unique_slots":0,"width_match_counts":{"32":0,"64":0},"hits":[]}
        targets.append(t);by_va[va]=t
    stage="scan_absolute_pointer_candidates"
    for sc in scopes:
        lo=sc["file_offset"];hi=lo+sc["size"];base=int(sc["vaddr"],16)
        first=lo+(-base)%4
        for off in range(first,hi-3,4):
            source_va=base+off-lo
            matches={}
            val32=int.from_bytes(data[off:off+4],"little")
            if val32 in by_va:
                matches[val32]=[32]
            if source_va%8==0 and off+8<=hi:
                val64=int.from_bytes(data[off:off+8],"little")
                if val64 in by_va:
                    matches.setdefault(val64,[]).append(64)
            for value,widths in matches.items():
                t=by_va[value]
                t["total_unique_slots"]+=1
                for width in widths:
                    t["width_match_counts"][str(width)]+=1
                if len(t["hits"])<64:
                    t["hits"].append({"file_offset":off,"source_segment":sc["segment"],
                                      "source_vaddr":hex(source_va),"widths":widths})
    all_retained=[(t,h) for t in targets for h in t["hits"]]
    # Bound retained detail globally while keeping each target's exact scan counts.
    selected=[]
    for rank in range(64):
        for t in targets:
            if rank<len(t["hits"]) and len(selected)<64:
                selected.append((t["id"],t["hits"][rank]))
    selected_keys={(tid,h["source_segment"],h["file_offset"]) for tid,h in selected}
    for t in targets:
        t["hits_retained_by_per_target_cap"]=len(t["hits"])
        t["hits"]=[h for h in t["hits"] if
                   (t["id"],h["source_segment"],h["file_offset"]) in selected_keys]
    stage="check_bounded_adjacent_pointer_candidates"
    load_ranges=[s for s in segs.values() if s["type"]==1 and s["memsz"]>0]
    def map_pointer(value):
        found=[s for s in load_ranges if int(s["vaddr"],16)<=value<
               int(s["vaddr"],16)+s["memsz"]]
        return found[0] if len(found)==1 else None
    neighbours=[]
    neighbour_seen=set()
    neighbour_total=0
    for tid,hit in selected:
        sc=next(x for x in scopes if x["segment"]==hit["source_segment"])
        lo=sc["file_offset"];hi=lo+sc["size"];base=int(sc["vaddr"],16)
        anchor=hit["file_offset"]
        for off in range(max(lo,anchor-32),min(hi-3,anchor+36),4):
            if off==anchor:
                continue
            source_va=base+off-lo
            if source_va%4:
                continue
            candidates={}
            val32=int.from_bytes(data[off:off+4],"little")
            mapped=map_pointer(val32)
            if mapped is not None:
                candidates[val32]=([32],mapped)
            if source_va%8==0 and off+8<=hi:
                val64=int.from_bytes(data[off:off+8],"little")
                mapped=map_pointer(val64)
                if mapped is not None:
                    if val64 in candidates:
                        candidates[val64][0].append(64)
                    else:
                        candidates[val64]=([64],mapped)
            for value,(widths,mapped) in candidates.items():
                nk=(sc["segment"],off,value)
                if nk in neighbour_seen:
                    continue
                neighbour_seen.add(nk);neighbour_total+=1
                if len(neighbours)>=32:
                    continue
                relative=value-int(mapped["vaddr"],16)
                file_backed=relative<mapped["filesz"]
                item={"source_file_offset":off,"source_segment":sc["segment"],
                      "source_vaddr":hex(source_va),"widths":widths,
                      "target_vaddr":hex(value),"target_segment":mapped["index"],
                      "target_file_backed":file_backed,"target_executable_flag":bool(mapped["flags"]&1),
                      "target_in_fv_container":mapped["index"]==8,
                      "anchor_hit_file_offset":anchor,"anchor_target_id":tid}
                if file_backed:
                    item["target_file_offset"]=mapped["file_offset"]+relative
                neighbours.append(item)
    result={
        "schema":"tb371fc-xbl-readonly-pointer-candidates-v1",
        "started_utc":start,"finished_utc":now(),
        "archive_size":len(archive),"archive_sha256":sha(archive),"tar_size":tar_size,
        "archive_and_members_verified":True,"capture_json_sha256":sha(members["capture.json"]),
        "input_member":"xbl_a.bin","input_size":len(data),"input_sha256":sha(data),
        "parser_source_sha256":PARSER_SHA,"archive_validator_source_sha256":ARCHIVE_VALIDATOR_SHA,
        "wrapper_source_sha256":WRAPPER_SOURCE_SHA256,
        "new_wrapper_independently_tested_before_execution":False,
        "existing_parser_ct_unit_tests":12,
        "existing_parser_tests_cover_this_wrapper":False,
        "scan_ranges":scopes,"scan_total_bytes":sum(s["size"] for s in scopes),
        "targets":targets,"per_target_hit_detail_cap":64,"global_hit_detail_cap":64,
        "neighbour_window_bytes_each_side":32,"neighbour_windows_examined":len(selected),
        "neighbour_unique_candidates_within_examined_windows":neighbour_total,
        "neighbour_detail_cap":32,"neighbours":neighbours,
        "string_pointer_scan_complete_for_declared_scope":True,
        "neighbour_scan_covers_all_pointer_hits":sum(t["total_unique_slots"] for t in targets)==len(selected),
        "function_selection_proven":False,"configuration_table_layout_proven":False,
        "active_configuration_proven":False,"writes_to_smem_proven":False,
        "firmware_execution_performed":False,"disassembler_executed":False,
        "filesystem_writes_requested":False,"new_block_device_reads":False,
        "new_identity_or_configuration_reads":False,"metadata_cli_main_called":False,
        "output_limit_bytes_including_newline":OUTPUT_LIMIT,
        "metadata_complete":True,
        "interpretation":"Numeric matches only. A candidate may be diagnostic data or coincidence; no function, DAL layout, configuration selection, or SMEM write is established. Neighbour values are not dereferenced.",
    }
    def update_counts():
        for t in targets:
            t["hits_reported"]=len(t["hits"])
            t["hits_omitted"]=t["total_unique_slots"]-len(t["hits"])
        result["neighbours_reported"]=len(result["neighbours"])
        result["neighbours_omitted"]=neighbour_total-len(result["neighbours"])
        result["details_truncated"]=any(t["hits_omitted"] for t in targets) or result["neighbours_omitted"]>0
    update_counts()
    while len(compact(result))+1>OUTPUT_LIMIT and result["neighbours"]:
        result["neighbours"].pop()
        update_counts()
    while len(compact(result))+1>OUTPUT_LIMIT and any(t["hits"] for t in targets):
        largest=max(targets,key=lambda t:len(t["hits"]))
        largest["hits"].pop()
        update_counts()
    encoded=compact(result)
    require(len(encoded)+1<=OUTPUT_LIMIT,"counts_metadata_exceeds_output_limit")
except Exception as error:
    result={"schema":"tb371fc-xbl-readonly-pointer-candidates-v1","started_utc":start,
            "finished_utc":now(),"stage":stage,"error_type":type(error).__name__,
            "archive_and_members_verified":archive_ok,"metadata_complete":False,
            "wrapper_source_sha256":WRAPPER_SOURCE_SHA256,
            "new_wrapper_independently_tested_before_execution":False,
            "firmware_execution_performed":False,"filesystem_writes_requested":False,
            "payload_printed":False}
    encoded=compact(result)
sys.stdout.write(encoded.decode("utf-8")+"\n")
sys.exit(0 if result.get("metadata_complete") is True else 1)
