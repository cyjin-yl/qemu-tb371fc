"""Public-safe synthetic prior observations; no keys, source bytes or live receipts.

These values exercise evidence validation only. All process identities are invented,
all generated commands remain unexecuted, and the job/source digest substitutions
are applied solely to the imported preparer inside the test process.
"""
import base64
import hashlib
import json
import os
from pathlib import Path

def make_fixture(directory,prep):
    directory=Path(directory);directory.mkdir(mode=0o700,exist_ok=False)
    encoded=prep.encoded
    sha=prep.sha
    root=prep.ROOT
    source={"path":"/dev/block/sda","bytes":125720068096,"logical_block_size":4096,
            "kind":"block-device","stat_device":17,"inode":2101,"rdev":2048}
    ranges=[{"label":"userdata-synthetic","offset":0,"length":16*prep.CHUNK,"classification":"live-mutable"}]
    jobs={"schema":"synthetic-test-jobs-only","jobs":[{"name":"lun0-tail-082","device":source["path"],
          "source_bytes":source["bytes"],"expected_chunks":16,"ranges":ranges}]}
    jobs_raw=encoded(jobs);binding={"source":source,"jobs_plan_sha256":sha(jobs_raw)}
    binding_raw=encoded(binding)
    plan={"schema":"tb371fc-readonly-range-stream-v1","source":source,"ranges":ranges,
          "chunk_bytes":prep.CHUNK,"gpt":None,"source_open_mode":"O_RDONLY","snapshot_consistent":False}
    def remote_pin(path,raw):return {"path":path,"bytes":len(raw),"sha256":sha(raw)}
    runtime={name:{"path":prep.runtime_path(name,legacy=True),"bytes":length,"sha256":digest}
             for name,(length,digest) in prep.LEGACY_RUNTIME.items()}
    request={"schema":"tb371fc-storage-supervised-attempt-v1","attempt_id":"attempt-000011","namespace":root,
             "capture_directory":root+"/captures/lun0-tail-081-synthetic","copy_preparation":None,
             "resume":True,"expected_completed_chunks":7,"timeout_seconds":260,"runtime":runtime,
             "host":{"boot_id":prep.HOST_BOOT,"uid":1000,"agent_heavy":{"path":"/home/ezra/.omp/agent/bin/agent-heavy",
                     "bytes":6681,"sha256":"dc60ed4fdd2136273ef0fdd20ab3b5230140b99b5092e81c15d89dcfde057cc4"}},
             "authorization":{"job_name":"lun0-tail-081","jobs_plan_sha256":sha(jobs_raw),"source_binding_sha256":sha(binding_raw)},
             "expected_plan":remote_pin(root+"/bindings/attempt-000011-plan-lun0-tail-081.json",encoded(plan)),
             "previous_release":remote_pin(root+"/bindings/attempt-000010-release-to-000011.json",encoded({"synthetic":True}))}
    request_raw=encoded(request);digest=sha(request_raw)
    ack={"chunks":8,"complete":False,"max_new_chunks":1,"new_chunks":1,"next_index":8,"paused":True,
         "pause_receipt":request["capture_directory"]+"/pause-receipts/pause-000008.json",
         "snapshot_consistent":False,"total_chunks":16}
    src={"schema":"tb371fc-storage-memory-sender-result-v2","attempt_id":request["attempt_id"],
         "host_request_sha256":digest,"job_name":"lun0-tail-081","source_boot_id":prep.SOURCE_BOOT,
         "collector_sha256":prep.LEGACY_RUNTIME["collector"][1],"source_open_mode":"O_RDONLY",
         "automatic_retry_permitted":False,"tablet_persistent_files_created_or_written":False,
         "tablet_block_source_written":False,"control_output_filesystem":"tmpfs","source_call_passed":True,
         "ssh_child_reaped":True,"ssh_exit_code":0,"source_pid":2147000101,"source_start_ticks":101,
         "ssh_pid":2147000102,"ssh_start_ticks":102,"ssh_terminate_sent":False,"ssh_kill_sent":False,
         "alarm_fired":False,"acknowledgement":ack}
    src_raw=encoded(src)
    worker={"boot_id":prep.SOURCE_BOOT,"pid":src["source_pid"],"start_ticks":src["source_start_ticks"]}
    controller={"boot_id":prep.SOURCE_BOOT,"pid":2147000103,"start_ticks":103}
    outer={"schema":"tb371fc-storage-memory-call-outer-v1","attempt_id":request["attempt_id"],
           "source_result":src,"stdout_base64":base64.b64encode(src_raw).decode(),"stdout_bytes":len(src_raw),
           "stdout_sha256":sha(src_raw),"stderr_base64":"","stderr_bytes":0,"stderr_sha256":sha(b""),
           "worker_reaped":True,"output_limit_reached":False,"persistent_output_files_written":False,
           "worker_process":worker,"controller_process":controller,"root_process_wait_returncode":0}
    source_end={"schema":"tb371fc-storage-source-termination-and-environment-v1","attempt_id":request["attempt_id"],
                "boot_id":prep.SOURCE_BOOT,"uid":0,"source_lock_available":True,
                "processes":[{"pid":p,"expected_start_ticks":s,"present":False,"same_identity_running":False}
                             for p,s in [(src["source_pid"],src["source_start_ticks"]),(src["ssh_pid"],src["ssh_start_ticks"]),
                                         (controller["pid"],controller["start_ticks"])]]}
    receiver={"boot_id":prep.HOST_BOOT,"pid":2147000201,"start_ticks":201}
    relay={"boot_id":prep.HOST_BOOT,"pid":2147000202,"start_ticks":202}
    admission={"verified":True,"background_daemon":False,"tmux_pane":"%9","synthetic_test_only":True}
    regular={}
    regular["approved-wire-plan.json"]={"matched":True}
    regular["receiver-ack.json"]={"request_sha256":digest,"acknowledgement":ack}
    for role,identity in (("receiver",receiver),("relay",relay)):
        regular[role+"-start.json"]={"request_sha256":digest,"process":identity}
        regular[role+"-connected.json"]={"request_sha256":digest,"process":identity}
    regular["receiver-start.json"]["admission"]=admission
    regular["receiver-ready.json"]={**regular["receiver-start.json"]}
    regular["receiver-terminal.json"]={"schema":"tb371fc-storage-receiver-terminal-v1","request_sha256":digest,
        "process":receiver,"host_boot_id":prep.HOST_BOOT,"exit_code":0,"clean_host_protocol":True,
        "outcome":"host-clean-pause","acknowledgement":ack,"actual_new_chunks":1,"max_new_chunks":1,"max_new_bytes":prep.CHUNK}
    regular["relay-terminal.json"]={"schema":"tb371fc-storage-relay-terminal-v1","request_sha256":digest,
        "process":relay,"host_boot_id":prep.HOST_BOOT,"exit_code":0,"outcome":"drained",
        "supervisor_termination_confirmed_before_exit0":True}
    regular["relay-drained.json"]={"request_sha256":digest,"stdin_eof":True,"response_eof":True}
    files=[]
    for i,(name,value) in enumerate(sorted(regular.items()),1):
        raw=encoded(value);files.append({"name":name,"mode":"0o600","inode":100+i,"kind":"regular",
                                       "bytes":len(raw),"sha256":sha(raw),"json":value})
    for i,name in enumerate(("receiver-to-source.fifo","source-to-receiver.fifo")):
        files.append({"name":name,"mode":"0o600","inode":200+i,"kind":"fifo","bytes":0})
    host={"schema":"tb371fc-storage-host-attempt-stop-review-v1","expected_identity":receiver,
          "receiver_identity_running":False,"relay_identity":relay,"relay_identity_running":False,
          "source_result_passed":True,"files":files,"panes":"@1|%9|1|0|2147000201\n","signal_sent":False}
    review={"outcome":"clean","reason":"Synthetic endpoint observations only; no real attempt release or execution.",
            "retained_files_reviewed":True}
    rows={"jobs.json":jobs_raw,"source-binding.json":binding_raw,"request.json":request_raw,
          "outer.json":encoded(outer),"source-end.json":encoded(source_end),"host-evidence.json":encoded(host),"review.json":encoded(review)}
    pins=[]
    for name,raw in rows.items():
        fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"wb") as f:f.write(raw)
        remote=root+("/preparations/attempt-000011-host-termination-evidence.json" if name=="host-evidence.json" else "/bindings/synthetic-"+name)
        pins.append({"name":name,"source":remote,"bytes":len(raw),"sha256":sha(raw)})
    (directory/"pins.json").write_bytes(encoded(pins))
    return {"jobs_plan_sha256":sha(jobs_raw),"source_binding_sha256":sha(binding_raw)}
