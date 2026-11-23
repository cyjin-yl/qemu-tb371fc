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
    stage="parse_allowlisted_metadata"
    inputs=[]
    for member,name in (("xbl_a.bin","xbl_a"),("xbl_config_a.bin","xbl_config_a")):
        data=members[member]
        expected_size,expected_sha=p["EXPECTED"][name]
        require(len(data)==expected_size and sha(data)==expected_sha,"member_not_matching_frozen_parser")
        facts={
            "elf":p["elf_metadata"](data),
            "firmware_volumes":p["fv_metadata"](data),
            "allowlisted_markers":{m.decode("ascii"):p["offsets"](data,m) for m in p["MARKERS"]},
            "memory_map_literal_candidates":p["memory_map_literals"](data),
        }
        e=facts["elf"];fv=facts["firmware_volumes"];segments=e.get("segments",[])
        elf_fields=("magic_at_zero","elf_class","byte_order","ident_version","header_decoded",
                    "type","machine","version","entry","flags","header_size",
                    "program_header_offset","program_header_size","program_header_count",
                    "section_header_offset","section_header_size","section_header_count",
                    "program_table_valid","errors","entry_low_bit_interpreted_as_thumb",
                    "entry_executable_loads_by_vaddr","entry_executable_loads_by_paddr")
        selected_elf={k:e[k] for k in elf_fields if k in e}
        selected_elf.update(segments=segments[:16],segments_total=len(segments),
                            segments_omitted=max(0,len(segments)-16))
        fv_fields=("signature_offset","candidate_header_offset","length","attributes",
                   "header_length","header_checksum_valid","block_map_terminated",
                   "block_map_total_size","revision","structurally_valid_candidate","errors")
        candidates=fv["candidates"]
        selected_fv={
            "signature_count":fv["signature_count"],"valid_candidate_count":fv["valid_candidate_count"],
            "scan_candidates_truncated":fv["candidates_truncated"],
            "candidate_details":[{k:c[k] for k in fv_fields if k in c} for c in candidates[:8]],
            "candidate_details_omitted":max(0,len(candidates)-8),
        }
        marker_details={
            k:{"count":v["count"],"offsets":v["offsets"][:8],
               "offsets_omitted":max(0,v["count"]-len(v["offsets"][:8]))}
            for k,v in facts["allowlisted_markers"].items()
        }
        inputs.append({
            "name":name,"member":member,"size":len(data),"sha256":sha(data),
            "canonical_pure_metadata_sha256":sha(compact(facts)),
            "elf":selected_elf,"firmware_volumes":selected_fv,
            "allowlisted_markers":marker_details,
            "memory_map_literal_candidates":facts["memory_map_literal_candidates"],
        })
    result={
        "schema":"tb371fc-xbl-readonly-tablet-metadata-v1","started_utc":start,
        "finished_utc":now(),"archive_size":len(archive),"archive_sha256":sha(archive),
        "tar_size":tar_size,"capture_json_sha256":sha(members["capture.json"]),
        "parser_source_sha256":PARSER_SHA,"archive_validator_source_sha256":ARCHIVE_VALIDATOR_SHA,
        "wrapper_source_sha256":WRAPPER_SOURCE_SHA256,
        "archive_and_members_verified":True,"metadata_complete":True,
        "inputs":inputs,"output_mode":"bounded_details",
        "output_limit_bytes_including_newline":OUTPUT_LIMIT,"firmware_execution_performed":False,
        "filesystem_writes_requested":False,"new_block_device_reads":False,
        "new_identity_or_configuration_reads":False,"metadata_cli_main_called":False,
        "runtime_or_smem_profile_validation":False,
        "interpretation":"Static metadata of the already-captured files only; marker or literal presence does not prove an active table, an initializer call, or runtime state.",
    }
    encoded=compact(result)
    if len(encoded)+1>OUTPUT_LIMIT:
        result["output_mode"]="counts_only_due_size_cap"
        for item in result["inputs"]:
            item["elf"]={k:item["elf"][k] for k in ("elf_class","byte_order","machine","entry","program_header_count","errors") if k in item["elf"]}
            f=item["firmware_volumes"]
            item["firmware_volumes"]={k:f[k] for k in ("signature_count","valid_candidate_count","scan_candidates_truncated")}
            item["allowlisted_markers"]={k:{"count":v["count"]} for k,v in item["allowlisted_markers"].items()}
            item.pop("memory_map_literal_candidates",None)
        result["details_omitted_for_output_cap"]=["segments","marker_offsets","FV_candidate_details","memory_map_literals"]
        encoded=compact(result)
    require(len(encoded)+1<=OUTPUT_LIMIT,"minimal_metadata_exceeds_output_limit")
except Exception as error:
    result={"schema":"tb371fc-xbl-readonly-tablet-metadata-v1","started_utc":start,
            "finished_utc":now(),"stage":stage,"error_type":type(error).__name__,
            "archive_and_members_verified":archive_ok,"metadata_complete":False,
            "firmware_execution_performed":False,"filesystem_writes_requested":False,
            "payload_printed":False}
    encoded=compact(result)
sys.stdout.write(encoded.decode("utf-8")+"\n")
sys.exit(0 if result.get("metadata_complete") is True else 1)
