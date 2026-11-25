#!/usr/bin/env python3
"""Synthetic regular-file-only batch tests. Never open a tablet/block source.

Production admission is substituted only in the explicit supervisor fixture:
the test process itself runs under the original outer agent-heavy gate. The
named-FIFO integration fixture additionally accepts a 4KiB regular test plan.
Neither substitution is present in candidate runtime code.
"""
import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import multiprocessing
import os
from pathlib import Path
import signal
import socket
import struct
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent
CANDIDATE = ROOT / "candidate"
EVIDENCE = Path(os.environ["BATCH_TEST_EVIDENCE"])
EVIDENCE.mkdir(mode=0o700, parents=True, exist_ok=False)

def module(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj)
    return obj

cap=module("batch_cap",CANDIDATE/"acquire_block_ranges_batch_v3.py")
sup=module("batch_sup",CANDIDATE/"storage_supervision_batch_v3.py")
sender=module("batch_source_caller",CANDIDATE/"tablet_sender_batch_v3.py")
legacy=module("legacy_wire_profile_fixture",CANDIDATE/"acquire_block_ranges_batch_v3.py")
# Protocol-only legacy profile fixture; no historical collector source is needed.
legacy.PAUSE_PROTOCOL="tb371fc-readonly-range-stream-pause-v1"

def write(path, value):
    raw=value if isinstance(value,bytes) else (json.dumps(value,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"wb") as f:f.write(raw)
    return pin(path)

def pin(path):
    raw=Path(path).read_bytes()
    return {"path":str(path),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}

def supervisor_fixture(request_path,digest,regular=False):
    sup.admission_proof=lambda request:{"test_only":True,"production_admission_asserted":False}
    if regular:
        sup.validate_production_plan=lambda plan,collector:collector.validate_plan(plan)
    with open(Path(request_path).parent/"supervisor-stdout.log","w") as log:
        with contextlib.redirect_stdout(log):
            raise SystemExit(sup.supervise(request_path,digest))

def relay_fixture(request_path,digest,read_fd,write_fd,unused):
    sup.validate_production_plan=lambda plan,collector:collector.validate_plan(plan)
    os.dup2(read_fd,0);os.dup2(write_fd,1)
    for fd in {read_fd,write_fd,*unused}:
        if fd>2:os.close(fd)
    with open(Path(request_path).parent/"relay-stderr.log","w") as log:
        with contextlib.redirect_stderr(log):
            raise SystemExit(sup.relay(request_path,digest))

class BatchTests(unittest.TestCase):
    def setUp(self):
        self.directory=EVIDENCE/self._testMethodName
        self.directory.mkdir(mode=0o700)
        self.source=self.directory/"source.bin"
        self.output=self.directory/"capture"
        self.chunk=4096

    def source_bytes(self,count=6):
        data=b"".join(hashlib.sha256(str(i).encode()).digest()*128 for i in range(count))
        write(self.source,data)
        return data

    def ranges(self):
        return [{"label":"userdata-test","offset":0,"length":self.source.stat().st_size,"classification":"live-mutable"}]

    def exchange(self,send_budget=4,receive_budget=4,resume=False,receiver_module=cap):
        left,right=socket.socketpair()
        left.settimeout(3);right.settimeout(3)
        outcomes={};errors={}
        def run(role,sock,work):
            inp=sock.makefile("rb",buffering=0);out=sock.makefile("wb",buffering=0)
            try:outcomes[role]=work(inp,out)
            except BaseException as exc:errors[role]=exc
            finally:
                with contextlib.suppress(OSError):sock.shutdown(socket.SHUT_RDWR)
                inp.close();out.close();sock.close()
        send_kwargs={} if send_budget is None else {"max_new_chunks":send_budget}
        receive_kwargs={} if receive_budget is None else {"max_new_chunks":receive_budget}
        jobs=[
            threading.Thread(target=run,args=("receive",right,lambda i,o:receiver_module.receive(i,o,self.output,resume=resume,**receive_kwargs))),
            threading.Thread(target=run,args=("send",left,lambda i,o:cap.send_stream(str(self.source),self.source.stat().st_size,self.ranges(),incoming=i,outgoing=o,chunk_bytes=self.chunk,allow_regular=True,**send_kwargs))),
        ]
        for job in jobs:job.start()
        for job in jobs:job.join(4)
        self.assertFalse(any(job.is_alive() for job in jobs),"protocol worker leaked")
        return outcomes,errors

    def committed(self):
        return sorted(self.output.glob("chunk-*/receipt.json")) if self.output.exists() else []

    def strict_success(self,**kwargs):
        outcomes,errors=self.exchange(**kwargs)
        self.assertEqual({}, {k:str(v) for k,v in errors.items()})
        self.assertEqual(outcomes["send"],outcomes["receive"])
        return outcomes["receive"]

    def test_four_chunks_pause_at_exact_budget(self):
        self.source_bytes(6)
        result=self.strict_success()
        self.assertEqual(4,len(self.committed()))
        self.assertEqual(4,result["new_chunks"])
        self.assertFalse(result["complete"])
        self.assertFalse((self.output/"manifest.json").exists())
        pause=json.loads(Path(result["pause_receipt"]).read_text())
        self.assertEqual(cap.PAUSE_SCHEMA,pause["schema"])
        self.assertEqual(4,len(pause["committed_receipts"]))
        self.assertFalse(pause["snapshot_consistent"])

    def test_short_remainder_finishes_and_preserves_v1_manifest(self):
        self.source_bytes(6);self.strict_success()
        before={str(p):(p.stat().st_ino,p.read_bytes()) for p in self.committed()}
        result=self.strict_success(resume=True)
        self.assertTrue(result["complete"]);self.assertEqual(6,result["chunks"])
        manifest=json.loads((self.output/"manifest.json").read_text())
        self.assertEqual("tb371fc-readonly-range-stream-v1",manifest["schema"])
        self.assertEqual("verified-range-copies",manifest["status"])
        self.assertFalse(manifest["snapshot_consistent"])
        for path,expected in before.items():
            p=Path(path);self.assertEqual(expected,(p.stat().st_ino,p.read_bytes()))

    def test_existing_api_default_still_one_chunk(self):
        self.source_bytes(3)
        result=self.strict_success(send_budget=None,receive_budget=None)
        self.assertEqual(1,result["max_new_chunks"]);self.assertEqual(1,len(self.committed()))

    def test_interrupted_third_chunk_retains_two_then_resumes(self):
        self.source_bytes(6)
        original=cap.send_chunk
        def interrupted(fd,row,output):
            if row["index"]==2:
                cap.send_json(output,{"begin":2,"source_before_sha256":cap.hash_range(fd,row["offset"],row["length"])})
                cap.write_all(output,struct.pack(">I",1024)+b"part")
                output.flush()
                raise OSError("synthetic interrupted source stream at third chunk")
            return original(fd,row,output)
        with mock.patch.object(cap,"send_chunk",interrupted):
            _,errors=self.exchange()
        self.assertIn("send",errors);self.assertIn("receive",errors)
        self.assertEqual(2,len(self.committed()))
        first={str(p):(p.stat().st_ino,p.read_bytes()) for p in self.committed()}
        partial=self.output/"chunk-000002/data.gz.partial"
        self.assertTrue(partial.exists())
        old_inode=partial.stat().st_ino
        result=self.strict_success(resume=True)
        self.assertTrue(result["complete"])
        self.assertEqual(6,len(self.committed()))
        retained=list((self.output/"retained-partials").rglob("data.gz.partial"))
        self.assertTrue(any(p.stat().st_ino==old_inode for p in retained))
        for path,expected in first.items():
            p=Path(path);self.assertEqual(expected,(p.stat().st_ino,p.read_bytes()))
        self.assertEqual(0,json.loads(next((self.output/"retained-partials").glob("*.json")).read_text())["deleted_files"])

    def test_source_prefix_mismatch_rejects_new_work(self):
        self.source_bytes(6);self.strict_success()
        with self.source.open("r+b") as f:f.write(b"changed!")
        before=[pin(p) for p in self.committed()]
        _,errors=self.exchange(resume=True)
        self.assertIn("source changed since completed chunk",str(errors["send"]))
        self.assertEqual(before,[pin(p) for p in self.committed()])
        self.assertFalse((self.output/"chunk-000004").exists())
        self.assertFalse((self.output/"manifest.json").exists())

    def test_receiver_corrupt_old_chunk_rejects_resume(self):
        self.source_bytes(6);self.strict_success()
        data=self.output/"chunk-000001/data.gz"
        with data.open("r+b") as f:f.seek(10);f.write(b"X")
        _,errors=self.exchange(resume=True)
        self.assertIn("receive",errors)
        self.assertFalse((self.output/"chunk-000004").exists())

    def test_budget_mismatch_rejects_before_output_creation(self):
        self.source_bytes(6)
        _,errors=self.exchange(send_budget=4,receive_budget=1)
        self.assertIn("receive",errors)
        self.assertFalse(self.output.exists())

    def test_legacy_peer_rejects_new_protocol_before_output(self):
        self.source_bytes(6)
        _,errors=self.exchange(send_budget=1,receive_budget=1,receiver_module=legacy)
        self.assertIn("receive",errors)
        self.assertFalse(self.output.exists())

    def test_invalid_budget_types_and_values_rejected(self):
        for budget in (0,2,3,5,True,False,1.0,"4",None):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):cap.check_budget(budget)
                with self.assertRaises(ValueError):sup.request_budget({"max_new_chunks":budget})

    def test_complete_zero_new_resume_preserves_manifest_identity(self):
        self.source_bytes(3)
        self.strict_success()
        m=self.output/"manifest.json"
        old=(m.stat().st_ino,m.stat().st_mtime_ns,m.read_bytes())
        result=self.strict_success(resume=True)
        self.assertTrue(result["complete"])
        self.assertEqual(old,(m.stat().st_ino,m.stat().st_mtime_ns,m.read_bytes()))

    def test_guard_budget_and_plan_identity_before_capture(self):
        self.source_bytes(6)
        with cap.source_session(str(self.source),self.source.stat().st_size,self.ranges(),chunk_bytes=4096,include_gpt=False,allow_regular=True) as (_,__,plan,___):
            incoming=io.BytesIO(json.dumps(cap.hello_message(4)).encode()+b"\n"+json.dumps(plan).encode()+b"\n")
            attempt=self.directory/"attempt";attempt.mkdir(mode=0o700)
            guard=sup.PlanGuardInput(incoming,plan,cap,attempt,max_new_chunks=4)
            self.assertEqual(cap.hello_message(4),json.loads(guard.readline(cap.JSON_LIMIT)))
            self.assertEqual(plan,json.loads(guard.readline(cap.JSON_LIMIT)))
            self.assertTrue((attempt/"approved-wire-plan.json").exists())
            wrong=io.BytesIO(json.dumps(cap.hello_message(1)).encode()+b"\n")
            with self.assertRaises(ValueError):sup.PlanGuardInput(wrong,plan,cap,attempt,4).readline(cap.JSON_LIMIT)
            mismatch=copy.deepcopy(plan);mismatch["source"]["inode"]+=1
            wrong=io.BytesIO(json.dumps(cap.hello_message(4)).encode()+b"\n"+json.dumps(mismatch).encode()+b"\n")
            guard=sup.PlanGuardInput(wrong,plan,cap,attempt,4);guard.readline(cap.JSON_LIMIT)
            with self.assertRaises(ValueError):guard.readline(cap.JSON_LIMIT)

    def make_request(self,namespace,budget=4,seconds=1,plan=None):
        namespace.mkdir(mode=0o700)
        (namespace/"captures").mkdir(mode=0o700)
        if plan is None:
            plan={"schema":cap.SCHEMA,"source":{"path":"/dev/synthetic-not-opened","kind":"block-device","bytes":8*67108864,"logical_block_size":4096,"stat_device":0,"inode":1,"rdev":0},"ranges":[{"label":"userdata-test","offset":0,"length":8*67108864,"classification":"live-mutable"}],"chunk_bytes":67108864,"gpt":None,"source_open_mode":"O_RDONLY","snapshot_consistent":False}
        plan_pin=write(namespace/"plan.json",plan)
        request={"schema":sup.REQUEST_SCHEMA,"attempt_id":"attempt-000001","namespace":str(namespace),"capture_directory":str(namespace/"captures/fixture"),"resume":False,"expected_plan":plan_pin,"expected_completed_chunks":0,"timeout_seconds":seconds,"max_new_chunks":budget,
         "host":{"boot_id":sup.boot_id(),"uid":os.geteuid(),"agent_heavy":{"path":"/home/ezra/.omp/agent/bin/agent-heavy","bytes":sup.WRAPPER_BYTES,"sha256":sup.WRAPPER_SHA256}},
         "runtime":{name:pin(CANDIDATE/name) for name in sup.RUNTIME_NAMES},
         "authorization":{"job_name":"fixture","jobs_plan_sha256":"1"*64,"source_binding_sha256":"2"*64},"copy_preparation":None,"previous_release":None}
        request["runtime"]["collector"]=pin(CANDIDATE/"acquire_block_ranges_batch_v3.py")
        request["runtime"]["interface"]=pin(CANDIDATE/"interface-contract-batch-v3.json")
        request_pin=write(namespace/"request.json",request)
        return request,request_pin,plan

    def test_request_budget_is_exactly_bound(self):
        namespace=self.directory/"namespace"
        request,binding,_=self.make_request(namespace)
        decoded=sup.request_context(binding["path"],binding["sha256"])[0]
        self.assertEqual(4,decoded["max_new_chunks"])
        for kind,change in (("missing",None),("boolean",True),("notallowed",2),("oldschema","old")):
            new=copy.deepcopy(request)
            if kind=="missing":new.pop("max_new_chunks")
            elif kind=="oldschema":new["schema"]="tb371fc-storage-supervised-attempt-v1"
            else:new["max_new_chunks"]=change
            p=write(namespace/("bad-"+kind+".json"),new)
            with self.assertRaises(ValueError):sup.request_context(p["path"],p["sha256"])

    def test_production_plan_cannot_exceed_256MiB_batch(self):
        namespace=self.directory/"namespace"
        request,binding,plan=self.make_request(namespace)
        cap_loaded=sup.request_context(binding["path"],binding["sha256"])[-1]
        self.assertEqual(268435456,request["max_new_chunks"]*plan["chunk_bytes"])
        plan["chunk_bytes"]=134217728
        with self.assertRaises(ValueError):sup.validate_production_plan(plan,cap_loaded)
        incoming=io.BytesIO(json.dumps(cap.hello_message(4)).encode()+b"\n"+json.dumps(plan).encode()+b"\n")
        with self.assertRaises(ValueError):cap.receive(incoming,io.BytesIO(),self.output,max_new_chunks=4)
        self.assertFalse(self.output.exists())

    def test_capture_lock_conflict_and_release(self):
        self.source_bytes(6);self.strict_success()
        lock=os.open(self.output/"capture.lock",os.O_RDWR)
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            _,errors=self.exchange(resume=True)
            self.assertIsInstance(errors["receive"],BlockingIOError)
            self.assertEqual(4,len(self.committed()))
        finally:os.close(lock)
        self.assertTrue(self.strict_success(resume=True)["complete"])

    def test_supervisor_deadline_releases_lock_and_records_failure(self):
        namespace=self.directory/"namespace"
        request,binding,_=self.make_request(namespace,seconds=1)
        before=signal.getsignal(signal.SIGALRM)
        with mock.patch.object(sup,"admission_proof",return_value={"test_only":True,"production_admission_asserted":False}):
            with contextlib.redirect_stdout(io.StringIO()):
                code=sup.supervise(binding["path"],binding["sha256"])
        self.assertEqual(2,code)
        self.assertEqual(before,signal.getsignal(signal.SIGALRM))
        terminal=json.loads((namespace/"attempts/attempt-000001/receiver-terminal.json").read_text())
        self.assertEqual("failed",terminal["outcome"])
        self.assertTrue(terminal["operator_release_required_before_next_attempt"])
        self.assertFalse(terminal["source_sender_termination_confirmed"])
        self.assertEqual(268435456,terminal["max_new_bytes"])
        lock=sup.held_lock(namespace/"supervisor.lock");os.close(lock)
        self.assertFalse(Path(request["capture_directory"]).exists())

    def release_fixture(self,namespace):
        namespace.mkdir(mode=0o700)
        attempts=namespace/"attempts";attempts.mkdir(mode=0o700)
        old=attempts/"attempt-000001";old.mkdir(mode=0o700)
        identity={"pid":2147483647,"start_ticks":1,"boot_id":sup.boot_id()}
        write(old/"receiver-start.json",{"request_sha256":"a"*64,"process":identity})
        rt=write(old/"receiver-terminal.json",{"request_sha256":"a"*64})
        write(old/"relay-start.json",{"request_sha256":"a"*64,"process":identity})
        relay_terminal=write(old/"relay-terminal.json",{"request_sha256":"a"*64})
        ht=write(namespace/"host-termination.json",{"synthetic":True})
        st=write(namespace/"source-termination.json",{"synthetic":True})
        release={"schema":sup.RELEASE_SCHEMA,"previous_attempt_id":"attempt-000001","previous_request_sha256":"a"*64,"next_attempt_id":"attempt-000002","receiver_terminal":rt,"relay_terminal":relay_terminal,"source_termination_confirmed":True,"host_termination_confirmed":True,"retained_files_reviewed":True,"abnormal_termination_reviewed":False,"source_termination_evidence":st,"host_termination_evidence":ht}
        return release,{"attempt_id":"attempt-000002","previous_release":None},old

    def test_next_attempt_cannot_bypass_operator_release(self):
        namespace=self.directory/"namespace"
        release,request,old=self.release_fixture(namespace)
        with self.assertRaises(ValueError):sup.previous_attempts(namespace,request,"b"*64)
        release["source_termination_confirmed"]=False
        request["previous_release"]=write(namespace/"false-release.json",release)
        with self.assertRaises(ValueError):sup.previous_attempts(namespace,request,"b"*64)
        release["source_termination_confirmed"]=True
        request["previous_release"]=write(namespace/"good-release.json",release)
        self.assertEqual(1,len(sup.previous_attempts(namespace,request,"b"*64)[1]))
        old_start=old/"receiver-start.json"
        value={"request_sha256":"a"*64,"process":sup.process_identity(os.getpid())}
        old_start.write_text(json.dumps(value))
        with self.assertRaises(ValueError):sup.previous_attempts(namespace,request,"b"*64)

    def test_new_source_caller_requires_budget_and_new_namespace(self):
        namespace=sender.HOST_ROOT
        payload={"schema":"tb371fc-storage-memory-sender-call-v2","attempt_id":"attempt-000001","host_request":{"path":namespace+"/bindings/attempt-000001-request.json","bytes":20,"sha256":"a"*64},"relay":{"path":namespace+"/code/ssh_stdio_relay_batch_v3.py","bytes":20,"sha256":"b"*64},"job_name":"lun0-tail-082","expected_completed_chunks":0,"source_deadline_seconds":240,"namespace":namespace,"max_new_chunks":4}
        sender.validate_payload(payload)
        for budget in (True,0,2,5):
            value=copy.deepcopy(payload);value["max_new_chunks"]=budget
            with self.assertRaises(ValueError):sender.validate_payload(value)
        value=copy.deepcopy(payload);value["source_deadline_seconds"]=241
        with self.assertRaises(ValueError):sender.validate_payload(value)

    def test_named_fifo_batch_waits_for_real_eof_and_drains(self):
        self.source_bytes(6)
        with cap.source_session(str(self.source),self.source.stat().st_size,self.ranges(),chunk_bytes=4096,include_gpt=False,allow_regular=True) as (_,__,plan,___):pass
        namespace=self.directory/"namespace"
        request,binding,_=self.make_request(namespace,budget=4,seconds=8,plan=plan)
        ctx=multiprocessing.get_context("fork")
        supervisor=ctx.Process(target=supervisor_fixture,args=(binding["path"],binding["sha256"],True))
        supervisor.start()
        attempt=namespace/"attempts/attempt-000001"
        until=time.monotonic()+3
        while not (attempt/"receiver-ready.json").exists() and time.monotonic()<until:time.sleep(.01)
        self.assertTrue((attempt/"receiver-ready.json").exists())
        read_to_relay,write_to_relay=os.pipe()
        read_from_relay,write_from_relay=os.pipe()
        relay=ctx.Process(target=relay_fixture,args=(binding["path"],binding["sha256"],read_to_relay,write_from_relay,(write_to_relay,read_from_relay)))
        relay.start();os.close(read_to_relay);os.close(write_from_relay)
        incoming=os.fdopen(read_from_relay,"rb",buffering=0);outgoing=os.fdopen(write_to_relay,"wb",buffering=0)
        try:
            result=cap.send_stream(str(self.source),self.source.stat().st_size,self.ranges(),incoming=incoming,outgoing=outgoing,chunk_bytes=4096,allow_regular=True,max_new_chunks=4)
            self.assertEqual(4,result["new_chunks"])
            time.sleep(.1)
            self.assertFalse((attempt/"relay-drained.json").exists(),"relay accepted absent source EOF")
            self.assertTrue(relay.is_alive())
            outgoing.close()
            supervisor.join(3);relay.join(3)
            self.assertEqual(0,supervisor.exitcode);self.assertEqual(0,relay.exitcode)
            drained=json.loads((attempt/"relay-drained.json").read_text())
            self.assertTrue(drained["stdin_eof"]);self.assertTrue(drained["response_eof"])
            self.assertEqual(drained["counts"]["source_read"],drained["counts"]["receiver_written"])
            self.assertEqual(drained["sha256"]["source_read"],drained["sha256"]["receiver_written"])
            self.assertEqual(drained["sha256"]["receiver_read"],drained["sha256"]["source_written"])
            terminal=json.loads((attempt/"receiver-terminal.json").read_text())
            self.assertEqual(4,terminal["actual_new_chunks"])
            self.assertFalse(terminal["source_sender_termination_confirmed"])
            self.assertTrue(terminal["operator_release_required_before_next_attempt"])
            relay_terminal=json.loads((attempt/"relay-terminal.json").read_text())
            self.assertTrue(relay_terminal["supervisor_termination_confirmed_before_exit0"])
            self.assertEqual(0,relay_terminal["undelivered_to_source_bytes"])
            self.assertEqual(0,relay_terminal["undelivered_to_receiver_bytes"])
        finally:
            if not outgoing.closed:outgoing.close()
            incoming.close()
            for proc in (supervisor,relay):
                if proc.is_alive():proc.terminate();proc.join(2)
                if proc.is_alive():proc.kill();proc.join(2)
                self.assertFalse(proc.is_alive())

    def test_unchanged_integrity_and_release_functions(self):
        import ast
        proof=json.loads((ROOT/"proof/baseline-invariants.json").read_bytes())
        for filename in ("acquire_block_ranges_batch_v3.py","storage_supervision_batch_v3.py"):
            funcs={n.name:n for n in ast.parse((CANDIDATE/filename).read_bytes()).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
            for name,digest in proof["function_ast_sha256"][filename].items():
                self.assertEqual(digest,hashlib.sha256(ast.dump(funcs[name],include_attributes=False).encode()).hexdigest(),name)

if __name__=="__main__":
    preparation_tests=module("batch_preparation_tests",ROOT/"test_batch_preparation_v3.py")
    suites=[unittest.defaultTestLoader.loadTestsFromModule(preparation_tests)]
    if os.environ.get("BATCH_PREPARATION_ONLY")!="1":suites.insert(0,unittest.defaultTestLoader.loadTestsFromTestCase(BatchTests))
    suite=unittest.TestSuite(suites)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    summary={"schema":"tb371fc-storage-batch-synthetic-tests-v1","tests_run":result.testsRun,"passed":result.wasSuccessful(),"failures":[(str(t),msg) for t,msg in result.failures],"errors":[(str(t),msg) for t,msg in result.errors],"scope":"Synthetic regular files, socketpair, named FIFOs, subprocess/EOF and lock/deadline tests only; supervisor admission substituted explicitly in fixture; no physical/tablet/block reads and no production deployment.","production_admission_tested":False,"live_source_read":False,"preparation_fixture_kind":preparation_tests.FIXTURE_KIND,"test_filesystem_path":str(EVIDENCE),"production_256MiB_deadline_performance_tested":False}
    write(EVIDENCE/"result.json",summary)
    raise SystemExit(0 if result.wasSuccessful() else 1)
