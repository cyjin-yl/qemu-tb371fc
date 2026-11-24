#!/usr/bin/env python3
"""Pure AST/mock verification for the appended CPUFreq observer only."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import unittest

OBSERVER=Path("/run/tb371fc-cpufreq-observer-a47/native-cpufreq-observer.py")
SOURCE=OBSERVER.read_text()
TREE=ast.parse(SOURCE,filename=str(OBSERVER))
NODES=[node for node in TREE.body
       if isinstance(node,ast.FunctionDef) and node.name=="capture_cpufreq_hw"
       or isinstance(node,ast.Assign) and any(isinstance(t,ast.Name)
                                             and t.id=="CPUFREQ_DOMAINS"
                                             for t in node.targets)]
assert len(NODES)==2
ENV={"json":json}
exec(compile(ast.Module(body=NODES,type_ignores=[]),"<isolated-cpufreq-observer>","exec"),ENV)
capture=ENV["capture_cpufreq_hw"]
DOMAINS=ENV["CPUFREQ_DOMAINS"]
LVAL=(16,32,64,96)
MV=(750,800,900,1000)

def fixture():
    data={}
    for d in DOMAINS:
        i=d["domain_id"]
        state={
            "enabled-reference":True,"domain-id":i,"reference-boot-perf-index":0,
            "memory-base":d["memory_base"],"resource-size":4096,
            "related-cpu-mask":d["related_cpu_mask"],"max-cores":4,
            "core-counter-count":len(d["cpu_ids"]),"lut-max-entries":40,
            "lut-row-size":4,"valid-perf-count":4,
            "xo-frequency-hz":19200000,"alternate-frequency-hz":600000000,
            "performance-index":i,"requested-lval":LVAL[i],"effective-lval":LVAL[i],
            "requested-frequency-hz":19200000*LVAL[i],"frequency-hz":19200000*LVAL[i],
            "selected-voltage-microvolt":MV[i]*1000,"interrupt-status":0,
            "frequency-limit-lval":0,"limited":False,"frequency-known":True,
            "irq-level":False,"counter":1234+i,"counter-fraction":5678,
            "counter-value-at-epoch":1230+i,"counter-fraction-at-epoch":12,
            "counter-epoch-ns":1000}
        for n in range(40):
            j=min(n,3)
            state.update({f"lut{n}-frequency-word":0x40040000|LVAL[j],
                          f"lut{n}-voltage-word":MV[j],
                          f"lut{n}-frequency-hz":19200000*LVAL[j]})
        for n in range(len(d["cpu_ids"])):
            state[f"core{n}-counter"]=state["counter"]
        data[d["path"]]=state
    return data

class Mock:
    def __init__(self):
        self.data=fixture()
        self.statuses=[{"status":"paused","running":False},
                       {"status":"paused","running":False}]
        self.calls=[]
    def __call__(self,command,arguments=None):
        self.calls.append((command,copy.deepcopy(arguments)))
        if command=="query-status":
            if not self.statuses:
                raise AssertionError("unexpected extra query-status")
            return copy.deepcopy(self.statuses.pop(0))
        if command!="qom-get":
            raise AssertionError("observer issued a forbidden command: "+command)
        return copy.deepcopy(self.data[arguments["path"]][arguments["property"]])

class Tests(unittest.TestCase):
    def setUp(self):
        self.m=Mock()
        self.d=self.m.data["/machine/cpufreq0"]
    def collected(self):
        r=capture(self.m)
        self.assertFalse(r["native_driver_success_assessed"])
        self.assertFalse(r["userspace_success_assessed"])
        self.assertFalse(r["counter_progress_observed"])
        self.assertFalse(r["native_irq_delivery_proven"])
        self.assertFalse(r["factory_lut_observed"])
        self.assertFalse(r["guest_memory_written"])
        self.assertFalse(r["device_mmio_read"])
        self.assertFalse(r["host_environment_modified_by_observer"])
        self.assertFalse(r["guest_cpu_executed_by_observer"])
        self.assertEqual(self.m.calls[0][0],"query-status")
        self.assertEqual(self.m.calls[-1][0],"query-status")
        return r
    def bad(self):
        with self.assertRaises((RuntimeError,KeyError)):
            capture(self.m)
    def test_complete_raw_snapshot_only(self):
        r=self.collected()
        self.assertEqual(r["observation_status"],"complete")
        self.assertEqual((r["domain_count"],r["geometry_matches"],
                          r["domains_with_consistent_reference_state"]),(3,3,3))
        self.assertEqual(sum(c[0]=="qom-get" for c in self.m.calls),455)
        self.assertEqual(len(self.m.calls),457)
        self.assertEqual(sum(len(x["core_counters"]) for x in r["domains"]),8)
        self.assertEqual(sum(len(x["lut_rows"]) for x in r["domains"]),120)
    def test_unknown_profile_is_inconclusive(self):
        self.d["enabled-reference"]=False
        r=self.collected()
        self.assertEqual(r["observation_status"],"inconclusive")
        self.assertEqual(r["domains_with_raw_state"],2)
        self.assertEqual(r["domains"][0]["raw_state_unavailable"],"unknown-profile")
    def test_bool_not_uint_configuration(self):
        self.d["domain-id"]=True
        self.bad()
    def test_bool_not_uint_state(self):
        self.d["counter"]=False
        self.bad()
    def test_negative_unsigned_counter(self):
        self.d["counter"]=-1
        self.bad()
    def test_out_of_range_fraction(self):
        self.d["counter-fraction"]=1000000000
        self.bad()
    def test_missing_device_property_still_checks_stopped_after(self):
        del self.d["counter"]
        self.bad()
        self.assertEqual(self.m.calls[-1][0],"query-status")
        self.assertFalse(self.m.statuses)
    def test_running_before_does_not_read_qom(self):
        self.m.statuses[0]={"status":"running","running":True}
        self.bad()
        self.assertEqual([x[0] for x in self.m.calls],["query-status"])
    def test_running_after_is_rejected(self):
        self.m.statuses[1]={"status":"running","running":True}
        self.bad()
    def test_changed_stopped_status_is_rejected(self):
        self.m.statuses[1]={"status":"postmigrate","running":False}
        self.bad()
    def test_geometry_mismatch_never_complete(self):
        self.d["memory-base"]+=0x1000
        r=self.collected()
        self.assertEqual(r["observation_status"],"inconclusive")
        self.assertEqual(r["geometry_matches"],2)
    def test_reserved_irq_bits_are_rejected(self):
        self.d["interrupt-status"]=8
        self.bad()
    def test_inconsistent_rate_never_complete(self):
        self.d["frequency-hz"]+=1
        r=self.collected()
        self.assertEqual(r["observation_status"],"inconclusive")
        self.assertEqual(r["domains_with_consistent_reference_state"],2)
    def test_wrong_reference_row_never_complete(self):
        self.d["lut4-frequency-word"]+=1
        r=self.collected()
        self.assertEqual(r["observation_status"],"inconclusive")
        self.assertFalse(r["domains"][0]["all_lut_rows_match_declared_reference"])
    def test_zero_cold_counter_does_not_claim_progress(self):
        for d in self.m.data.values():
            d["counter"]=d["counter-fraction"]=0
            d["counter-value-at-epoch"]=d["counter-fraction-at-epoch"]=0
            d["counter-epoch-ns"]=0
            for key in list(d):
                if key.startswith("core") and key.endswith("-counter"):
                    d[key]=0
        r=self.collected()
        self.assertEqual(r["observation_status"],"complete")
        self.assertFalse(r["counter_progress_observed"])
    def test_unknown_clock_is_recorded_as_unknown_not_ready(self):
        for d in self.m.data.values():
            d["xo-frequency-hz"]=0
            d["requested-frequency-hz"]=d["frequency-hz"]=0
            d["frequency-known"]=False
            for n in range(40):
                d[f"lut{n}-frequency-hz"]=0
        r=self.collected()
        self.assertEqual(r["observation_status"],"complete")
        self.assertEqual(r["domains_with_known_frequency"],0)
        self.assertFalse(r["native_driver_success_assessed"])
    def test_latched_irq_after_restriction_is_legitimate(self):
        self.d["interrupt-status"]=4
        self.d["irq-level"]=True
        r=self.collected()
        self.assertEqual(r["observation_status"],"complete")
        self.assertFalse(r["domains"][0]["state"]["limited"])
    def test_w1c_during_unchanged_restriction_is_legitimate(self):
        self.d["frequency-limit-lval"]=8
        self.d["effective-lval"]=8
        self.d["frequency-hz"]=19200000*8
        self.d["limited"]=True
        r=self.collected()
        self.assertEqual(r["observation_status"],"complete")
        self.assertFalse(r["domains"][0]["state"]["irq-level"])
    def test_bool_not_uint_lut_word(self):
        self.d["lut3-frequency-word"]=True
        self.bad()
    def test_terminal_index_not_a_valid_performance_state(self):
        self.d["performance-index"]=4
        self.bad()

if __name__=="__main__":
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    print(json.dumps({"schema":"cpufreq-observer-isolated-mock-review-v1",
                      "observer_bytes":len(SOURCE.encode()),
                      "observer_sha256":hashlib.sha256(SOURCE.encode()).hexdigest(),
                      "tests_run":result.testsRun,"failures":len(result.failures),
                      "errors":len(result.errors),"qemu_executed":False,
                      "legacy_wrapper_imported_or_executed":False},sort_keys=True))
    raise SystemExit(not result.wasSuccessful())

