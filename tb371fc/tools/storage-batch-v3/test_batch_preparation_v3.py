#!/usr/bin/env python3
"""Offline preparation tests: retained metadata and synthetic receipts only.

The default fixture is entirely synthetic. An optional explicit environment path
permits a private preflight against retained historical metadata. Any v2 chain
here is explicitly synthetic. Generated commands are compiled and inspected,
never launched; no production namespace, source, device or SSH is accessed.
"""
import ast
import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parent
CANDIDATE=ROOT/"candidate"
FIXTURE=None
EVIDENCE=Path(os.environ["BATCH_TEST_EVIDENCE"])

def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj)
    return obj

prep=load_module("batch_preparer",CANDIDATE/"prepare_batch_attempt_v3.py")
ram=load_module("batch_ram_launcher",CANDIDATE/"prepare_ram_launch_batch_v3.py")
controller=load_module("batch_ram_controller",CANDIDATE/"ram_source_controller_batch_v3.py")
sup=load_module("batch_preparation_supervision",CANDIDATE/"storage_supervision_batch_v3.py")
PRODUCTION_DIGESTS={"jobs_plan_sha256":prep.JOBS_SHA,"source_binding_sha256":prep.BINDING_SHA}
if "BATCH_PREPARATION_FIXTURES" in os.environ:
    FIXTURE=Path(os.environ["BATCH_PREPARATION_FIXTURES"])
    FIXTURE_KIND="private-retained-attempt11-metadata"
    SYNTHETIC_DIGESTS=None
else:
    FIXTURE=EVIDENCE/"synthetic-preparation-fixtures"
    SYNTHETIC_DIGESTS=load_module("synthetic_preparation_fixture",ROOT/"synthetic_preparation_fixture.py").make_fixture(FIXTURE,prep)
    FIXTURE_KIND="self-contained-synthetic-metadata"
PROOF=json.loads((ROOT/"proof/baseline-invariants.json").read_bytes())

def save(path,value):
    raw=value if isinstance(value,bytes) else prep.encoded(value)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"wb") as f:f.write(raw)
    return prep.file_pin(path,raw)

def replace_value(value,old,new):
    if isinstance(value,dict):return {k:replace_value(v,old,new) for k,v in value.items()}
    if isinstance(value,list):return [replace_value(v,old,new) for v in value]
    return new if value==old else value

class PreparationTests(unittest.TestCase):
    def setUp(self):
        if SYNTHETIC_DIGESTS is not None:
            from unittest import mock
            for name,key in (("JOBS_SHA","jobs_plan_sha256"),("BINDING_SHA","source_binding_sha256")):
                patcher=mock.patch.object(prep,name,SYNTHETIC_DIGESTS[key]);patcher.start();self.addCleanup(patcher.stop)
        self.directory=EVIDENCE/self._testMethodName
        self.directory.mkdir(mode=0o700)
        self.caller=(CANDIDATE/"tablet_sender_batch_v3.py").read_bytes()
        self.collector=(CANDIDATE/"acquire_block_ranges_batch_v3.py").read_bytes()
        self.controller=(CANDIDATE/"ram_source_controller_batch_v3.py").read_bytes()

    def fixture(self,name):
        rows=json.loads((FIXTURE/"pins.json").read_bytes())
        item=next(x for x in rows if x["name"]==name)
        raw=(FIXTURE/name).read_bytes()
        self.assertEqual((item["bytes"],item["sha256"]),(len(raw),prep.sha(raw)))
        return raw,item

    def spec(self,budget=4):
        host_raw,host_pin=self.fixture("host-evidence.json")
        host_packet={"evidence":{k:host_pin["source"] if k=="path" else host_pin[k] for k in ("path","bytes","sha256")},"result":json.loads(host_raw)}
        host_path=self.directory/"host-wrapper.json";save(host_path,host_packet)
        review=json.loads(self.fixture("review.json")[0])
        return {"schema":"tb371fc-storage-batch-attempt-preparation-spec-v3",
                "previous":{"request":str(FIXTURE/"request.json"),"outer_result":str(FIXTURE/"outer.json"),
                            "source_termination":str(FIXTURE/"source-end.json"),"host_termination":str(host_path)},
                "review":{k:review[k] for k in ("outcome","reason","retained_files_reviewed")},
                "next":{"attempt_id":"attempt-000012","job_name":"lun0-tail-082","capture_name":"lun0-tail-082-offline-preparation-test",
                        "resume":False,"expected_completed_chunks":0,"host_timeout_seconds":260,
                        "source_deadline_seconds":240,"copy_preparation":None,"max_new_chunks":budget},
                "tmux":{"socket":"qemu-support-6a5","pane":"%9"},
                "inputs":{"jobs_plan":str(FIXTURE/"jobs.json"),"source_binding":str(FIXTURE/"source-binding.json"),
                          "caller":str(CANDIDATE/"tablet_sender_batch_v3.py"),"collector":str(CANDIDATE/"acquire_block_ranges_batch_v3.py")}}

    def prepare(self,spec,label="valid"):
        path=self.directory/(label+"-spec.json");save(path,spec)
        out=self.directory/(label+"-outputs")
        with contextlib.redirect_stdout(io.StringIO()):pins=prep.prepare(path,out)
        return out,{x["path"]:x for x in pins}

    def rejects(self,spec,label):
        path=self.directory/(label+"-spec.json");save(path,spec)
        out=self.directory/(label+"-outputs")
        with self.assertRaises((ValueError,AssertionError,KeyError,FileExistsError)):
            with contextlib.redirect_stdout(io.StringIO()):prep.prepare(path,out)
        self.assertFalse(out.exists(),"invalid input created a preparation packet")

    def synthetic_v2(self,spec,budget=4):
        # Construct explicitly test-only v2 observations from retained old metadata.
        request=json.loads(self.fixture("request.json")[0])
        old_sha=prep.sha(self.fixture("request.json")[0])
        request.update(schema="tb371fc-storage-supervised-attempt-v2",max_new_chunks=budget,
                       runtime=prep.candidate_runtime_pins())
        request_raw=prep.encoded(request);request_sha=prep.sha(request_raw)
        outer=json.loads(self.fixture("outer.json")[0])
        source=outer["source_result"];source["host_request_sha256"]=request_sha
        source.update(collector_sha256=prep.RUNTIME["collector"][1],max_new_chunks=budget,max_new_bytes=budget*prep.CHUNK)
        ack=source["acknowledgement"]
        ack.update(max_new_chunks=budget,new_chunks=budget,chunks=request["expected_completed_chunks"]+budget,
                   next_index=request["expected_completed_chunks"]+budget)
        ack["pause_receipt"]=str(Path(request["capture_directory"])/"pause-receipts"/("pause-%06d.json"%ack["chunks"]))
        source_raw=prep.encoded(source)
        outer.update(stdout_base64=base64.b64encode(source_raw).decode(),stdout_bytes=len(source_raw),stdout_sha256=prep.sha(source_raw))
        host=json.loads(Path(spec["previous"]["host_termination"]).read_bytes())
        host["result"]=replace_value(host["result"],old_sha,request_sha)
        for row in host["result"]["files"]:
            if row["kind"]!="regular":continue
            obj=row["json"]
            for key,value in (("max_new_chunks",budget),("max_new_bytes",budget*prep.CHUNK),("actual_new_chunks",budget)):
                if key in obj:obj[key]=value
            if "acknowledgement" in obj:obj["acknowledgement"]=copy.deepcopy(ack)
            raw=prep.encoded(obj);row.update(bytes=len(raw),sha256=prep.sha(raw))
        host["evidence"]=prep.file_pin(host["evidence"]["path"],prep.encoded(host["result"]))
        paths={}
        for key,name,obj in (("request","synthetic-v2-request.json",request),
                             ("outer_result","synthetic-v2-outer.json",outer),
                             ("host_termination","synthetic-v2-host.json",host)):
            path=self.directory/name;save(path,obj);paths[key]=str(path)
        spec=copy.deepcopy(spec);spec["previous"].update(paths)
        return spec,request,outer,host

    def validate_previous(self,spec):
        rows={k:Path(v).read_bytes() for k,v in spec["previous"].items()}
        return prep.validate_previous(rows["request"],rows["outer_result"],rows["source_termination"],
                                      rows["host_termination"],spec["review"],spec["tmux"]["pane"])

    def test_preparation_legacy_to_four_budget_full_chain(self):
        spec=self.spec(4);out,_=self.prepare(spec)
        request=json.loads((out/"request.json").read_bytes())
        payload=json.loads((out/"sender-payload.json").read_bytes())
        packet=json.loads((out/"ram-source-packet.json").read_bytes())
        ram_info=json.loads((out/"ram-preparation.json").read_bytes())
        deployment=json.loads((out/"deployment-packet.json").read_bytes())
        summary=json.loads((out/"preparation.json").read_bytes())
        self.assertEqual("tb371fc-storage-supervised-attempt-v2",request["schema"])
        self.assertEqual(prep.ROOT,request["namespace"]);self.assertEqual(4,request["max_new_chunks"])
        self.assertEqual(4,payload["max_new_chunks"]);self.assertEqual(payload,packet["payload"])
        self.assertEqual(prep.sha((out/"request.json").read_bytes()),payload["host_request"]["sha256"])
        self.assertEqual(request["runtime"],prep.candidate_runtime_pins())
        for name,pin in request["runtime"].items():
            filename={"collector":"acquire_block_ranges_batch_v3.py","interface":"interface-contract-batch-v3.json"}.get(name,name)
            raw=(CANDIDATE/filename).read_bytes();self.assertEqual((len(raw),prep.sha(raw)),(pin["bytes"],pin["sha256"]))
            self.assertEqual(prep.runtime_path(name),pin["path"])
        plan=json.loads((out/"approved-plan.json").read_bytes())
        self.assertEqual(prep.CHUNK,plan["chunk_bytes"]);self.assertEqual(268435456,4*plan["chunk_bytes"])
        self.assertEqual(json.loads((FIXTURE/"source-binding.json").read_bytes())["source"],plan["source"])
        self.assertEqual("O_RDONLY",plan["source_open_mode"]);self.assertFalse(plan["snapshot_consistent"])
        self.assertEqual(260,request["timeout_seconds"]);self.assertEqual(240,payload["source_deadline_seconds"])
        self.assertEqual(250,ram_info["controller_overdue_seconds"]);self.assertFalse(ram_info["commands_executed"])
        self.assertEqual(ram.RAM_ROOT+"/attempt-000012",ram_info["private_ram_directory"])
        self.assertNotEqual(str(controller.RAM_ROOT),PROOF["legacy_ram_root"])
        self.assertEqual(self.caller,base64.b64decode(packet["caller_base64"]))
        self.assertEqual(self.collector,base64.b64decode(packet["collector_base64"]))
        compile(controller.worker_code(packet),"<unexecuted-worker>","exec")
        self.assertFalse(summary["commands_executed"]);self.assertFalse(summary["runtime_modified"])
        self.assertEqual("attempt-000011",deployment["previous_attempt"])
        self.assertEqual(["attempt-%06d"%n for n in range(1,12)],deployment["expected_prior_attempts"])
        self.assertGreater(len(deployment["existing_pins"]),10)
        for path in out.glob("*command.txt"):
            code=path.read_text().split("\n",1)[1].rsplit("\nPY",1)[0]
            compile(code,str(path),"exec")
        for item in deployment["files"]:
            raw=base64.b64decode(item["base64"],validate=True);prep.matches_pin({k:item[k] for k in ("path","bytes","sha256")},raw)
        release=json.loads((out/"release.json").read_bytes())
        self.assertTrue(release["source_termination_confirmed"]);self.assertTrue(release["host_termination_confirmed"])
        self.assertEqual("attempt-000012",release["next_attempt_id"])

    def test_preparation_explicit_one_remains_supported(self):
        spec=self.spec(1);out,_=self.prepare(spec)
        request=json.loads((out/"request.json").read_bytes());self.assertEqual(1,request["max_new_chunks"])
        self.assertEqual(1,json.loads((out/"ram-source-packet.json").read_bytes())["payload"]["max_new_chunks"])

    def test_preparation_synthetic_v2_previous_chain(self):
        spec,request,outer,host=self.synthetic_v2(self.spec(),4)
        self.assertEqual(4,prep.request_budget(self.validate_previous(spec)[0]))
        out,_=self.prepare(spec)
        self.assertEqual(4,json.loads((out/"request.json").read_bytes())["max_new_chunks"])
        self.assertEqual(prep.sha(prep.encoded(request)),json.loads((out/"release.json").read_bytes())["previous_request_sha256"])

    def test_preparation_rejects_invalid_typed_budgets(self):
        spec=self.spec()
        for i,budget in enumerate((True,False,0,2,5,"4",4.0,None)):
            changed=copy.deepcopy(spec);changed["next"]["max_new_chunks"]=budget
            self.rejects(changed,"bad-budget-%d"%i)
        request=json.loads(self.fixture("request.json")[0]);request["max_new_chunks"]=4
        with self.assertRaises(ValueError):prep.request_budget(request)
        for budget in (True,2,5):
            request.update(schema="tb371fc-storage-supervised-attempt-v2",max_new_chunks=budget)
            with self.assertRaises(ValueError):prep.request_budget(request)

    def test_preparation_v2_rejects_missing_budget_and_legacy_source_downgrade(self):
        spec,request,outer,host=self.synthetic_v2(self.spec(),4)
        for name,change in (("missing-budget",lambda s:s.pop("max_new_chunks")),
                            ("wrong-budget",lambda s:s.update(max_new_chunks=1)),
                            ("legacy-result",lambda s:s.update(schema="tb371fc-storage-memory-sender-result-v1",tablet_files_created_or_written=False))):
            changed=copy.deepcopy(outer);change(changed["source_result"])
            raw=prep.encoded(changed["source_result"])
            changed.update(stdout_base64=base64.b64encode(raw).decode(),stdout_bytes=len(raw),stdout_sha256=prep.sha(raw))
            path=self.directory/(name+"-outer.json");save(path,changed)
            next_spec=copy.deepcopy(spec);next_spec["previous"]["outer_result"]=str(path)
            self.rejects(next_spec,name)

    def test_preparation_rejects_running_source_or_unreaped_worker(self):
        spec=self.spec()
        end=json.loads(self.fixture("source-end.json")[0]);end["processes"][0]["same_identity_running"]=True
        path=self.directory/"running-source.json";save(path,end)
        changed=copy.deepcopy(spec);changed["previous"]["source_termination"]=str(path);self.rejects(changed,"running")
        outer=json.loads(self.fixture("outer.json")[0]);outer["worker_reaped"]=False
        path=self.directory/"unreaped-outer.json";save(path,outer)
        changed=copy.deepcopy(spec);changed["previous"]["outer_result"]=str(path);self.rejects(changed,"unreaped")

    def test_preparation_source_jobs_and_code_pins_remain_fixed(self):
        spec=self.spec()
        for key in ("jobs_plan","source_binding","caller","collector"):
            path=self.directory/(key+"-modified")
            save(path,Path(spec["inputs"][key]).read_bytes()+b"\n")
            changed=copy.deepcopy(spec);changed["inputs"][key]=str(path);self.rejects(changed,"modified-"+key)

    def test_preparation_identity_and_deadlines_are_explicit(self):
        spec=self.spec()
        for i,(key,value) in enumerate((("attempt_id","attempt-000011"),("attempt_id","attempt-000013"),
                                       ("host_timeout_seconds",261),("host_timeout_seconds",True),
                                       ("source_deadline_seconds",239),("source_deadline_seconds",True))):
            changed=copy.deepcopy(spec);changed["next"][key]=value;self.rejects(changed,"identity-%d"%i)

    def test_ram_launcher_and_controller_reject_unpinned_or_unbounded_input(self):
        out,_=self.prepare(self.spec())
        payload=json.loads((out/"sender-payload.json").read_bytes())
        packet=json.loads((out/"ram-source-packet.json").read_bytes())
        for which in ("caller","collector","controller"):
            args={"caller":self.caller,"collector":self.collector,"controller":self.controller};args[which]+=b"\n"
            with self.assertRaises(ValueError):ram.build("capture","attempt-000012",payload,**args)
        for budget in (True,0,2,5,"4"):
            changed=copy.deepcopy(payload);changed["max_new_chunks"]=budget
            with self.assertRaises(ValueError):ram.build("capture","attempt-000012",changed,self.caller,self.collector,self.controller)
            changed_packet=copy.deepcopy(packet);changed_packet["payload"]=changed
            with self.assertRaises(ValueError):controller.worker_code(changed_packet)
        for key in ("caller_base64","collector_base64"):
            changed=copy.deepcopy(packet);changed[key]=base64.b64encode(base64.b64decode(packet[key])+b"\n").decode()
            with self.assertRaises(ValueError):controller.worker_code(changed)
        probe=ram.build("probe","probe-000001",None,b"",b"",self.controller)
        probe_packet=json.loads(probe["source-packet.json"])
        self.assertEqual({"schema","mode","attempt_id","boot_id"},set(probe_packet))
        self.assertIn(b"'block_source_opened':False",controller.worker_code(probe_packet))
        with self.assertRaises(ValueError):ram.build("probe","probe-000001",None,b"",b"",b"print('arbitrary')")

    def test_preparation_never_overwrites_existing_output(self):
        spec=self.spec();out,_=self.prepare(spec)
        before={x.name:prep.sha(x.read_bytes()) for x in out.iterdir()}
        with self.assertRaises(FileExistsError):
            with contextlib.redirect_stdout(io.StringIO()):prep.prepare(self.directory/"valid-spec.json",out)
        self.assertEqual(before,{x.name:prep.sha(x.read_bytes()) for x in out.iterdir()})

    def test_preparation_generated_guards_refuse_changed_receipts_or_existing_next_id(self):
        # Execute only unchanged host-preparation guards in an isolated local tree.
        # The production dead-pane check is separately AST-identical and fixture-validated.
        root=self.directory/"isolated-host";root.mkdir(mode=0o700)
        for name in ("attempts","bindings","preparations","code","captures"):(root/name).mkdir(mode=0o700)
        save(root/"supervisor.lock",b"")
        old=root/"attempts/attempt-000001";old.mkdir(mode=0o700)
        receipt=save(old/"receiver-start.json",{"synthetic":True})
        p={"existing_pins":[receipt],"previous_attempt":"attempt-000001",
           "previous_files":["receiver-start.json"],"previous_processes":[],
           "expected_prior_attempts":["attempt-000001"],"files":[]}
        env={"p":p,"r":root,"m":sup,"os":os,"fcntl":__import__("fcntl"),"dead_pane":lambda:None,"pincheck":sup.pin_file}
        tree=ast.parse(prep.HOST_COMMON.replace("PACKET\n",""))
        previous=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="previous_still_ended")
        exec(compile(ast.Module(body=[previous],type_ignores=[]),"<guard-fixture>","exec"),env)
        env["previous_still_ended"]()
        (root/"attempts/attempt-000002").mkdir(mode=0o700)
        with self.assertRaises(AssertionError):exec(compile(prep.HOST_PREPARE,"<guard-fixture>","exec"),env)
        lock=sup.held_lock(root/"supervisor.lock");os.close(lock)
        self.assertEqual([],list((root/"bindings").iterdir()))
        (old/"receiver-start.json").write_bytes(b'{"changed":true}\n')
        with self.assertRaises(ValueError):exec(compile(prep.HOST_PREPARE,"<guard-fixture>","exec"),env)
        self.assertEqual([],list((root/"bindings").iterdir()))

    def test_original_ram_guards_and_preparation_release_guards_unchanged(self):
        funcs={n.name:n for n in ast.parse((CANDIDATE/"ram_source_controller_batch_v3.py").read_bytes()).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
        for name,digest in PROOF["function_ast_sha256"]["ram_source_controller_batch_v3.py"].items():
            self.assertEqual(digest,hashlib.sha256(ast.dump(funcs[name],include_attributes=False).encode()).hexdigest(),name)
        for name in ("HOST_PREPARE","HOST_LAUNCH","HOST_OBSERVE"):
            value=getattr(prep,name)
            if name=="HOST_LAUNCH":value=value.replace("host_supervisor_batch_v3.py","host_supervisor.py")
            self.assertEqual(PROOF["preparation_template_sha256"][name],hashlib.sha256(value.encode()).hexdigest(),name)
        funcs={n.name:n for n in ast.parse(prep.HOST_COMMON.replace("PACKET\n","")).body if isinstance(n,ast.FunctionDef)}
        for name,digest in PROOF["preparation_host_function_ast_sha256"].items():
            self.assertEqual(digest,hashlib.sha256(ast.dump(funcs[name],include_attributes=False).encode()).hexdigest(),name)
        self.assertEqual("a90c1161c6cbce7d0fada8e1901354f0a58b5707e02231d206a0c1f663a1a77a",PRODUCTION_DIGESTS["jobs_plan_sha256"])
        self.assertEqual("78fa6806272f40e906dabbf9c75bb6553642c2fbe9e1deb7d0f20a784cc8b131",PRODUCTION_DIGESTS["source_binding_sha256"])
