#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DSU fixture: inherited owned-child transport, real TCG and RAM QTest."""
import json
from pathlib import Path
import subprocess
import tempfile

from qce_bam_test_transport import Rig, put_json, sha
from dsu_pmu_test_contract import (PATH, CPU_PROP, PROFILE_PROP, FREQUENCY_PROP,
    FREQUENCY, GETTERS, pure_cpu_expected, validate_shared_shape, require)


class DsuRig(Rig):
    def __init__(self, args, directory, *, mode=None, frequency=FREQUENCY,
                 incoming=False, accel='tcg', invalid_cpu=None, ras=False,
                 expected_configuration_failure=False):
        require(mode in (None,False,True) and accel in ('tcg','qtest'), 'fixture mode')
        require(invalid_cpu in (None,'cortex-a55','cortex-a77'), 'generic negative CPU')
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=False)
        self.sockets=Path(tempfile.mkdtemp(prefix='dsup-'))
        self.process=None;self.peers={};self.closed=False
        self.expected_failure=invalid_cpu is not None or expected_configuration_failure
        self.expected_exit=1 if self.expected_failure else 0
        self.request_id=0
        self.count=dict(qom_get=0,qom_set=0,qtest=0,memory=0,device_mmio=0,ram=0,cont=0)
        self.events,self.irq_events,self.waits=[],[],[]
        self.irq={50:False};self.intercepted=False
        self.logs={name:(self.directory/name).open('wb') for name in ('qemu.stdout','qemu.stderr')}
        self.protocol=(self.directory/'protocol.jsonl').open('w')
        machine=args.machine+',counter-exact=on'
        if mode is not None: machine+=',dsu-pmu='+('on' if mode else 'off')
        machine+=',dsu-pmu-frequency-hz='+str(frequency)
        if ras: machine+=',ras-zero-records-compat=on'
        if invalid_cpu: machine='none'
        command=[str(Path(args.qemu).resolve()),'-machine',machine,
            '-accel','tcg,thread=single' if accel=='tcg' else 'qtest',
            '-m','128M','-smp','1' if invalid_cpu else '8','-S',
            '-display','none','-monitor','none','-serial','null','-serial','null',
            '-d','guest_errors,invalid_mem',
            '-qmp',f'unix:{self.sockets}/m,server=on,wait=off',
            '-qtest',f'unix:{self.sockets}/t,server=on,wait=off',
            '-qtest-log',str(self.directory/'qtest-server.log')]
        if invalid_cpu:
            command+=['-cpu',invalid_cpu+','+CPU_PROP+'=on,has_el2=on,has_el3=off']
        else:
            command+=['-device','loader,file='+str(Path(args.probe).resolve())+',cpu-num=0']
        if incoming: command+=['-incoming','defer']
        self.command_line=command;put_json(self.directory/'command.json',command)
        try:
            self.process=subprocess.Popen(command,stdin=subprocess.DEVNULL,
                stdout=self.logs['qemu.stdout'],stderr=self.logs['qemu.stderr'])
            put_json(self.directory/'child.json',{'pid':self.process.pid,
                'created_by_this_fixture':True,'signals_to_preexisting_processes':False})
            if self.expected_failure:
                self.wait_exit(15);return
            self.peers['qmp']=self.connect('m','qmp')
            greeting=json.loads(self.peers['qmp'].receive());self.record({'qmp_receive':greeting})
            require('QMP' in greeting,'QMP greeting');self.monitor('qmp_capabilities')
            self.peers['qtest']=self.connect('t','qtest')
        except BaseException:
            self.close();raise

    def snapshot(self, mode):
        cpus=[]
        for cpu in range(8):
            path=f'/machine/cpu{cpu}'
            cpus.append({p:self.monitor('qom-get',{'path':path,'property':p})
                         for p in (CPU_PROP,PROFILE_PROP,FREQUENCY_PROP)})
        shared=({p:self.monitor('qom-get',{'path':PATH,'property':p}) for p in GETTERS}
                if mode else None)
        return {'cpus':cpus,'shared':shared}

    def pure_state(self, mode, frequency=FREQUENCY, *, cold=False):
        before=dict(self.count);first,second=self.snapshot(mode),self.snapshot(mode)
        require(first==second,'two complete pure snapshots differ')
        expected=pure_cpu_expected(mode,frequency)
        require(first['cpus']==[expected]*8,'all eight CPU profile and shared frequency')
        for row in first['cpus']+second['cpus']:
            require(type(row[CPU_PROP]) is bool and type(row[PROFILE_PROP]) is int and
                    type(row[FREQUENCY_PROP]) is int,'exact typed CPU getters')
        if mode: validate_shared_shape(first['shared'],frequency,cold=cold)
        else: require(first['shared'] is None,'disabled bank getter invented')
        require(self.count['qom_get']-before['qom_get']==(104 if mode else 48),'complete getter delta')
        require(all(self.count[k]==before[k] for k in ('qtest','memory','device_mmio','ram','cont','qom_set')),
                'pure observation performed mutation or guest access')
        return {'first':first,'second':second,'before':before,'after':dict(self.count)}

    def intercept(self):
        require(not self.intercepted,'one shared output interception per fixture')
        # Input observation preserves delivery to the GIC; output interception would disconnect it.
        self.qt('irq_intercept_in /machine/gic');self.intercepted=True

    def fence_irq(self):
        require(self.qt('endianness')==['little'],'IRQ fence response')
        return {'line':bool(self.irq[50]),'events':list(self.irq_events)}


def cleanup_records(out, names):
    paths={p.parent.name:p for p in Path(out).glob('*/cleanup.json')}
    require(set(paths)==set(names),'one complete cleanup per actual fixture')
    rows=[]
    for name in names:
        c=json.loads(paths[name].read_bytes())
        require(c['actually_waited_and_reaped'] and c['exit_code']==c['expected_exit_code'] and
                c['exit_code'] in (0,1),'actual wait/expected exit')
        require(not c['forced_terminate'] and not c['forced_kill'] and
                not c['signals_to_preexisting_processes'],'owned normal cleanup')
        require(len(c['wait_calls'])==(1 if c['exit_code']==0 else 2),'actual Popen.wait journal length')
        for w in c['wait_calls']:
            require(w['called']=='Popen.wait' and w['returned_exit_code']==c['exit_code'],'actual Popen.wait result')
        if c['exit_code']==0:
            require(c['quit_sent'] and c['quit_acknowledged'] and c['quit_error'] is None,'normal current quit ACK')
        else:
            require(not c['quit_sent'] and c['quit_not_applicable']=='already-exited','fatal process already exited')
            require(not any(e['action']=='raise' for e in c['irq_events']),'failed incoming never publishes IRQ, including drained tail')
        require(set(c['drains'])=={'qmp','qtest'} and not c['unexpected_qtest_drain_lines'],'both complete channel drains')
        unopened=name.startswith('invalid-')
        for label,d in c['drains'].items():
            require(d['channel']==label and bool(d.get('not_opened'))==unopened,'actual open channel identity')
            if unopened:continue
            require(d['error'] is None and d['terminal'] in ('eof','reset'),'bounded complete channel terminal')
            require(d['terminal']=='eof' or c['exit_code']==1,'normal shutdown uses EOF')
            raw=bytes.fromhex(d['drain_hex']);require(len(raw)==d['drain_bytes'],'full channel tail bytes')
            if label=='qtest':
                for line in raw.decode().splitlines():
                    if line:require(line in ('IRQ raise 50','IRQ lower 50'),'only complete observed GIC input50 tail lines')
            else:
                for line in raw.splitlines():
                    e=json.loads(line)
                    require('event' in e and e['event'] in ('SHUTDOWN','STOP','RESUME','MIGRATION'),
                            'QMP drained tail contains only known asynchronous events')
        require(c['counters']['device_mmio']==0,'host performs no QCE/device MMIO')
        for filename,pin in c['stdio'].items():
            p=paths[name].parent/filename
            require(pin['complete_after_actual_wait'] is True and p.stat().st_size==pin['bytes'] and
                    sha(p)==pin['sha256'],'whole stdio after real child wait')
            if c['exit_code']==0 or filename=='qemu.stdout':require(pin['bytes']==0,'unexpected normal/guest stdout')
        rows.append({'fixture':name,**c})
    return rows
