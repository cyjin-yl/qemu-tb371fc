#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only RAS acceptance: full guest bytes, ELF instructions and raw protocols.

No producer or model module is imported, and no process is started. RAS values,
permissions, CPU identities, exception syndromes and wire offsets are specified
independently. Unrelated VMState field sizes come from the complete QEMU stream
trailer, with a forward walk of framing/RAM/fields/footers to its exact end.
"""
import hashlib
import json
from pathlib import Path
import re
import struct

NAME = 'ras-zero-records-compat'
SCHEMA = 'tb371fc-ras-zero-records-compiled-v1'
CPU_PROP = 'tb371fc-ras-zero-records-compat'
PROFILE = 'tb371fc-ras-zero-records-profile'
SUB = 'cpu/tb371fc-ras-zero-records-compat'
CONTROL, BOX, STRIDE, BYTES = 0x80200000, 0x80210000, 0x8000, 16512
MASK, SENTINEL, NONCE = (1 << 64) - 1, 0x52a5c33cdeadbeef, 0x5241530000000000
ENCODINGS = (0xc298, 0xc299, 0xc2a0, 0xc2a1, 0xc2a2, 0xc2a3, 0xc2a8, 0xc2a9, 0xc2a4, 0xc2af)
REG_NAMES = ('erridr', 'errselr', 'erxfr', 'erxctlr', 'erxstatus', 'erxaddr', 'erxmisc0', 'erxmisc1', 'unknown0', 'unknown1')
KVM_IDS = tuple(0x6030000000130000 | x for x in ENCODINGS[1:8])
NORMAL = ('qtest-default-off', 'qtest-on', 'default-off', 'explicit-off', 'on-reset',
          'on-source', 'on-target', 'on-return', 'off-source', 'off-target', 'off-return')
INVALID = ('invalid-cortex-a55', 'invalid-cortex-a77')
MUTANTS = (('profile0-value0', 'profile-value', 0, 0), ('profile7-value2', 'profile-value', 7, 2),
           ('profile0-version0', 'profile-version', 0, 0), ('profile7-version0', 'profile-version', 7, 0),
           ('profile0-absent', 'profile-absent', 0, None), ('profile7-absent', 'profile-absent', 7, None)) + tuple(
               (f'cpreg-cpu{cpu}-{i}', 'cpreg-nonzero', cpu, i) for cpu in (0, 7) for i in range(7))
ORDER = NORMAL[:5] + INVALID + NORMAL[5:] + ('cross-on-to-off', 'cross-off-to-on') + tuple(x[0] for x in MUTANTS)
SUMMARY = {'checks':35,'processes':35,'normal_exit_processes':11,'rejected_processes':24,
           'actual_Popen_wait_calls':59,'successful_incoming':4,'roundtrips':2,'rejected_migrations':22,
           'actual_stream_files':24,'guest_epochs':10,'cpus_per_epoch':8,'operations_per_cpu_epoch':112,
           'guest_instruction_operations':8960,'whole_cpu_sections_per_saved_stream':8,
           'new_zero_const_cpregs_per_enabled_cpu':7,'retained_cpu_vmstate_version':22,
           'profile_subsection_version':1,'profile_payload_bytes':4}
EXPECTED_VALUES = {'schema': SCHEMA, 'summary': SUMMARY, 'binary_unchanged': True,
    'off_wire_has_no_RAS_subsection_or_new_cpregs': True, 'native_executed': False,
    'original_inputs_used': False, 'physical_RAS_state_verified': False,
    'error_injection_verified': False, 'Android_or_UI_verified': False}
EXPECTED_LENGTHS = {'checks':35,'processes':35,'roundtrips':2,'rejected_migrations':22,
                    'streams':24,'guest_epochs':10,'source_files':7}
COUNTERS = ('qom_get','qom_set','qtest','memory','device_mmio','ram','cont')


def need(ok, why):
    if not ok: raise ValueError('RAS contract: ' + why)


def equal(value, expected, why):
    need(type(value) is type(expected), why + ' type')
    if isinstance(expected, dict):
        need(value.keys() == expected.keys(), why + ' keys')
        for key in expected: equal(value[key], expected[key], why + '.' + key)
    elif isinstance(expected, (list, tuple)):
        need(len(value) == len(expected), why + ' length')
        for i, (a,b) in enumerate(zip(value, expected)): equal(a,b,f'{why}[{i}]')
    else: need(value == expected, why)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def read_file(root, relative, claimed=None):
    need(type(relative) is str and not Path(relative).is_absolute() and '..' not in Path(relative).parts, 'relative evidence path')
    path = root / relative
    need(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()), 'regular contained evidence file')
    raw = path.read_bytes()
    if claimed is not None:
        equal(claimed, {'file':relative, 'bytes':len(raw), 'sha256':sha(raw)}, 'full evidence file pin')
    return raw


def elf_probe(raw):
    need(raw[:7] == b'\x7fELF\x02\x01\x01', 'ELF64 little endian')
    h=struct.unpack_from('<16sHHIQQQIHHHHHH',raw)
    need(h[1:4] == (2,183,1) and (h[8],h[9],h[11]) == (64,56,64), 'ELF header')
    entry, phoff, shoff, phnum, shnum = h[4],h[5],h[6],h[10],h[12]
    need(0<phnum<32 and 0<shnum<128 and phoff+56*phnum<=len(raw) and shoff+64*shnum<=len(raw),'ELF table bounds')
    segments=[]
    for i in range(phnum):
        kind,flags,off,va,pa,filesz,memsz,alignment=struct.unpack_from('<IIQQQQQQ',raw,phoff+56*i)
        if kind != 1: continue
        need(va==pa and 0x80010000<=pa<pa+memsz<=CONTROL and filesz<=memsz and off+filesz<=len(raw),'ELF payload containment')
        segments.append({'address':pa,'offset':off,'file_bytes':filesz,'memory_bytes':memsz,'flags':flags})
    symbols={}
    for i in range(shnum):
        sh=struct.unpack_from('<IIQQQQIIQQ',raw,shoff+64*i)
        if sh[1]!=2:continue
        off,size,link,entsize=sh[4],sh[5],sh[6],sh[9]
        need(entsize==24 and size%24==0 and off+size<=len(raw) and link<shnum,'ELF symbol bounds')
        st=struct.unpack_from('<IIQQQQIIQQ',raw,shoff+64*link)
        strings=raw[st[4]:st[4]+st[5]];need(len(strings)==st[5],'ELF string bounds')
        for at in range(off,off+size,24):
            name,info,other,index,address,extent=struct.unpack_from('<IBBHQQ',raw,at)
            need(name<len(strings) and strings.find(b'\0',name)>=name,'ELF symbol name')
            key=strings[name:strings.find(b'\0',name)].decode('ascii')
            if key in ('_start','secondary_entry','enter_el1','enter_el0','el1_return_hvc','el0_return_svc','el2_return','el1_return') or key.startswith(('read_','write_')):
                need(key not in symbols and index!=0,'ELF unique defined symbol');symbols[key]=address
    need(entry==symbols.get('_start')==0x80010000,'ELF entry')
    instructions={}
    for reg,encoding in enumerate(ENCODINGS):
        for wr in (0,1):
            key=('write_' if wr else 'read_')+REG_NAMES[reg];pc=symbols.get(key)
            need(type(pc) is int and pc%4==0,'ELF operation symbol')
            match=[s for s in segments if s['address']<=pc and pc+8<=s['address']+s['file_bytes'] and s['flags']&1]
            need(len(match)==1,'ELF operation executable segment')
            off=match[0]['offset']+pc-match[0]['address']
            words=struct.unpack_from('<II',raw,off)
            need(words==((0xd5000000 if wr else 0xd5200000)|(encoding<<5),0xd65f03c0),'exact MRS/MSR plus RET')
            instructions[key]={'address':pc,'instruction_u32':words[0],'file_offset':off}
    return {'bytes':len(raw),'sha256':sha(raw),'entry':entry,'load_segments':segments,'symbols':symbols,'instructions':instructions}


def operation_plan():
    rows=[]
    def rd(p,e,r): rows.append((p,e,r,0,SENTINEL))
    def wr(p,e,r,v=MASK): rows.append((p,e,r,1,v))
    def matrix(p,e):
        for r in range(8):rd(p,e,r)
        for r in (1,3,4,5,6,7):wr(p,e,r);rd(p,e,r)
        for v in (0,1,65535):wr(p,e,1,v);rd(p,e,1)
        wr(p,e,3,0x10f);rd(p,e,3);wr(p,e,6,0x7f7f00000000);rd(p,e,6)
        wr(p,e,0);wr(p,e,2)
        for r in (8,9):rd(p,e,r);wr(p,e,r)
    matrix(1,1)
    for r in range(10):rd(2,0,r);wr(2,0,r)
    matrix(3,2)
    for r in range(8):rd(4,1,r)
    for r in (1,3,4,5,6,7,0,2):wr(4,1,r)
    for r in (8,9):rd(4,1,r);wr(4,1,r)
    need(len(rows)==112,'literal operation matrix')
    return rows


def guest_bytes(raw, cpu, mode, epoch, elf):
    need(len(raw)==BYTES,'complete 16512-byte guest record')
    hn=('magic','abi','cpu','mpidr','midr','pfr0','boot_el','epoch','count','fatal','active','done','nonce','hvc_returns','svc_returns','reserved')
    en=('seq','phase','el','reg','write','input','output','pc','traps','trap_el','esr','elr','spsr','far','complete','reserved')
    h=dict(zip(hn,struct.unpack_from('<16Q',raw)))
    fixed={'magic':0x525a524153505230,'abi':1,'cpu':cpu,'mpidr':0x80000000|(cpu<<8),
           'midr':0x412fd050 if cpu<4 else 0x411fd0d0,'boot_el':8,'epoch':epoch,'count':112,'fatal':0,
           'active':MASK,'done':epoch,'nonce':NONCE|epoch,'hvc_returns':2,'svc_returns':1,'reserved':0}
    for k,v in fixed.items():equal(h[k],v,'guest header '+k)
    need(((h['pfr0']>>28)&15)==1,'actual guest RAS field')
    events=[]
    for i,(phase,el,reg,wr,value) in enumerate(operation_plan()):
        e=dict(zip(en,struct.unpack_from('<16Q',raw,128+i*128)))
        pc=elf['instructions'][('write_' if wr else 'read_')+REG_NAMES[reg]]['address']
        for k,v in dict(seq=i,phase=phase,el=el,reg=reg,write=wr,input=value,pc=pc,complete=1,reserved=0).items():
            equal(e[k],v,'guest exact operation '+k)
        exists=reg==0 or (mode and reg in range(1,8))
        permitted=exists and el>=1 and (not wr or reg in (1,3,4,5,6,7))
        trap=not permitted or phase==4
        equal(e['output'],value if wr or trap else 0,'real returned value')
        if not trap:
            equal([e[k] for k in ('traps','trap_el','esr','elr','spsr','far')],[0]*6,'successful instruction did not trap')
        else:
            esr=0x02000000;target=1 if el==0 else el
            if permitted and phase==4:
                code=ENCODINGS[reg];op0=(code>>14)&3;op1=(code>>11)&7;crn=(code>>7)&15;crm=(code>>3)&15;op2=code&7
                esr=0x62000000|(op0<<20)|(op2<<17)|(op1<<14)|(crn<<10)|(crm<<1)|int(not wr)
                target=2
            equal([e[k] for k in ('traps','trap_el','esr','elr','far')],[1,target,esr,pc,0],'full exception syndrome/EL/PC')
            need(e['spsr']&0x3cf=={0:0x3c0,1:0x3c5,2:0x3c9}[el],'exception source EL/DAIF')
        events.append(e)
    need(raw[128+112*128:]==bytes(16*128),'unused event slots')
    return {'cpu':cpu,'header':h,'events':events,'bytes':len(raw),'sha256':sha(raw),
            'instruction_operations':112,'actual_exceptions':sum(x['traps'] for x in events)}


class Wire:
    """Separate acceptance implementation of the full v3 stream walk."""
    def __init__(self,raw):
        self.b=raw;self.p=0;self.ids={};self.ram={};self.ram_name=None;self.pages=0;self.cpu=[]
        candidates=[]
        at=raw.find(b'\x00\x06',max(8,len(raw)-16*1024*1024))
        while at!=-1:
            if at+6<=len(raw) and at+6+int.from_bytes(raw[at+2:at+6],'big')==len(raw):
                try:
                    d=json.loads(raw[at+6:])
                    if type(d) is dict and type(d.get('devices')) is list:candidates.append((at,d))
                except (ValueError,UnicodeDecodeError):pass
            at=raw.find(b'\x00\x06',at+1)
        need(len(candidates)==1,'one exact complete stream trailer')
        self.end,self.desc=candidates[0]
        equal(self.desc['page_size'],4096,'stream RAM page size')
        self.devices={}
        for d in self.desc['devices']:
            key=d['name'],d['instance_id'];need(key not in self.devices and type(d.get('fields')) is list,'stream device description')
            self.devices[key]=d

    def take(self,n):
        need(type(n) is int and 0<=n<=len(self.b)-self.p,'whole stream bounds')
        start=self.p;self.p+=n;return self.b[start:self.p]
    def u(self,n):return int.from_bytes(self.take(n),'big')
    def string(self):return self.take(self.u(1)).decode('ascii')

    def fields(self,description):
        start=self.p;rows=[];subs=[]
        for f in description['fields']:
            count=f.get('array_len',1);need(type(count)is int and 0<=count<1<<20,'VMState array bound')
            for i in range(count):
                off=self.p
                if f['type']=='struct':self.fields(f['struct'])
                elif f['type']=='capability':self.take(self.u(1))
                else:self.take(f['size'])
                rows.append((f['name'],off,self.p-off))
        for d in description.get('subsections',[]):
            off=self.p;equal(self.u(1),5,'subsection marker');name=self.string();version=self.u(4)
            equal(name,d['vmsd_name'],'subsection name');equal(version,d['version'],'subsection version')
            body,fs,ss=self.fields(d)
            subs.append({'name':name,'version':version,'start':off,'body_start':body,'end':self.p,
                         'fields':fs,'subsections':ss})
        return start,rows,subs

    def ram_body(self):
        while True:
            word=self.u(8);flags=word&4095;offset=word&~4095
            if flags&4:
                left=offset
                while left:
                    name=self.string();size=self.u(8)
                    need(name not in self.ram and size>0 and size<=left and size%4096==0,'RAM complete block declaration')
                    self.ram[name]=size;left-=size
                flags&=~4
            if flags&10:
                need(flags&10 in (2,8),'RAM compressed/page encoding')
                if flags&32:flags&=~32
                else:self.ram_name=self.string()
                need(self.ram_name in self.ram and offset+4096<=self.ram[self.ram_name],'RAM exact page bound')
                self.take(1 if flags&2 else 4096);self.pages+=1;flags&=~10
            if flags==0x200:continue
            if flags&16:
                equal(flags,16,'RAM terminal flags');break
            equal(flags,0,'unknown RAM flags')

    def cpu_body(self,instance,version,section,section_start,start,fields,subs):
        equal(version,22,'CPU unchanged version');need(instance in range(8),'CPU instance')
        n=int.from_bytes(self.b[start+568:start+572],'big',signed=True)
        need(0<n<65536,'CPU CPreg array count')
        def selected(name):return [x for x in fields if x[0]==name]
        equal(selected('cpreg_vmstate_array_len'),[('cpreg_vmstate_array_len',start+568,4)],'CPU count offset')
        equal(selected('cpreg_vmstate_indexes'),[('cpreg_vmstate_indexes',start+572+i*8,8) for i in range(n)],'all CPreg index fields')
        equal(selected('cpreg_vmstate_values'),[('cpreg_vmstate_values',start+572+(n+i)*8,8) for i in range(n)],'all CPreg value fields')
        need(fields[-1][1]+fields[-1][2]==start+637+n*16,'complete original CPU base')
        indexes=list(struct.unpack_from('>'+str(n)+'Q',self.b,start+572))
        values=list(struct.unpack_from('>'+str(n)+'Q',self.b,start+572+8*n))
        need(indexes==sorted(set(indexes)),'CPreg ordering and uniqueness')
        need(0x603000000013c298 in indexes and values[indexes.index(0x603000000013c298)]==0,'old ERRIDR zero')
        ras=[s for s in subs if s['name']==SUB];need(len(ras)<=1,'unique RAS subsection')
        profile=None
        if ras:
            sub=ras[0];equal(sub['version'],1,'RAS version1')
            equal(sub['fields'],[('tb371fc_ras_zero_records_profile',sub['body_start'],4)],'RAS exact single uint32 field')
            equal(sub['end']-sub['body_start'],4,'four-byte profile body')
            equal(sub['subsections'],[],'no hidden nested RAS fields')
            profile=int.from_bytes(self.b[sub['body_start']:sub['end']],'big');equal(profile,1,'canonical mode1')
        new=[]
        for index in KVM_IDS:
            if index in indexes:
                i=indexes.index(index);equal(values[i],0,'actual outgoing zero CONST')
                new.append({'id':index,'value':0,'value_offset':start+572+(n+i)*8})
        equal(len(new),7 if profile==1 else 0,'exact seven added CPregs only in on mode')
        self.cpu.append({'instance':instance,'version':version,'section_id':section,'section_start':section_start,
            'body_start':start,'body_end':self.p,'base_bytes':637+n*16,'cpreg_count':n,
            'cpreg_ids':indexes,'cpreg_values':values,'new_registers':new,'profile':profile,
            'subsections':[{k:s[k] for k in ('name','version','start','body_start','end')} for s in subs],
            'body_sha256':sha(self.b[start:self.p])})

    def parse(self,mode):
        equal(self.take(8),b'QEVM\0\0\0\x03','stream magic/version')
        pending=None
        while self.p<self.end:
            begin=self.p;tag=self.u(1)
            if tag==7:
                need(not self.ids,'configuration before devices')
                if 'configuration' in self.desc:self.fields(self.desc['configuration'])
                else:equal(self.take(self.u(4)),b'tb371fc','configuration board')
            elif tag in (1,4):
                need(pending is None,'mandatory previous section footer')
                sid=self.u(4);name=self.string();instance=self.u(4);version=self.u(4);key=(name,instance)
                need(sid not in self.ids,'unique section ID');self.ids[sid]=(key,version)
                if key==('ram',0):
                    equal((tag,version),(1,4),'RAM setup header');self.ram_body()
                else:
                    need(tag==4 and key in self.devices,'known complete VMState device')
                    d=self.devices[key];equal(version,d['version'],'VMState declared version')
                    start,fields,subs=self.fields(d)
                    if name=='cpu':self.cpu_body(instance,version,sid,begin,start,fields,subs)
                pending=sid
            elif tag in (2,3):
                need(pending is None,'iterative prior footer');sid=self.u(4)
                equal(self.ids.get(sid),(('ram',0),4),'only RAM iterative sections')
                self.ram_body();pending=sid
            elif tag==0x7e:
                need(pending is not None and self.u(4)==pending,'exact section footer');pending=None
            elif tag==8:
                equal((self.u(2),self.u(2)),(11,0),'fixed switchover command')
            else:need(False,'unknown stream marker')
        need(pending is None and self.p==self.end,'all sections have footer at exact EOF')
        equal(self.take(2),b'\x00\x06','EOF/trailer markers');size=self.u(4)
        equal(self.take(size),self.b[self.end+6:],'full VMDESCRIPTION');equal(self.p,len(self.b),'no trailing bytes')
        equal(sorted(c['instance'] for c in self.cpu),list(range(8)),'all eight CPU sections')
        self.cpu.sort(key=lambda c:c['instance'])
        need(all(c['profile']==(1 if mode else None) for c in self.cpu),'all eight saved modes')
        return {'bytes':len(self.b),'sha256':sha(self.b),'cpu_sections':self.cpu,'ram_blocks':self.ram,
                'ram_pages_parsed':self.pages,'complete_forward_parse':True,'all_bytes_consumed':True}


def mutation_bytes(original,wire,kind,cpu,value):
    row=wire['cpu_sections'][cpu];equal(row['instance'],cpu,'mutation exact CPU')
    if kind=='cpreg-nonzero':
        e=row['new_registers'][value];equal(e['id'],KVM_IDS[value],'mutation exact CPreg')
        offset,old,new=e['value_offset'],bytes(8),(1).to_bytes(8,'big')
    else:
        matches=[s for s in row['subsections'] if s['name']==SUB];equal(len(matches),1,'mutation profile anchor')
        s=matches[0]
        if kind=='profile-value':offset,old,new=s['body_start'],b'\0\0\0\1',value.to_bytes(4,'big')
        elif kind=='profile-version':offset,old,new=s['body_start']-4,b'\0\0\0\1',bytes(4)
        else:
            equal(kind,'profile-absent','structural mutation kind');offset,old,new=s['start'],original[s['start']:s['end']],b''
    equal(original[offset:offset+len(old)],old,'original exact mutation range')
    modified=original[:offset]+new+original[offset+len(old):]
    return modified,{'kind':kind,'cpu':cpu,'value':value,'offset':offset,'old_hex':old.hex(),'new_hex':new.hex(),
        'original_bytes':len(original),'original_sha256':sha(original),'bytes':len(modified),'sha256':sha(modified)}



def raw_protocol(directory, claimed, mode, invalid=False):
    rows=[json.loads(x) for x in (directory/'protocol.jsonl').read_bytes().splitlines()]
    need(rows and rows[-1]=={'cleanup':claimed},'final complete cleanup journal')
    calls=[];pending=None;greeting=0;next_id=1;waits=[];drains={};qt_sent=[];qt_reply=[];events=[]
    counts={k:0 for k in COUNTERS}
    for at,row in enumerate(rows):
        if 'qmp_send' in row:
            equal(set(row),{'qmp_send'},'QMP send row');need(pending is None,'serial protocol request')
            req=row['qmp_send'];equal(req.get('id'),next_id,'monotonic QMP id');next_id+=1
            before=dict(counts)
            key={'qom-get':'qom_get','qom-set':'qom_set','cont':'cont'}.get(req['execute'])
            if key:counts[key]+=1
            pending={'kind':'qmp','request':req,'index':at,'before':before,'after':dict(counts)}
        elif 'qmp_receive' in row:
            equal(set(row),{'qmp_receive'},'QMP receive row');v=row['qmp_receive']
            if 'QMP' in v:
                need(greeting==0 and not calls and pending is None,'one initial QMP greeting');greeting+=1
            elif 'event' in v:events.append((at,v))
            else:
                need(pending is not None and pending['kind']=='qmp' and v.get('id')==pending['request']['id'],'exact QMP response association')
                pending['reply']=v;calls.append(pending);pending=None
        elif 'qtest_send' in row:
            equal(set(row),{'qtest_send'},'QTest send row');need(pending is None,'serial QTest request')
            command=row['qtest_send'];fields=command.split();need(fields[0] in ('read','write'),'only explicit RAM buffer QTest')
            addr,size=int(fields[1],0),int(fields[2],0)
            need(0x80000000<=addr<addr+size<=0x88000000,'host test RAM range')
            before=dict(counts)
            for k in ('qtest','memory','ram'):counts[k]+=1
            pending={'kind':'qtest','request':command,'index':at,'before':before,'after':dict(counts)};qt_sent.append(command)
        elif 'qtest_receive' in row:
            equal(set(row),{'qtest_receive'},'QTest receive row');v=row['qtest_receive'];qt_reply.append(v)
            need(pending is not None and pending['kind']=='qtest' and v.startswith('OK'),'QTest complete successful reply')
            pending['reply']=v;calls.append(pending);pending=None
        elif 'actual_wait' in row:
            equal(set(row),{'actual_wait'},'actual wait row');waits.append(row['actual_wait'])
        elif 'drain' in row:
            equal(set(row),{'drain'},'drain row');d=row['drain'];label=d['channel'];need(label not in drains,'one actual drain per channel');drains[label]=d
        elif 'channel' in row:
            need(set(row)=={'channel','terminal'} and row['channel'] in ('qmp','qtest') and row['terminal'] in ('eof','reset'),'exact transport terminal row')
        elif 'cleanup' in row:
            need(at==len(rows)-1,'cleanup once and last')
        else:need(False,'unknown raw transport record')
    need(pending is None,'all submitted commands must have a complete response')
    equal(greeting,0 if invalid else 1,'actual QMP connected state')
    equal(counts,claimed['counters'],'all raw counters');equal(waits,claimed['wait_calls'],'actual waits equal sidecar')
    equal(set(drains),set() if invalid else {'qmp','qtest'},'all opened channels were drained')
    for channel,row in drains.items():equal(row,claimed['drains'][channel],'raw drain and sidecar exact')
    if invalid:
        equal(calls,[],'failed realization ran no QMP/QTest')
        if (directory/'qtest-server.log').exists():equal((directory/'qtest-server.log').read_bytes(),b'','unconnected server log')
    else:
        sends=[];replies=[];controls=[]
        for text in (directory/'qtest-server.log').read_text().splitlines():
            match=re.fullmatch(r'\[([RSI]) \+?[0-9]+\.[0-9]+\] ?(.*)',text)
            need(match is not None,'complete QTest server line')
            role,body=match.groups();(sends if role=='R' else replies if role=='S' else controls).append(body)
        equal(sends,qt_sent,'every QTest server request');equal(replies,qt_reply,'every QTest server reply')
        need(controls in (['OPENED'],['OPENED','CLOSED']),'one actual QTest connection')
        equal(drains['qtest']['total_received_bytes'],sum(len(x.encode())+1 for x in replies),'all QTest received bytes')
    return calls,events


def validate_cleanup(directory,name,c):
    invalid=name in INVALID;normal=name in NORMAL
    equal(c['expected_exit_code'],0 if normal else 1,'expected process exit')
    equal(c['exit_code'],c['expected_exit_code'],'actual process exit')
    equal(c['actually_waited_and_reaped'],True,'real wait/reap')
    for k in ('forced_terminate','forced_kill','signals_to_preexisting_processes'):equal(c[k],False,k)
    need(type(c['pid'])is int and c['pid']>0,'owned child PID')
    child=json.loads((directory/'child.json').read_bytes())
    equal(child,{'pid':c['pid'],'created_by_this_fixture':True,'signals_to_preexisting_processes':False},'actual child record')
    equal(len(c['wait_calls']),1 if normal else 2,'complete wait count')
    for w in c['wait_calls']:
        equal(set(w),{'called','returned_exit_code','timeout_seconds'},'wait exact fields')
        equal(w['called'],'Popen.wait','actual wait API');equal(w['returned_exit_code'],c['exit_code'],'wait actual status')
        need(type(w['timeout_seconds'])is int and w['timeout_seconds']>0,'wait deadline')
    equal(c['quit_sent'],normal,'normal-only quit');equal(c['quit_acknowledged'],normal,'actual quit ACK')
    equal(c['quit_error'],None,'quit error');equal(c['quit_not_applicable'],None if normal else 'already-exited','quit applicability')
    equal(c['irq_events'],[],'no GPIO interception or fabricated IRQ');equal(c['unexpected_qtest_drain_lines'],[],'no extra QTest data')
    equal(set(c['drains']),{'qmp','qtest'},'fixed cleanup channels')
    for label,d in c['drains'].items():
        equal(d['channel'],label,'drain channel')
        if invalid:
            equal(d,{'channel':label,'not_opened':True,'terminal':None,'drain_bytes':0,
                     'reason':'not-connected-before-child-exit-or-tcg'},'unopened channel exact evidence')
            continue
        need('not_opened' not in d,'actual connected channel')
        equal(d['error'],None,'drain error');need(d['terminal']=='eof' or (not normal and d['terminal']=='reset'),'terminal socket semantics')
        raw=bytes.fromhex(d['drain_hex']);equal(len(raw),d['drain_bytes'],'drain bytes')
        if label=='qtest':equal(raw,b'','empty QTest tail')
        else:
            for line in raw.splitlines():
                event=json.loads(line);need(event.get('event') in ('SHUTDOWN','STOP','RESUME','MIGRATION') and 'id' not in event,'only complete asynchronous QMP tail events')
    equal(set(c['counters']),set(COUNTERS),'all actual counters');equal(c['counters']['device_mmio'],0,'host no device MMIO')
    equal(set(c['stdio']),{'qemu.stdout','qemu.stderr'},'both complete stdio files')
    for name_,pin in c['stdio'].items():
        raw=(directory/name_).read_bytes()
        equal(pin,{'bytes':len(raw),'sha256':sha(raw),'complete_after_actual_wait':True},'complete post-wait stdio')
        if normal or name_=='qemu.stdout':equal(raw,b'','empty guest output/normal diagnostics')


class Flow:
    def __init__(self,calls,events,root,name,mode,elf):
        self.calls,self.events,self.root,self.name,self.mode,self.elf=calls,events,root,name,mode,elf
        self.i=0;self.directory=root/name
    def next(self,kind):
        need(self.i<len(self.calls),'missing actual operation in '+self.name)
        c=self.calls[self.i];self.i+=1;equal(c['kind'],kind,'actual operation kind');return c
    def q(self,execute,args=None,error=False):
        c=self.next('qmp');req={'execute':execute,'id':c['request']['id']}
        if args is not None:req['arguments']=args
        equal(c['request'],req,'literal QMP command')
        if error:
            need(c['reply'].get('error',{}).get('class')=='GenericError' and type(c['reply']['error'].get('desc'))is str,'actual GenericError rejection')
            return c['reply']
        need('error' not in c['reply'] and 'return'in c['reply'],'QMP successful response')
        return c['reply']['return']
    def qt(self,address,size,data=None):
        c=self.next('qtest')
        command=(f'read {address:#x} {size:#x}' if data is None else f'write {address:#x} {size:#x} 0x{data.hex()}')
        equal(c['request'],command,'literal QTest RAM operation')
        words=c['reply'].split()
        if data is not None:equal(words,['OK'],'QTest write reply');return None
        need(len(words)==2 and words[0]=='OK' and words[1].startswith('0x'),'QTest read reply encoding')
        raw=bytes.fromhex(words[1][2:]);equal(len(raw),size,'complete actual QTest read bytes');return raw
    def stopped(self):
        v=self.q('query-status');equal(v['running'],False,'actual stopped state');return v
    def profiles(self,claimed):
        before=self.calls[self.i]['before'];states=[]
        for repeat in range(2):
            part=[]
            for cpu in range(8):
                row={}
                for prop,wanted in ((CPU_PROP,self.mode),(PROFILE,int(self.mode))):
                    v=self.q('qom-get',{'path':f'/machine/cpu{cpu}','property':prop});equal(v,wanted,'typed actual CPU profile')
                    row[prop]=v
                part.append(row)
            states.append(part)
        after=self.calls[self.i-1]['after']
        equal(claimed,{'first':states[0],'second':states[1],'before':before,'after':after},'two complete pure QOM snapshots')
        equal(after['qom_get']-before['qom_get'],32,'32 pure gets')
        for key in COUNTERS:
            if key!='qom_get':equal(after[key],before[key],'pure observation '+key)
    def snapshot(self,claim,epoch,label):
        self.stopped();control=self.qt(CONTROL,80)
        equal(struct.unpack('<10Q',control),(epoch,NONCE|epoch)+(0,)*8,'actual CPU_ON/control RAM')
        cp=claim['control'];equal(cp,{'file':label+'-control.bin','bytes':80,'sha256':sha(control)},'control file pin')
        equal((self.directory/cp['file']).read_bytes(),control,'full control file matches protocol')
        cpus=[];files=[]
        for cpu in range(8):
            raw=self.qt(BOX+cpu*STRIDE,BYTES);file=f'{label}-cpu{cpu}.bin'
            equal((self.directory/file).read_bytes(),raw,'full guest mailbox matches actual RAM reply')
            cpus.append(guest_bytes(raw,cpu,self.mode,epoch,self.elf));files.append({'file':file,'bytes':BYTES,'sha256':sha(raw)})
        expected={'label':label,'mode':self.mode,'epoch':epoch,'control':cp,'files':files,'cpus':cpus}
        equal({k:v for k,v in claim.items() if k!='polls'},expected,'full report to guest-byte association')
    def zero(self):
        self.qt(CONTROL,80,bytes(80))
        for cpu in range(8):self.qt(BOX+cpu*STRIDE,BYTES,bytes(BYTES))
    def epoch(self,claim,epoch,cold):
        self.stopped()
        if cold:self.zero()
        self.qt(CONTROL,16,struct.pack('<2Q',epoch,NONCE|epoch))
        equal(self.q('cont'),{},'actual guest continue ACK')
        polls=[]
        while True:
            batch=[]
            for cpu in range(8):
                words=struct.unpack('<16Q',self.qt(BOX+cpu*STRIDE,128))
                equal(words[9],0,'guest fatal flag during every poll')
                batch.append({'cpu':cpu,'done':words[11],'fatal':words[9],'count':words[8]})
            polls.append(batch)
            if all(x['done']==epoch for x in batch):break
            need(len(polls)<30000,'bounded actual polling')
        equal(claim['polls'],polls,'every actual header poll')
        equal(self.q('stop'),{},'actual guest stop ACK');self.stopped()
        self.snapshot(claim,epoch,f'epoch-{epoch}')
    def migration(self,incoming,path,claim):
        arguments={'channels':[{'channel-type':'main','addr':{'transport':'file','filename':str(path.resolve()),'offset':0}}]}
        equal(self.q('migrate-incoming' if incoming else 'migrate',arguments),{},'migration request ACK')
        while True:
            v=self.q('query-migrate')
            need(v.get('status') not in ('failed','cancelled'),'actual migration progress')
            if v.get('status')=='completed':break
        equal(v,claim,'actual completed migration response')
    def finish(self,normal):
        if normal:equal(self.q('quit'),{},'actual normal quit ACK')
        equal(self.i,len(self.calls),'all actual operations consumed')



def command_identity(directory,name,qemu,root):
    command=json.loads((directory/'command.json').read_bytes())
    need(type(command)is list and all(type(x)is str for x in command),'complete argv')
    mode=(name not in ('qtest-default-off','default-off','explicit-off','off-source','off-target','off-return','cross-on-to-off'))
    accel='qtest' if name.startswith('qtest-') else 'tcg,thread=single'
    machine='tb371fc' if name in ('qtest-default-off','default-off') else 'tb371fc,ras-zero-records-compat='+('on' if mode else 'off')
    invalid=name in INVALID
    if invalid:machine='none'
    need('-qmp'in command and '-qtest'in command,'two actual monitor transports')
    endpoint=command[command.index('-qmp')+1]
    match=re.fullmatch(r'unix:(.+)/m,server=on,wait=off',endpoint);need(match is not None,'QMP private socket syntax')
    expected=[qemu,'-machine',machine,'-accel',accel,'-m','128M','-smp','1' if invalid else '8','-S',
        '-display','none','-monitor','none','-serial','null','-serial','null','-d','guest_errors,invalid_mem',
        '-qmp',endpoint,'-qtest',f'unix:{match.group(1)}/t,server=on,wait=off',
        '-qtest-log',str(directory/'qtest-server.log')]
    if invalid:expected+=['-cpu',name.removeprefix('invalid-')+','+CPU_PROP+'=on,has_el2=on,has_el3=off']
    else:expected+=['-device','loader,file='+str(root/'build/probe.elf')+',cpu-num=0']
    incoming=name.endswith(('-target','-return')) or name not in NORMAL+INVALID
    if incoming:expected+=['-incoming','defer']
    equal(command,expected,'complete literal QEMU argv and generated guest ELF')
    return mode,incoming


def compile_evidence(root,repo,elf):
    command=json.loads((root/'build/command.json').read_bytes())
    expected=['aarch64-linux-gnu-gcc','-O2','-Wall','-Wextra','-Werror','-ffreestanding','-fno-builtin',
        '-fno-stack-protector','-fno-pie','-mstrict-align','-mgeneral-regs-only','-march=armv8.2-a',
        '-nostdlib','-static','-Wl,--build-id=none','-T',str(repo/'tb371fc/tests/ras_zero_records/link.ld'),
        str(repo/'tb371fc/tests/ras_zero_records/start.S'),str(repo/'tb371fc/tests/ras_zero_records/probe.c'),
        '-o',str(root/'build/probe.elf')]
    equal(command,expected,'fixed freestanding general-register compiler argv')
    equal(json.loads((root/'build/execution.json').read_bytes()),
        {'returncode':0,'timed_out':False,'actually_waited_and_reaped':True,'passed':True},'actual compiler wait/status')
    equal((root/'build/stdout.log').read_bytes(),b'','compiler stdout')
    equal((root/'build/stderr.log').read_bytes(),b'','compiler stderr with -Werror')
    need(elf['instructions'] and len(elf['instructions'])==20,'all20 ELF operation instruction witnesses')


def validate_qom_access(flow,claim):
    types=[];rejected=[]
    for cpu in range(8):
        path=f'/machine/cpu{cpu}';props=flow.q('qom-list',{'path':path})
        need(type(props)is list and len({x['name'] for x in props})==len(props),'complete unique QOM property list')
        mapping={x['name']:x['type'] for x in props}
        equal([mapping.get(CPU_PROP),mapping.get(PROFILE)],['bool','uint32'],'exact property metadata')
        types.append({'path':path,'types':{CPU_PROP:'bool',PROFILE:'uint32'}})
        for prop,value in ((CPU_PROP,not flow.mode),(PROFILE,int(not flow.mode))):
            args={'path':path,'property':prop,'value':value}
            response=flow.q('qom-set',args,error=True);rejected.append({'arguments':args,'response':response})
    args={'path':'/machine','property':'ras-zero-records-compat','value':not flow.mode}
    response=flow.q('qom-set',args,error=True);rejected.append({'arguments':args,'response':response})
    equal(claim['types'],types,'eight complete metadata observations');equal(claim['rejected'],rejected,'17 actual property rejections')
    flow.profiles(claim['unchanged']);equal(set(claim),{'types','rejected','unchanged'},'QOM check shape')


def validate_rejection(flow,row,path,needle):
    equal(row['mode'],flow.mode,'rejected target mode');equal(row['expected_exit_code'],1,'load expected exit')
    flow.profiles(row['initial_profiles'])
    args={'channels':[{'channel-type':'main','addr':{'transport':'file','filename':str(path.resolve()),'offset':0}}]}
    equal(flow.q('migrate-incoming',args),{},'rejected incoming request ACK')
    equal(row['incoming_file'],'streams/'+path.name,'rejected complete stream source')
    equal(row['diagnostic_needle'],needle,'correct failure branch diagnostic')
    stderr=(flow.directory/'qemu.stderr').read_text()
    need(needle in stderr and 'load of migration failed' in stderr,'actual load failure diagnostic')
    equal(row['actual_stderr_contains_needle'],True,'diagnostic observation');equal(row['actual_exit_code'],1,'actual fatal exit')
    flow.finish(False)


def judge_ras_zero_records_details(case,data):
    if case.name!=NAME:return
    for key,wanted in EXPECTED_VALUES.items():equal(data.get(key),wanted,key)
    equal(data.get('passed'),True,'actual gate passed')
    for key,size in EXPECTED_LENGTHS.items():need(type(data.get(key))is list and len(data[key])==size,'complete '+key)
    equal([x['fixture'] for x in data['checks']],list(ORDER),'ordered35 actual fixtures')
    for row in data['checks']:
        equal(row.get('name'),row['fixture'],'harness check identity');equal(row.get('passed'),True,'individual actual check verdict')
    need('error' not in data and 'traceback' not in data,'successful report has no retained failure field')
    equal([x['fixture'] for x in data['processes']],list(ORDER),'ordered35 cleanup records')
    root=Path(case.result).resolve().parent
    repo=Path(__file__).resolve().parents[2]
    qemu=str(Path(case.command[case.command.index('--qemu')+1]).resolve())
    equal(sha(Path(qemu).read_bytes()),data['qemu_sha256'],'actual compiled QEMU full hash')
    elf=elf_probe(read_file(root,'build/probe.elf'));equal(data['probe_elf'],elf,'full ELF metadata and every tested instruction')
    compile_evidence(root,repo,elf)
    source_names=['tb371fc/tests/ras_zero_records/'+n for n in ('start.S','probe.c','link.ld')]+[
        'tb371fc/tests/'+n for n in ('ras_zero_records_test_contract.py','ras_zero_records_test_transport.py','test_ras_zero_records.py','qce_bam_test_transport.py')]
    sources=[]
    for name in source_names:
        raw=(repo/name).read_bytes();sources.append({'relative_path':name,'bytes':len(raw),'sha256':sha(raw)})
    equal(data['source_files'],sources,'seven complete actual source pins')
    inventory={}
    for row in data['actual_files']:
        file=row['file'];need(file not in inventory,'unique full-file pin');read_file(root,file,row);inventory[file]=row
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.resolve()!=Path(case.result).resolve()}
    equal(set(inventory),actual,'all actual full artifacts present and pinned')
    equal({p.name for p in root.iterdir() if p.is_dir()},set(ORDER)|{'build','streams'},'complete fixture directories')
    checks={x['fixture']:x for x in data['checks']};cleanups={x['fixture']:x for x in data['processes']}
    flows={};all_calls=[]
    for name in ORDER:
        directory=root/name;c=json.loads((directory/'cleanup.json').read_bytes())
        equal(cleanups[name],{'fixture':name,**c},'entire cleanup file/report')
        validate_cleanup(directory,name,c)
        mode,incoming=command_identity(directory,name,qemu,root)
        calls,events=raw_protocol(directory,c,mode,name in INVALID);all_calls+=calls
        flow=Flow(calls,events,root,name,mode,elf);flows[name]=flow
        if name not in INVALID:equal(flow.q('qmp_capabilities'),{},'QMP negotiation first')
    equal(sum(len(c['wait_calls']) for c in cleanups.values()),59,'all59 actual waits')
    epochs=[]
    for name in NORMAL[:5]:
        f=flows[name];row=checks[name]
        equal(row['mode'],f.mode,'ordinary mode');equal(row['accel'],'qtest' if name.startswith('qtest-') else 'tcg','actual accelerator')
        f.profiles(row['initial_profiles'])
        if name.startswith('qtest-'):
            equal(row['epochs'],[],'qtest no guest epoch');validate_qom_access(f,row['qom_access'])
            need(cleanups[name]['counters']['cont']==cleanups[name]['counters']['ram']==0,'pure qtest observation')
        else:
            equal(len(row['epochs']),2 if name=='on-reset' else 1,'ordinary epochs')
            f.epoch(row['epochs'][0],1,True);epochs.append({'fixture':name,'epoch':1})
            if name=='on-reset':
                f.zero();at=f.calls[f.i]['index'];equal(f.q('system_reset'),{},'reset ACK');f.stopped()
                need(any(index>at and event.get('event')=='RESET' for index,event in f.events),'actual RESET event follows request')
                equal(row['reset_event_seen'],True,'reset report');f.profiles(row['reset_profiles'])
                f.epoch(row['epochs'][1],2,False);epochs.append({'fixture':name,'epoch':2})
        f.finish(True)
    for name in INVALID:
        row=checks[name];f=flows[name]
        equal(row['actual_exit_code'],1,'invalid configuration actual exit')
        needle='tb371fc-ras-zero-records-compat requires the TB371FC A55/A77 RASv1 software-emulated profile'
        equal(row['diagnostic_needle'],needle,'configuration failure diagnostic')
        need(needle in (f.directory/'qemu.stderr').read_text(),'actual generic-machine opt-in rejection')
        f.finish(False)
    streams={x['file']:x for x in data['streams']};equal(len(streams),24,'24 unique whole streams')
    equal({p.name for p in (root/'streams').iterdir()},{Path(k).name for k in streams},'complete stream file set')
    saved={};loops=[]
    for mode,label in ((True,'on'),(False,'off')):
        loop={'mode':mode,'fixtures':[],'streams':[],'incoming':[]}
        for leg,suffix in enumerate(('-source','-target','-return')):
            name=label+suffix;f=flows[name];row=checks[name];loop['fixtures'].append(name)
            equal((row['mode'],row['leg']),(mode,leg),'roundtrip leg identity')
            if leg==0:f.profiles(row['initial_profiles'])
            else:
                inc=row['incoming'];f.profiles(inc['before'])
                file=label+('-initial.mig' if leg==1 else '-return.mig')
                f.migration(True,root/'streams'/file,inc['migration'])
                f.profiles(inc['after']);f.stopped()
                equal(inc['cont_before_complete'],0,'no target guest before migration complete')
                equal(inc['ram_before_postload_profiles'],0,'first postload observation pure QOM')
                need(f.calls[f.i-1]['after']['cont']==f.calls[f.i-1]['after']['ram']==0,'raw no target execution/RAM before pure getters')
                f.snapshot(row['restored_guest'],leg,'restored')
                loop['incoming'].append({'fixture':name,**inc})
            equal(len(row['epochs']),1,'one fresh target epoch');f.epoch(row['epochs'][0],leg+1,leg==0)
            epochs.append({'fixture':name,'epoch':leg+1})
            if leg<2:
                file='streams/'+label+('-initial.mig' if leg==0 else '-return.mig');record=streams[file]
                equal(row['outgoing'],file,'outgoing stream identity');f.stopped();f.migration(False,root/file,record['migration'])
                raw=read_file(root,file);wire=Wire(raw).parse(mode);equal(record['wire'],wire,'complete independently decoded CPU wire')
                equal(set(record),{'file','migration','wire'},'saved stream shape');loop['streams'].append(file)
                if leg==0:saved[label]=(raw,wire)
            f.finish(True)
        loops.append(loop)
    equal(data['roundtrips'],loops,'two complete roundtrip records');equal(data['guest_epochs'],epochs,'ten independently observed fresh epochs')
    for cpu in range(8):
        old=saved['off'][1]['cpu_sections'][cpu]['cpreg_ids'];new=saved['on'][1]['cpu_sections'][cpu]['cpreg_ids']
        equal(new,sorted(old+list(KVM_IDS)),'only seven CPreg additions to default-compatible wire')
    rejected=[]
    for src,target in (('on','off'),('off','on')):
        name='cross-'+src+'-to-'+target;row=checks[name];f=flows[name]
        needle='Incoming CPU RAS zero-records mode differs from destination' if target=='on' else '0 != 1'
        validate_rejection(f,row,root/'streams'/(src+'-initial.mig'),needle);rejected.append(row)
    original,wire=saved['on']
    for name,kind,cpu,value in MUTANTS:
        raw,mutation=mutation_bytes(original,wire,kind,cpu,value)
        record=streams['streams/'+name+'.mig']
        equal(read_file(root,record['file']),raw,'whole mutant and all unmodified bytes')
        equal(record,{'file':'streams/'+name+'.mig','bytes':len(raw),'sha256':sha(raw),'mutation':mutation},'independent mutation offsets and full digests')
        row=checks[name];equal(row['mutation'],mutation,'mutation/rejection association')
        needle=('Incoming CPU RAS zero-records mode differs from destination' if kind=='profile-absent' else
                SUB if kind=='profile-version' else f'1 != {value:x}' if kind=='profile-value' else 'post load hook failed for: cpu, version_id: 22')
        validate_rejection(flows[name],row,root/record['file'],needle);rejected.append(row)
    equal(data['rejected_migrations'],rejected,'all22 exact failed loads')
    need(all(f.i==len(f.calls) for f in flows.values()),'all full raw sequences exhausted')
    qmp=[c['request']['execute'] for c in all_calls if c['kind']=='qmp']
    equal(qmp.count('cont'),10,'ten actual fresh guest execution commands')
    equal(qmp.count('migrate'),4,'four actual saves');equal(qmp.count('migrate-incoming'),26,'four successful and22 rejected incoming calls')
    equal(qmp.count('system_reset'),1,'one actual reset')
