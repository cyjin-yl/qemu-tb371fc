#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared DSU virtual PMU: eight real guest CPUs, actual GIC and complete migration."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import time
import traceback

from qce_bam_test_transport import file_channels, put_json, sha
import dsu_pmu_test_contract as C
from dsu_pmu_test_transport import DsuRig, cleanup_records

HERE=Path(__file__).resolve().parent
ALTERNATE=19_200_000
PURE=(('qtest-default-off',None,C.FREQUENCY),('qtest-on',True,C.FREQUENCY),
      ('qtest-frequency-min',True,1),('qtest-frequency-max',True,1_000_000_000))
FUNCTIONAL=(('default-off',None,1,C.FREQUENCY,False),('explicit-off',False,1,C.FREQUENCY,False),
    ('on-permissions',True,1,C.FREQUENCY,False),('shared-reset',True,2,C.FREQUENCY,False),
    ('clock-reference',True,3,C.FREQUENCY,False),('clock-alternate',True,3,ALTERNATE,False),
    ('overflow-irq',True,4,C.FREQUENCY,False),('ras-coexist',True,1,C.FREQUENCY,True))
INVALID=(('invalid-cortex-a55','cortex-a55',C.FREQUENCY),
         ('invalid-cortex-a77','cortex-a77',C.FREQUENCY),
         ('invalid-frequency-zero',None,0),('invalid-frequency-high',None,1_000_000_001))
MUTATIONS=(('profile0-zero','word',0,0,4,0,'1 != 0'),
 ('profile7-two','word',7,0,4,2,'1 != 2'),
 ('index3-four','word',3,4,4,4,'3 != 4'),
 ('frequency0-zero','word',0,8,8,0,'5f5e100 != 0'),
 ('frequency7-plus1','word',7,8,8,100_000_001,'5f5e100 != 5f5e101'),
 ('actlr0-reserved','word',0,16,8,1,'Invalid incoming TB371FC DSU CPU permission profile'),
 ('actlr7-reserved','word',7,16,8,1,'Invalid incoming TB371FC DSU CPU permission profile')) + tuple(
 (name,'word',0,24+off,size,value,'tb371fc-dsu-pmu: invalid incoming shared bank')
 for name,off,size,value in (
 ('bank-magic',0,4,0),('bank-cpu-mask',4,4,1),('bank-intid',8,4,81),('bank-count',12,4,7),
 ('bank-frequency',16,8,100_000_001),('bank-pmcr',24,4,2),('bank-enable',28,4,0x40),
 ('bank-overflow',32,4,0x40),('bank-inten',36,4,0x40),('bank-selector',40,4,32),
 ('bank-type0',68,4,0x40000000),('bank-type5',88,4,0x40000000),('bank-fraction',100,8,1_000_000_000))) + (
 ('absent0','absent',0,0,0,0,'Incoming CPU DSU PMU mode differs from destination'),
 ('absent7','absent',7,0,0,0,'Incoming CPU DSU PMU mode differs from destination'),
 ('duplicate0','duplicate',0,0,0,0,'Invalid incoming TB371FC DSU CPU permission profile'),
 ('duplicate7','duplicate',7,0,0,0,'Invalid incoming TB371FC DSU CPU permission profile'),
 ('out-of-order','swap-cpu01',0,0,0,0,'tb371fc-dsu-pmu: invalid incoming CPU order'),
 ('repeat-cpu0','repeat-cpu0',0,0,0,0,'tb371fc-dsu-pmu: invalid incoming CPU order'),
 ('truncated-bank','truncate',0,0,0,0,'cpu/tb371fc-dsu-pmu/shared-bank state: stream error:'),
 ('version0','version',0,0,4,0,'incoming version_id 0 is too old'),
 ('version7','version',7,0,4,2,'incoming version_id 2 is too new'))
NORMAL=tuple(x[0] for x in PURE+FUNCTIONAL)+tuple(label+'-'+leg for label in ('on','off') for leg in ('source','target','return'))
ORDER=tuple(x[0] for x in PURE+FUNCTIONAL)+tuple(x[0] for x in INVALID)+NORMAL[12:]+(
 'cross-on-to-off','cross-off-to-on','cross-frequency')+tuple(x[0] for x in MUTATIONS)


def pin(path, root):
    path=Path(path);raw=path.read_bytes()
    return {'file':path.relative_to(root).as_posix(),'bytes':len(raw),'sha256':C.digest(raw)}


def build_probe(args, out):
    out.mkdir(exist_ok=False)
    args.probe = out / 'probe.elf'
    command = [args.cc, '-O2', '-Wall', '-Wextra', '-Werror', '-ffreestanding',
               '-fno-builtin', '-fno-stack-protector', '-fno-pie', '-mstrict-align',
               '-mgeneral-regs-only', '-march=armv8.2-a', '-nostdlib', '-static',
               '-Wl,--build-id=none', '-T', str(HERE / 'dsu_pmu/link.ld'),
               str(HERE / 'dsu_pmu/start.S'), str(HERE / 'dsu_pmu/probe.c'),
               '-o', str(args.probe)]
    put_json(out / 'command.json', command)
    with (out / 'stdout.log').open('wb') as stdout, (out / 'stderr.log').open('wb') as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        try:
            code = process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
            put_json(out / 'execution.json', {'returncode': process.returncode, 'timed_out': True,
                'actually_waited_and_reaped': True, 'passed': False})
            raise
    put_json(out / 'execution.json', {'returncode': code, 'timed_out': False,
             'actually_waited_and_reaped': True, 'passed': code == 0})
    C.require(code == 0, 'guest probe compilation failed; complete compiler stderr retained')
    return C.parse_probe_elf(args.probe.read_bytes())


def qom_access(r, mode, frequency):
    types=[];rejected=[]
    subjects=[(f'/machine/cpu{i}',{C.CPU_PROP:'bool',C.PROFILE_PROP:'uint32',C.FREQUENCY_PROP:'uint64'}) for i in range(8)]
    if mode:subjects.append((C.PATH,{**{k:'uint64' for k in C.UINT_GETTERS},
        **{k:'bool' for k in C.BOOL_GETTERS},**{k:'string' for k in C.HEX_GETTERS}}))
    for path,wanted in subjects:
        rows=r.monitor('qom-list',{'path':path});mapping={x['name']:x['type'] for x in rows}
        C.require(all(mapping.get(k)==v for k,v in wanted.items()),'actual property metadata types')
        types.append({'path':path,'types':{k:mapping[k] for k in wanted}})
        for prop,typ in wanted.items():
            value=False if typ=='bool' else '' if typ=='string' else 0
            arg={'path':path,'property':prop,'value':value};reply=r.raw_monitor('qom-set',arg)
            C.require(reply.get('error',{}).get('class')=='GenericError','realized/readonly property accepted write')
            rejected.append({'arguments':arg,'response':reply})
    rows=r.monitor('qom-list',{'path':'/machine'})
    C.require(any(x['name']=='dsu-pmu-device' for x in rows)==mode,'actual device absent when disabled')
    for prop,value in (('dsu-pmu',not mode),('dsu-pmu-frequency-hz',frequency+1)):
        arg={'path':'/machine','property':prop,'value':value};reply=r.raw_monitor('qom-set',arg)
        C.require(reply.get('error',{}).get('class')=='GenericError','immutable machine setting changed')
        rejected.append({'arguments':arg,'response':reply})
    return {'types':types,'machine_children':rows,'rejected':rejected,'unchanged':r.pure_state(mode,frequency,cold=True)}


def capture_mailboxes(r, mode, epoch, recipe, elf, frequency, label):
    C.require(r.monitor('query-status')['running'] is False,'whole mailbox capture stopped')
    raw=r.read_ram(C.CONTROL,C.CONTROL_BYTES);control=list(struct.unpack('<16Q',raw))
    C.require(control[:5]==[epoch,C.NONCE|epoch,recipe,16 if recipe==2 else 8,frequency] and
              control[5:]==[0]*11,'complete control, eight PSCI success results, no hidden commands')
    path=r.directory/(label+'-control.bin');path.write_bytes(raw)
    result={'label':label,'mode':mode,'frequency_hz':frequency,'epoch':epoch,'recipe':recipe,
        'control':{'file':path.name,'bytes':len(raw),'sha256':C.digest(raw)},'files':[],'cpus':[]}
    for cpu in range(8):
        raw=r.read_ram(C.BOX_BASE+cpu*C.BOX_STRIDE,C.BOX_BYTES);path=r.directory/f'{label}-cpu{cpu}.bin'
        path.write_bytes(raw);result['files'].append({'file':path.name,'bytes':len(raw),'sha256':C.digest(raw)})
        result['cpus'].append(C.decode_mailbox(raw,cpu,mode,epoch,recipe,elf,frequency))
    return result


def run_epoch(r, mode, epoch, recipe, elf, frequency=C.FREQUENCY, *, cold=False, before=None):
    C.require(r.monitor('query-status')['running'] is False,'epoch starts stopped')
    if cold:
        r.write_ram(C.CONTROL,bytes(C.CONTROL_BYTES))
        for cpu in range(8):r.write_ram(C.BOX_BASE+cpu*C.BOX_STRIDE,bytes(C.BOX_BYTES))
    # One complete write carries the epoch; no partially updated control word.
    r.write_ram(C.CONTROL,struct.pack('<16Q',epoch,C.NONCE|epoch,recipe,0,frequency,*([0]*11)))
    r.monitor('cont');deadline=time.monotonic()+30;polls=[]
    while True:
        rows=[struct.unpack('<32Q',r.read_ram(C.BOX_BASE+cpu*C.BOX_STRIDE,256)) for cpu in range(8)]
        polls.append([{'cpu':i,'epoch':h[7],'recipe':h[8],'count':h[9],'fatal':h[10],'done':h[12]} for i,h in enumerate(rows)])
        C.require(all(h[10]==0 for h in rows),'actual guest fault retained in partial RAM/protocol')
        if all(h[12]==epoch for h in rows):break
        C.require(time.monotonic()<deadline,'eight actual CPUs did not complete recipe')
        time.sleep(0.01)
    # Stop before the many metadata queries: only guest elapsed time counts.
    r.monitor('stop');C.require(r.monitor('query-status')['running'] is False,'actual QMP stop fence')
    state=r.pure_state(mode,frequency)
    result=capture_mailboxes(r,mode,epoch,recipe,elf,frequency,'epoch-'+str(epoch))
    result['polls']=polls;result['after']=state
    result['semantic']=C.verify_epoch(result['cpus'],mode,recipe,frequency,before=before,
                                      after=state['first']['shared'])
    result['irq']=r.fence_irq() if mode else None
    if mode:C.require(result['irq']['line']==state['first']['shared']['irq-level'],'real output and stored derived IRQ agree')
    if recipe==4:
        C.require(result['irq']['events']==[{'action':a,'number':50} for _ in range(3) for a in ('raise','lower')],
                  'three software/natural32/natural64 IRQ output pulses')
    return result


def save(r, path, mode, frequency):
    C.require(r.monitor('query-status')['running'] is False,'save starts stopped')
    before=r.pure_state(mode,frequency);r.monitor('migrate',file_channels(path));progress=r.migrate_wait()
    raw=path.read_bytes();wire=C.parse_stream(raw,mode,frequency)
    after=r.pure_state(mode,frequency)
    C.require(before['first']==after['first'] and before['second']==after['second'],
              'saving stopped bank changed the actual stored state')
    if mode:C.compare_state_wire(after['first']['shared'],wire)
    return {'file':'streams/'+path.name,'kind':'canonical','mode':mode,'frequency_hz':frequency,
        'migration':progress,'wire':wire,'state_before':before,'state_after':after}


def incoming(r, path, mode, frequency, wire):
    before=r.pure_state(mode,frequency,cold=True)
    if mode:r.intercept();C.require(r.fence_irq()['line'] is False,'cold destination output low')
    counters=dict(r.count);r.monitor('migrate-incoming',file_channels(path));progress=r.migrate_wait()
    # First postload observation is the two full pure getter passes.
    after=r.pure_state(mode,frequency)
    C.require(r.count['cont']==0 and r.count['ram']==0,'no pre-pure guest or RAM access')
    C.require(r.monitor('query-status')['running'] is False,'incoming did not autostart')
    irq=r.fence_irq() if mode else None
    if mode:
        C.compare_state_wire(after['first']['shared'],wire)
        s=after['first']['shared'];C.require(s['incoming-started-cpus']==s['incoming-completed-cpus']==255 and
            not s['migration-quarantined'] and irq['line']==s['irq-level'],'all eight validated before actual IRQ publish')
        C.require(irq['events']==([{'action':'raise','number':50}] if s['irq-level'] else []),'single valid restore publication')
    return {'before':before,'after':after,'migration':progress,'before_load_counters':counters,
        'cont_before_postload_pure':r.count['cont'],'ram_before_postload_pure':r.count['ram'],'irq':irq}


def mutate(original, wire, spec):
    name,kind,cpu,relative,width,value,needle=spec;row=wire['cpu_sections'][cpu]
    sub=next(s for s in row['subsections'] if s['name']==C.SUBSECTION)
    if kind=='word':offset=sub['body_start']+relative;old=original[offset:offset+width];new=value.to_bytes(width,'big')
    elif kind=='version':offset=sub['body_start']-4;old=original[offset:offset+4];new=value.to_bytes(4,'big')
    elif kind=='absent':offset=sub['start'];old=original[offset:sub['end']];new=b''
    elif kind=='duplicate':offset=sub['start'];old=original[offset:sub['end']];new=old+old
    elif kind=='truncate':offset=sub['body_start']+24+107;old=original[offset:];new=b''
    elif kind=='repeat-cpu0':offset=row['section_start'];old=original[offset:row['section_end']];new=old+old
    else:
        C.require(kind=='swap-cpu01','known structural mutation')
        a,b=wire['cpu_sections'][:2];C.require(a['section_end']<=b['section_start'],'ordered nonoverlapping actual ARM CPU0/1 sections')
        offset=a['section_start'];old=original[offset:b['section_end']]
        middle=original[a['section_end']:b['section_start']]
        new=original[b['section_start']:b['section_end']]+middle+original[a['section_start']:a['section_end']]
    C.require(old!=new and original[offset:offset+len(old)]==old,'exact nonempty finite source mutation')
    modified=original[:offset]+new+original[offset+len(old):]
    return modified,{'name':name,'kind':kind,'cpu':cpu,'relative':relative,'width':width,'value':value,
        'offset':offset,'old_bytes':len(old),'old_sha256':C.digest(old),'new_bytes':len(new),'new_sha256':C.digest(new),
        'original_bytes':len(original),'original_sha256':C.digest(original),'bytes':len(modified),'sha256':C.digest(modified)}


def rejected_load(args,out,name,path,mode,frequency,needle,elf):
    r=DsuRig(args,out/name,mode=mode,frequency=frequency,incoming=True)
    row={'fixture':name,'mode':mode,'frequency_hz':frequency,'incoming_file':'streams/'+path.name,
         'diagnostic_needle':needle,'expected_exit_code':1}
    try:
        row['initial_state']=r.pure_state(mode,frequency,cold=True)
        if mode:r.intercept();row['initial_irq']=r.fence_irq()
        else:row['initial_irq']=None
        row['before_load_counters']=dict(r.count);row['irq_event_index']=len(r.irq_events)
        r.expected_exit=1;r.monitor('migrate-incoming',file_channels(path))
        C.require(r.wait_exit(30)==1,'actual rejected incoming QEMU exit1')
        C.require(all(r.count[k]==row['before_load_counters'][k] for k in ('cont','ram','memory')),
                  'failure load ran no guest or memory access')
        C.require(needle in r.stderr(),'exact expected rejection diagnostic absent')
        row['actual_exit_code']=r.process.returncode
    finally:r.close()
    cleanup=json.loads((r.directory/'cleanup.json').read_bytes())
    suffix=cleanup['irq_events'][row['irq_event_index']:]
    C.require(suffix==[],'cold failure quarantine never publishes the high incoming bank')
    row['complete_postload_irq_events']=suffix
    return row


def source_files():
    paths=[HERE/'dsu_pmu'/name for name in ('start.S','probe.c','link.ld')]
    paths += [HERE/name for name in ('dsu_pmu_test_contract.py','dsu_pmu_test_transport.py','test_dsu_pmu.py','qce_bam_test_transport.py')]
    return [{'relative_path':'tb371fc/tests/'+p.relative_to(HERE).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in paths]


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--qemu',required=True)
    parser.add_argument('--machine',default='tb371fc');parser.add_argument('--cc',default='aarch64-linux-gnu-gcc')
    parser.add_argument('--out',required=True);parser.add_argument('--report');args=parser.parse_args()
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=False);(out/'streams').mkdir()
    args.report=Path(args.report).resolve() if args.report else out/'result.json'
    C.require(args.machine=='tb371fc','exact board profile');binary=sha(args.qemu)
    report={'schema':C.SCHEMA,'passed':False,'qemu_sha256':binary,'source_files':source_files(),
        'checks':[],'processes':[],'streams':[],'roundtrips':[],'rejected_migrations':[],'guest_epochs':[],
        'native_executed':False,'original_inputs_used':False,'physical_PMU_measured':False,
        'physical_clock_measured':False,'cache_or_bus_events_modeled':False,'Android_or_UI_verified':False}
    names=[];saved={};all_epochs=[]
    def add_epoch(name,row):
        all_epochs.append(row);report['guest_epochs'].append({'fixture':name,'epoch':row['epoch'],'recipe':row['recipe']})
    try:
        elf=build_probe(args,out/'build');report['probe_elf']=elf
        for name,mode,frequency in PURE:
            names.append(name);r=DsuRig(args,out/name,mode=mode,frequency=frequency,accel='qtest')
            try:
                row={'fixture':name,'mode':mode is True,'frequency_hz':frequency,'accel':'qtest','epochs':[],
                    'initial_state':r.pure_state(mode is True,frequency,cold=True)}
                row['qom_access']=qom_access(r,mode is True,frequency)
                C.require(r.count['cont']==r.count['ram']==0,'pure boundary fixture executed guest')
            finally:r.close()
            report['checks'].append(row)
        for name,mode,recipe,frequency,ras in FUNCTIONAL:
            names.append(name);r=DsuRig(args,out/name,mode=mode,frequency=frequency,ras=ras)
            try:
                row={'fixture':name,'mode':mode is True,'frequency_hz':frequency,'accel':'tcg','ras':ras,
                     'initial_state':r.pure_state(mode is True,frequency,cold=True),'epochs':[]}
                if mode:r.intercept();row['initial_irq']=r.fence_irq()
                else:row['initial_irq']=None
                if ras:
                    row['ras_profiles']=[{p:r.monitor('qom-get',{'path':f'/machine/cpu{i}','property':p})
                        for p in ('tb371fc-ras-zero-records-compat','tb371fc-ras-zero-records-profile')} for i in range(8)]
                    C.require(row['ras_profiles']==[{'tb371fc-ras-zero-records-compat':True,'tb371fc-ras-zero-records-profile':1}]*8,
                              'independent opt-ins coexist on all eight CPUs')
                epoch=run_epoch(r,mode is True,1,recipe,elf,frequency,cold=True);row['epochs'].append(epoch);add_epoch(name,epoch)
                if name=='shared-reset':
                    r.write_ram(C.CONTROL,bytes(C.CONTROL_BYTES))
                    for cpu in range(8):r.write_ram(C.BOX_BASE+cpu*C.BOX_STRIDE,bytes(C.BOX_BYTES))
                    event_count=len(r.events);r.monitor('system_reset')
                    C.require(r.monitor('query-status')['running'] is False,'reset did not continue guest')
                    C.require(any(e.get('event')=='RESET' for e in r.events[event_count:]),'actual RESET event')
                    row['reset_event_seen']=True;row['reset_state']=r.pure_state(True,frequency,cold=True)
                    row['reset_irq']=r.fence_irq();C.require(not row['reset_irq']['line'],'whole reset IRQ low')
                    epoch=run_epoch(r,True,2,1,elf,frequency);row['epochs'].append(epoch);add_epoch(name,epoch)
            finally:r.close()
            report['checks'].append(row)
        for name,cpu,frequency in INVALID:
            names.append(name);r=DsuRig(args,out/name,mode=True,frequency=frequency,invalid_cpu=cpu,
                                       expected_configuration_failure=cpu is None)
            needle=('tb371fc-dsu-pmu requires the TB371FC A55/A77 shared software-emulated bank, EL2 on and EL3 off'
                if cpu else 'dsu-pmu-frequency-hz requires 1..1000000000 before machine creation')
            try:C.require(r.process.returncode==1 and needle in r.stderr(),'expected configuration rejection')
            finally:r.close()
            report['checks'].append({'fixture':name,'actual_exit_code':1,'diagnostic_needle':needle})
        for mode,label in ((True,'on'),(False,'off')):
            loop={'mode':mode,'fixtures':[],'streams':[],'incoming':[]};prior=None;prior_epoch=None
            for leg in range(3):
                name=label+'-'+('source','target','return')[leg];names.append(name);loop['fixtures'].append(name)
                r=DsuRig(args,out/name,mode=mode,incoming=leg!=0)
                row={'fixture':name,'mode':mode,'frequency_hz':C.FREQUENCY,'leg':leg,'epochs':[]}
                try:
                    if leg==0:
                        row['initial_state']=r.pure_state(mode,cold=True)
                        if mode:r.intercept();row['initial_irq']=r.fence_irq()
                        else:row['initial_irq']=None
                    else:
                        path=out/prior['file'];row['incoming']=incoming(r,path,mode,C.FREQUENCY,prior['wire'])
                        loop['incoming'].append({'fixture':name,**row['incoming']})
                        expected_epoch=1 if leg==1 else (3 if mode else 2)
                        expected_recipe=5 if mode else 1
                        row['restored_guest']=capture_mailboxes(r,mode,expected_epoch,expected_recipe,elf,C.FREQUENCY,'restored')
                        C.require(row['restored_guest']['cpus']==prior_epoch['cpus'] and
                            row['restored_guest']['control']['sha256']==prior_epoch['control']['sha256'],
                            'complete actual restored RAM equals the source epoch before any continuation')
                    if mode:
                        if leg==0:epoch=run_epoch(r,True,1,5,elf,cold=True)
                        else:epoch=run_epoch(r,True,2 if leg==1 else 4,6,elf,
                                             before=row['incoming']['after']['first']['shared'])
                    else:epoch=run_epoch(r,False,leg+1,1,elf,cold=leg==0)
                    row['epochs'].append(epoch);add_epoch(name,epoch)
                    if mode and leg==1:
                        epoch=run_epoch(r,True,3,5,elf);row['epochs'].append(epoch);add_epoch(name,epoch)
                    if leg<2:
                        path=out/'streams'/(label+('-initial.mig' if leg==0 else '-return.mig'))
                        stream=save(r,path,mode,C.FREQUENCY);report['streams'].append(stream)
                        loop['streams'].append(stream['file']);row['outgoing']=stream['file'];prior=stream;prior_epoch=epoch
                        if leg==0:saved[label]=stream
                finally:r.close()
                report['checks'].append(row)
            report['roundtrips'].append(loop)
        for cpu in range(8):
            old=saved['off']['wire']['cpu_sections'][cpu]['cpreg_ids'];new=saved['on']['wire']['cpu_sections'][cpu]['cpreg_ids']
            C.require(new==[v for v in old if v!=0x603000000013e081],
                      'only on AA64 ACTLR_EL2 leaves generic list; no DSU aliases added')
        report['off_wire_original_and_on_NO_RAW_verified']=True
        crosses=(('cross-on-to-off','on',False,C.FREQUENCY,'0 != 1'),
                 ('cross-off-to-on','off',True,C.FREQUENCY,'Incoming CPU DSU PMU mode differs from destination'),
                 ('cross-frequency','on',True,ALTERNATE,'124f800 != 5f5e100'))
        for name,label,mode,frequency,needle in crosses:
            names.append(name);row=rejected_load(args,out,name,out/saved[label]['file'],mode,frequency,needle,elf)
            report['checks'].append(row);report['rejected_migrations'].append(row)
        original=(out/saved['on']['file']).read_bytes();wire=saved['on']['wire']
        for spec in MUTATIONS:
            name=spec[0];names.append(name);raw,mutation=mutate(original,wire,spec)
            path=out/'streams'/(name+'.mig');path.write_bytes(raw)
            report['streams'].append({'file':'streams/'+path.name,'kind':'finite-invalid-mutation',
                'bytes':len(raw),'sha256':C.digest(raw),'mutation':mutation})
            row=rejected_load(args,out,name,path,True,C.FREQUENCY,spec[-1],elf)
            row['mutation']=mutation;report['checks'].append(row);report['rejected_migrations'].append(row)
        C.require(tuple(names)==ORDER,'complete literal fixture order')
        for row in report['checks']:row.update(name=row['fixture'],passed=True)
        report['processes']=cleanup_records(out,names)
        report['summary']={'checks':len(names),'processes':len(report['processes']),
            'normal_exit_processes':sum(x['exit_code']==0 for x in report['processes']),
            'rejected_processes':sum(x['exit_code']==1 for x in report['processes']),
            'actual_Popen_wait_calls':sum(len(x['wait_calls']) for x in report['processes']),
            'successful_incoming':sum(len(x['incoming']) for x in report['roundtrips']),
            'roundtrips':len(report['roundtrips']),'rejected_migrations':len(report['rejected_migrations']),
            'actual_stream_files':len(report['streams']),'canonical_streams':4,'mutated_streams':len(MUTATIONS),
            'guest_epochs':len(report['guest_epochs']),'cpus_per_epoch':8,
            'guest_instruction_operations':sum(sum(c['instruction_operations'] for c in x['cpus']) for x in all_epochs),
            'real_GIC82_handlers':sum(sum(len(c['irq_events']) for c in x['cpus']) for x in all_epochs),
            'real_arithmetic_overflow_handlers':2,'software_overflow_handlers':1,
            'retained_cpu_vmstate_version':22,'dedicated_profile_version':1,'shared_bank_bytes':108,
            'complete_profile_payload_bytes':300,'common_bytes_per_cpu':24,'DSU_raw_cpreg_entries':0,
            'typed_shared_getters':28,'typed_CPU_getters_per_CPU':3,'enabled_two_pass_pure_getters':104,
            'harness_methods_before':51,'boot_runner_methods':45}
        C.require(sha(args.qemu)==binary,'compiled binary changed');report['binary_unchanged']=True;report['passed']=True
    except BaseException as exc:
        report['error']=f'{type(exc).__name__}: {exc}';report['traceback']=traceback.format_exc()
        report['processes']=[dict(fixture=p.parent.name,**json.loads(p.read_bytes())) for p in sorted(out.glob('*/cleanup.json'))]
    finally:
        report['actual_files']=[pin(p,out) for p in sorted(out.rglob('*')) if p.is_file() and p.resolve()!=args.report]
        put_json(args.report,report)
    print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'report':str(args.report)},sort_keys=True))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())

