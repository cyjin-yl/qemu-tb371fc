#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Standalone DSU raw-evidence judge. No producer/model imports or execution.

The source-reviewed pure architectural ABI arithmetic below is embedded in
full and has the same approved literal provenance as the producer contract.
The I/O consumer independently walks every full QMP/QTest/server record,
ELF instruction, eight-CPU mailbox and complete migration file. Neither
report semantic summaries nor producer success booleans supply an oracle.
"""
import ast
from pathlib import Path
import re
import hashlib
import json
import struct

NAME = 'dsu-pmu-reference'
SCHEMA = 'tb371fc-dsu-pmu-compiled-v1'
PATH = '/machine/dsu-pmu-device'
CPU_PROP = 'tb371fc-dsu-pmu'
PROFILE_PROP = 'tb371fc-dsu-pmu-profile'
FREQUENCY_PROP = 'tb371fc-dsu-pmu-frequency-hz'
SUBSECTION = 'cpu/tb371fc-dsu-pmu'
FREQUENCY = 100_000_000
CONTROL, CONTROL_BYTES = 0x80200000, 128
BOX_BASE, BOX_STRIDE, BOX_BYTES = 0x80210000, 0x10000, 53312
MAGIC, SENTINEL, U64 = 0x445355504d555030, 0xd5a5c33cdeadbeef, (1 << 64) - 1
NONCE = 0x4453550000000000
HCR_RW, HCR_TIDCP, ACTLR_WRITE = 1 << 31, 1 << 20, 1 << 12
PMCR_FIXED, ENABLE_MASK, TYPE_MASK = 0x41413040, 0x8000003f, 0xa000ffff
REGISTERS = (
    ('pmcr', 3, 0, 15, 5, 0), ('cntenset', 3, 0, 15, 5, 1),
    ('cntenclr', 3, 0, 15, 5, 2), ('ovsset', 3, 0, 15, 5, 3),
    ('ovsclr', 3, 0, 15, 5, 4), ('selr', 3, 0, 15, 5, 5),
    ('intenset', 3, 0, 15, 5, 6), ('intenclr', 3, 0, 15, 5, 7),
    ('ccntr', 3, 0, 15, 6, 0), ('xevtyper', 3, 0, 15, 6, 1),
    ('xevcntr', 3, 0, 15, 6, 2), ('ceid0', 3, 0, 15, 6, 4),
    ('ceid1', 3, 0, 15, 6, 5), ('hole0', 3, 0, 15, 6, 3),
    ('hole1', 3, 0, 15, 6, 7),
)
AUXILIARY_INSTRUCTIONS = (
    ('read_reference_counter', False, (3,3,14,0,1)),
    ('read_reference_frequency', False, (3,3,14,0,0)),
    ('read_actlr', False, (3,4,1,0,1)), ('write_actlr', True, (3,4,1,0,1)),
    ('read_hcr', False, (3,4,1,1,0)), ('write_hcr', True, (3,4,1,1,0)),
    ('read_irq_ack', False, (3,0,12,12,0)), ('write_irq_eoi', True, (3,0,12,12,1)),
)
UINT_GETTERS = ('enabled-profile','reference-identity','cpu-mask','interrupt-intid',
    'event-counter-count','virtual-reference-frequency-hz','pmcr','counter-enable',
    'overflow-status','interrupt-enable','selected-counter','cycle-counter',
    'fractional-phase','last-sync-ns','shared-wire-bytes','ceid0',
    'incoming-started-cpus','incoming-completed-cpus','timer-deadline-ns')
BOOL_GETTERS = ('irq-level','migration-quarantined','timer-pending','virtual-cycle-model',
    'physical-state-captured','cache-events-modeled','bus-events-modeled')
HEX_GETTERS = ('event-counters-hex','event-types-hex')
GETTERS = UINT_GETTERS + BOOL_GETTERS + HEX_GETTERS
HEADER = ('magic','abi','cpu','mpidr','midr','pfr0','boot_el','epoch',
    'recipe','count','fatal','active','done','nonce','hvc_returns','svc_returns',
    'irq_count','cntfrq','actlr_initial','actlr_final','hcr_final','mmio_count','delay_count','reserved0',
    'phase_hcr','phase_actlr','irq_goal','reserved1','reserved2','reserved3','reserved4','reserved5')
EVENT = ('seq','phase','el','reg','write','input','output','pc','traps','trap_el','esr','elr',
    'spsr','far','complete','reserved','time_before','time_after','counter_frequency','hcr','actlr',
    'reserved1','reserved2','reserved3')
IRQ_EVENT = ('seq','iar','overflow_before','overflow_after','counter_enable','interrupt_enable',
    'time_before','time_after','elr','spsr','level','eoir')
MMIO_EVENT = ('seq','address','width','write','input','output','complete','reserved')
DELAY_EVENT = ('seq','phase','start','end','requested_ticks')


def require(ok, why):
    if not ok:
        raise ValueError(why)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoding(fields):
    op0, op1, crn, crm, op2 = fields
    return (op0 << 14) | (op1 << 11) | (crn << 7) | (crm << 3) | op2


def access_operations():
    rows = []
    def matrix(phase, el, after):
        for reg in range(13):
            rows.extend([(phase, el, reg, 0, SENTINEL), (phase, el, reg, 1, 0)])
            if after:
                rows.append((phase, el, reg, 0, SENTINEL))
        for reg in (13, 14):
            rows.extend([(phase, el, reg, 0, SENTINEL), (phase, el, reg, 1, U64)])
    matrix(1, 1, True)
    for reg in range(15):
        rows.extend([(2, 0, reg, 0, SENTINEL), (2, 0, reg, 1, U64)])
    matrix(3, 1, True)
    matrix(4, 2, True)
    matrix(5, 1, False)
    require(len(rows) == 189, 'complete literal access instruction sequence')
    return rows


def access_exception(mode, phase, el, reg, write):
    fields = REGISTERS[reg][1:]
    op0, op1, crn, crm, op2 = fields
    # HCR.TIDCP is checked before unknown-register lookup, unlike RAS.TERR.
    categorized = el == 1 and (phase == 5 or
                    (mode and phase == 3 and write and reg < 11))
    if categorized:
        esr = 0x62000000 | (op0 << 20) | (op2 << 17) | (op1 << 14) | (crn << 10) | (crm << 1) | int(not write)
        return 2, esr
    if not mode or reg >= 13 or el == 0:
        return (1 if el == 0 else el), 0x02000000
    return None


def reference_cycle_bounds(start_before, start_after, stop_before, stop_after,
                           reference_frequency, dsu_frequency):
    """All values are integer ticks/Hz; bounds include only quantization.

    A generic-counter sample n denotes virtual ns in
    [ceil(n*1e9/F), ceil((n+1)*1e9/F)-1]. The tested enable/disable writes
    lie inside their actual before/after samples. The unknown reference
    phase adds at most one DSU edge; no host-wall-clock tolerance is used.
    """
    require(all(type(x) is int and x >= 0 for x in
        (start_before,start_after,stop_before,stop_after)), 'typed time brackets')
    require(0 < reference_frequency <= 10**9 and 0 < dsu_frequency <= 10**9,
            'bounded explicit frequencies')
    require(start_before <= start_after <= stop_before <= stop_after,
            'ordered actual instruction time brackets')
    def ceildiv(a,b): return (a+b-1)//b
    lo = max(0, ceildiv(stop_before*10**9,reference_frequency) -
                  (ceildiv((start_after+1)*10**9,reference_frequency)-1))
    hi = (ceildiv((stop_after+1)*10**9,reference_frequency)-1) - \
                  ceildiv(start_before*10**9,reference_frequency)
    return (lo*dsu_frequency//10**9,
            (hi*dsu_frequency+10**9-1)//10**9)


def pure_cpu_expected(mode, frequency):
    require(type(mode) is bool and type(frequency) is int, 'pure profile arguments')
    return {CPU_PROP: mode, PROFILE_PROP: int(mode), FREQUENCY_PROP: frequency if mode else 0}


def validate_shared_shape(state, frequency, *, cold=False):
    require(type(state) is dict and set(state)==set(GETTERS), 'whole shared getter set')
    require(all(type(state[k]) is int and 0 <= state[k] <= U64 for k in UINT_GETTERS), 'typed shared uint64')
    require(all(type(state[k]) is bool for k in BOOL_GETTERS), 'typed shared booleans')
    require(all(type(state[k]) is str and len(state[k])==48 and
                all(c in '0123456789abcdef' for c in state[k]) for k in HEX_GETTERS), 'complete six-word state strings')
    fixed = {'enabled-profile':1,'reference-identity':0x44535531,'cpu-mask':255,
        'interrupt-intid':82,'event-counter-count':6,'virtual-reference-frequency-hz':frequency,
        'shared-wire-bytes':108,'ceid0':1<<17,'virtual-cycle-model':True,
        'physical-state-captured':False,'cache-events-modeled':False,'bus-events-modeled':False}
    for key,value in fixed.items(): require(state[key]==value, 'immutable virtual policy '+key)
    require(state['pmcr'] & ~1 == PMCR_FIXED, 'PMCR fixed fields and stored E only')
    for key in ('counter-enable','overflow-status','interrupt-enable'):
        require(state[key] & ~ENABLE_MASK == 0, 'reserved mask '+key)
    require(state['selected-counter'] < 32 and state['fractional-phase'] < 10**9, 'selector/phase range')
    require(state['irq-level'] == (bool(state['overflow-status'] & state['interrupt-enable']) and
            not state['migration-quarantined']), 'derived IRQ law')
    require(state['last-sync-ns'] <= (1 << 63)-1, 'nonnegative signed virtual clock')
    counts=[int(state['event-counters-hex'][i:i+8],16) for i in range(0,48,8)]
    types=[int(state['event-types-hex'][i:i+8],16) for i in range(0,48,8)]
    require(all(t & ~TYPE_MASK == 0 for t in types), 'all six event type reserved bits zero')
    for key in ('incoming-started-cpus','incoming-completed-cpus'):
        require(state[key] <= 255, 'eight-bit incoming CPU mask')
    require(state['incoming-completed-cpus'] & ~state['incoming-started-cpus'] == 0,
            'every completed incoming CPU was started')
    # The next previously-unreported wrap is an observable event even with
    # INTEN clear. Compute its deadline directly in integer ns, independently
    # of the model's overflow-avoiding C implementation.
    distances=[]
    if state['pmcr'] & 1 and not state['migration-quarantined']:
        enabled=state['counter-enable'] & ~state['overflow-status']
        if enabled & (1 << 31): distances.append((1 << 64)-state['cycle-counter'])
        for i,t in enumerate(types):
            if enabled & (1 << i) and t & 0xffff == 0x11 and ((t >> 29) & 1)==((t >> 31) & 1):
                distances.append((1 << 32)-counts[i])
    deadline=0
    if distances:
        delay=(min(distances)*10**9-state['fractional-phase']+frequency-1)//frequency
        candidate=state['last-sync-ns']+delay
        if candidate <= (1 << 63)-1: deadline=candidate
    require(state['timer-deadline-ns']==deadline and state['timer-pending']==bool(deadline),
            'exact next-wrap timer and no arbitrary future deadline')
    if cold:
        for key in ('counter-enable','overflow-status','interrupt-enable','selected-counter','cycle-counter',
                    'fractional-phase','incoming-started-cpus','incoming-completed-cpus','timer-deadline-ns'):
            require(state[key]==0, 'cold functional state '+key)
        require(state['pmcr']==PMCR_FIXED and not any(state[k] for k in
                ('irq-level','migration-quarantined','timer-pending')), 'cold controls')
        require(all(state[k]=='0'*48 for k in HEX_GETTERS), 'cold six-slot state')
    return state


def parse_probe_elf(raw):
    require(raw[:7] == b'\x7fELF\x02\x01\x01', 'ELF64 little-endian probe')
    h = struct.unpack_from('<16sHHIQQQIHHHHHH', raw)
    _, typ, machine, version, entry, phoff, shoff, flags, eh, phsize, phnum, shsize, shnum, shstr = h
    require((typ, machine, version, eh, phsize, shsize) == (2, 183, 1, 64, 56, 64), 'ELF identity')
    require(0 < phnum < 32 and 0 < shnum < 128, 'ELF bounded tables')
    require(phoff + phnum * phsize <= len(raw) and shoff + shnum * shsize <= len(raw), 'ELF table bounds')
    segments = []
    for i in range(phnum):
        t, fl, off, va, pa, size, mem, align = struct.unpack_from('<IIQQQQQQ', raw, phoff + i * phsize)
        if t != 1: continue
        require(va == pa and 0x80100000 <= pa < pa + mem <= CONTROL and size <= mem,
                'probe load segment outside test RAM')
        require(off + size <= len(raw), 'ELF segment truncation')
        segments.append(dict(address=pa, offset=off, file_bytes=size, memory_bytes=mem, flags=fl))
    require(segments, 'no ELF load segments')
    symbols = {}
    for i in range(shnum):
        sh = struct.unpack_from('<IIQQQQIIQQ', raw, shoff + i * shsize)
        if sh[1] != 2: continue
        _, _, _, _, off, size, link, _, _, entsize = sh
        require(entsize == 24 and size % 24 == 0 and link < shnum and off + size <= len(raw), 'symbol table')
        string_sh = struct.unpack_from('<IIQQQQIIQQ', raw, shoff + link * shsize)
        strings = raw[string_sh[4]:string_sh[4] + string_sh[5]]
        require(len(strings) == string_sh[5], 'string table truncation')
        for at in range(off, off + size, 24):
            name, info, other, index, value, extent = struct.unpack_from('<IBBHQQ', raw, at)
            require(name < len(strings), 'symbol name range')
            end = strings.find(b'\0', name)
            require(end >= name, 'symbol name terminator')
            key = strings[name:end].decode('ascii')
            if key in ('_start', 'secondary_entry', 'enter_el1', 'enter_el0', 'el1_return_hvc',
                       'el0_return_svc', 'el2_return', 'el1_return') or key.startswith(('read_', 'write_')):
                require(key not in symbols and index != 0, 'duplicate/undefined probe symbol')
                symbols[key] = value
    require(symbols.get('_start') == entry == 0x80100000, 'probe entry')
    instructions = {}
    for reg, row in enumerate(REGISTERS):
        for wr in (0, 1):
            key = ('write_' if wr else 'read_') + row[0]
            address = symbols.get(key)
            require(type(address) is int and address % 4 == 0, 'missing instruction symbol ' + key)
            matches = [s for s in segments if s['address'] <= address and address + 8 <= s['address'] + s['file_bytes']]
            require(len(matches) == 1 and matches[0]['flags'] & 1, 'instruction segment ' + key)
            s = matches[0]; off = s['offset'] + address - s['address']
            insn, ret = struct.unpack_from('<II', raw, off)
            expected = (0xd5000000 if wr else 0xd5200000) | (encoding(row[1:]) << 5)
            require(insn == expected and ret == 0xd65f03c0, 'ELF tested instruction differs: ' + key)
            instructions[key] = {'address': address, 'instruction_u32': insn, 'file_offset': off}
    for key, write, fields in AUXILIARY_INSTRUCTIONS:
        address = symbols.get(key)
        require(type(address) is int and address % 4 == 0, 'missing auxiliary witness '+key)
        matches = [s for s in segments if s['address'] <= address and address+8 <= s['address']+s['file_bytes']]
        require(len(matches)==1 and matches[0]['flags'] & 1, 'auxiliary executable witness')
        s = matches[0]; off = s['offset']+address-s['address']
        insn, ret = struct.unpack_from('<II', raw, off)
        require(insn == ((0xd5000000 if write else 0xd5200000) | (encoding(fields)<<5)) and ret==0xd65f03c0,
                'actual auxiliary instruction encoding '+key)
        instructions[key] = {'address':address,'instruction_u32':insn,'file_offset':off}
    return {'bytes': len(raw), 'sha256': digest(raw), 'entry': entry,
            'load_segments': segments, 'symbols': symbols, 'instructions': instructions}


def recipe_operations(recipe, cpu, frequency=FREQUENCY):
    """Literal test program, independent of C text and model implementation."""
    if recipe == 1:
        return access_operations()
    rows = []
    def r(p, reg, el=2): rows.append((p,el,reg,0,SENTINEL))
    def w(p, reg, value, el=2): rows.append((p,el,reg,1,value & U64))
    def reset(p, el=2):
        for reg,value in ((0,6),(2,U64),(7,U64),(4,U64)):w(p,reg,value,el)
        for i in range(6):w(p,5,i,el);w(p,9,0,el)
        w(p,5,0,el)
    def bank(p, el=2):
        for i in range(6):w(p,5,i,el);r(p,9,el);r(p,10,el)
        for reg in (8,0,1,6,4):r(p,reg,el)
    if recipe == 2:
        if cpu == 0:reset(10)
        r(11,5);r(11,8);w(11,5,cpu%6)
        w(11,9,0xffffffff20000011|cpu)
        w(11,10,0xa55a000012340000|cpu);w(11,8,0x1122334400000000|cpu)
        for reg in (9,10,8):r(11,reg)
        for reg,value in ((5,U64),):w(12,reg,value)
        r(12,5);w(12,9,U64);w(12,10,U64);r(12,9);r(12,10)
        w(12,5,6);r(12,10);w(12,5,cpu%6);r(12,10)
        bank(13)
        if cpu == 7:
            for reg,value,read in ((1,U64,2),(2,0x80000015,1),(6,U64,7),
                    (7,U64,6),(3,U64,4),(4,0x80000015,3)):
                w(14,reg,value);r(14,read)
            w(14,4,U64);w(14,2,U64)
            w(15,0,U64-1);r(15,0);bank(15);w(15,0,0)
    elif cpu != 0:
        require(recipe in (3,4,5,6),'unknown one-CPU recipe')
    elif recipe == 3:
        reset(20)
        for i,value in enumerate((0x11,0xa0000011,0x80000011,0x20000011,0x17,0xffff)):
            w(20,5,i);w(20,9,value);w(20,10,1000+i*100)
        w(20,8,10000);w(20,1,U64)
        w(21,0,1);w(21,0,0);bank(21);bank(22)
        w(23,2,1);w(23,0,1);w(23,0,0);bank(23)
        w(24,0,2);bank(24);w(24,0,4);bank(24)
    elif recipe == 4:
        reset(30,1);w(30,6,1,1);w(30,3,1,1)
        for reg,value in ((5,0),(9,0x11),(10,(1<<32)-frequency//100),
                          (1,1),(0,1),(0,0)):w(31,reg,value,1)
        bank(31,1)
        for reg,value in ((7,U64),(6,1<<31),(8,(1<<64)-frequency//100),
                          (1,1<<31),(0,1),(0,0)):w(32,reg,value,1)
        bank(32,1)
        for reg in (7,4,2):w(33,reg,U64,1)
    elif recipe == 5:
        reset(40)
        for i in range(6):w(40,5,i);w(40,9,0x11);w(40,10,(1<<32)-1-frequency*2+i)
        w(40,8,U64-frequency*2);w(40,1,ENABLE_MASK);w(40,6,0x80000001);w(40,0,1);w(40,3,1)
    elif recipe == 6:
        bank(50);w(50,0,0);bank(51)
    else:require(False,'unknown recipe')
    require(len(rows)<=256,'bounded complete recipe')
    return rows


def decode_mailbox(raw, cpu, mode, epoch, recipe, elf, frequency=FREQUENCY):
    require(len(raw)==BOX_BYTES and cpu in range(8) and type(mode)is bool and
            type(epoch)is int and epoch>0,'complete mailbox identity')
    h=dict(zip(HEADER,struct.unpack_from('<32Q',raw)))
    operations=recipe_operations(recipe,cpu,frequency)
    fixed=dict(magic=MAGIC,abi=1,cpu=cpu,mpidr=0x80000000|(cpu<<8),
        midr=0x412fd050 if cpu<4 else 0x411fd0d0,boot_el=8,epoch=epoch,recipe=recipe,
        count=len(operations),fatal=0,active=U64,done=epoch,nonce=NONCE|epoch,
        hvc_returns=3 if recipe==1 else int(recipe==4 and cpu==0),
        svc_returns=int(recipe==1),actlr_initial=ACTLR_WRITE if mode else 0,
        actlr_final=ACTLR_WRITE if mode else 0,hcr_final=HCR_RW,
        irq_count=3 if recipe==4 and cpu==0 else 0,
        mmio_count=11 if recipe==4 and cpu==0 else 0,
        delay_count=3 if recipe==3 and cpu==0 else 0,
        irq_goal=3 if recipe==4 and cpu==0 else 0,
        phase_hcr=HCR_RW,phase_actlr=ACTLR_WRITE if mode else 0)
    for k in HEADER:
        if k.startswith('reserved'):fixed[k]=0
    for k,v in fixed.items():require(h[k]==v,f'CPU{cpu} complete header {k}')
    require(h['cntfrq']==19_200_000 and (h['pfr0']>>28)&15==1,'actual board generic frequency and CPU RASv1 identity')
    events=[];last=0
    for n,(phase,el,reg,write,value) in enumerate(operations):
        e=dict(zip(EVENT,struct.unpack_from('<24Q',raw,256+192*n)))
        key=('write_' if write else 'read_')+REGISTERS[reg][0]
        pc=elf['instructions'][key]['address']
        control_hcr=HCR_RW|(HCR_TIDCP if recipe==1 and phase in (4,5) else 0)
        control_actlr=ACTLR_WRITE if mode and not(recipe==1 and phase==3) else 0
        expected=dict(seq=n,phase=phase,el=el,reg=reg,write=write,input=value,pc=pc,complete=1,
            counter_frequency=h['cntfrq'],hcr=control_hcr,actlr=control_actlr)
        for k in EVENT:
            if k.startswith('reserved'):expected[k]=0
        for k,v in expected.items():require(e[k]==v,f'CPU{cpu} event{n} {k}')
        require(last<=e['time_before']<=e['time_after'],'actual event time order');last=e['time_after']
        exception=access_exception(mode,phase,el,reg,write) if recipe==1 else None
        if exception:
            level,esr=exception
            require((e['traps'],e['trap_el'],e['esr'],e['elr'])==(1,level,esr,pc),'exact synchronous exception witness')
            require(e['spsr']&0x3cf==(0x3c0 if el==0 else 0x3c5 if el==1 else 0x3c9),'interrupted EL and DAIF')
            require(e['far']==0 and e['output']==value,'UNDEF/trap kept argument and did not abort memory')
        else:
            require(all(e[k]==0 for k in ('traps','trap_el','esr','elr','spsr','far')),'unexpected exception in legal access')
            if write:require(e['output']==value,'MSR wrapper returned its complete input')
        events.append(e)
    require(raw[256+len(events)*192:256+256*192]==bytes((256-len(events))*192),'unused complete event slots')
    base=256+256*192
    irqs=[dict(zip(IRQ_EVENT,struct.unpack_from('<12Q',raw,base+i*96))) for i in range(h['irq_count'])]
    require(raw[base+len(irqs)*96:base+16*96]==bytes((16-len(irqs))*96),'unused IRQ slots')
    for i,e in enumerate(irqs):
        mask=1 if i<2 else 1<<31
        require((e['seq'],e['iar'],e['eoir'],e['level'])==(i,82,82,1),'actual GIC82 IAR and EOI')
        require((e['overflow_before'],e['overflow_after'],e['counter_enable'],e['interrupt_enable'])==(mask,0,0,mask),'IRQ W1C and gate state')
        require(e['time_before']<=e['time_after'] and e['spsr']&0x3cf==0x345,'actual IRQ EL1 with IRQ unmasked')
        require(any(s['address']<=e['elr']<s['address']+s['file_bytes'] and s['flags']&1
                    for s in elf['load_segments']),'IRQ ELR lies in actual executable guest')
    base+=16*96
    mmio=[dict(zip(MMIO_EVENT,struct.unpack_from('<8Q',raw,base+i*64))) for i in range(h['mmio_count'])]
    require(raw[base+len(mmio)*64:base+32*64]==bytes((32-len(mmio))*64),'unused MMIO witness slots')
    expected_mmio=((4,0x17a00000,1,0),(4,0x17a60014,1,0),(4,0x17a60014,0,0),
        (4,0x17a00088,1,1<<18),(1,0x17a00452,1,0x80),(4,0x17a00c14,1,0),
        (8,0x17a06290,1,0),(4,0x17a00108,1,1<<18),(4,0x17a00000,1,2),
        (4,0x17a00000,0,0),(4,0x17a00188,1,1<<18))
    for i,e in enumerate(mmio):
        width,address,write,value=expected_mmio[i]
        require(tuple(e[k] for k in ('seq','address','width','write','input','complete','reserved'))==
                (i,address,width,write,value,1,0),'literal actual GIC transaction')
        expected_output=value if write else (0x52 if address==0x17a00000 else 0)
        require(e['output']==expected_output,'actual GIC register reply')
    base+=32*64
    delays=[dict(zip(DELAY_EVENT,struct.unpack_from('<5Q',raw,base+i*40))) for i in range(h['delay_count'])]
    require(raw[base+len(delays)*40:]==bytes((8-len(delays))*40),'unused complete delay witness slots')
    for i,e in enumerate(delays):
        require((e['seq'],e['phase'],e['requested_ticks'])==(i,21+i,h['cntfrq']//100) and
                e['end']>=e['start']+e['requested_ticks'],'actual generic-counter bounded wait')
    return {'cpu':cpu,'header':h,'events':events,'irq_events':irqs,'gic_mmio':mmio,
            'delays':delays,'bytes':len(raw),'sha256':digest(raw),
            'instruction_operations':len(events),'actual_exceptions':sum(e['traps'] for e in events)}



class Stream:
    """Complete file framing, RAM pages, fields, footer and trailer bounds."""
    def __init__(self, raw):
        self.raw, self.pos, self.cpus = raw, 0, []
        self.sections = {}
        self.ram_sizes = {}
        self.ram_name = None
        self.ram_pages = 0
        # A trailer candidate must end exactly at EOF, decode as JSON, and
        # later be reached by the forward parser. No CPU byte-pattern scan.
        candidates = []
        start = max(8, len(raw) - 16 * 1024 * 1024)
        at = raw.find(b'\x00\x06', start)
        while at >= 0:
            if at + 6 <= len(raw):
                size = int.from_bytes(raw[at + 2:at + 6], 'big')
                if at + 6 + size == len(raw):
                    try:
                        desc = json.loads(raw[at + 6:])
                        if type(desc) is dict and type(desc.get('devices')) is list:
                            candidates.append((at, desc))
                    except (ValueError, UnicodeDecodeError):
                        pass
            at = raw.find(b'\x00\x06', at + 1)
        require(len(candidates) == 1, 'one complete VMDESCRIPTION trailer')
        self.end, self.desc = candidates[0]
        require(self.desc.get('page_size') == 4096, 'migration page size')
        self.devices = {}
        for device in self.desc['devices']:
            key = (device['name'], device['instance_id'])
            require(key not in self.devices and type(device.get('fields')) is list, 'duplicate/invalid VMDESCRIPTION device')
            self.devices[key] = device

    def take(self, n):
        require(type(n) is int and n >= 0 and self.pos + n <= len(self.raw), 'stream truncation')
        value = self.raw[self.pos:self.pos + n]; self.pos += n
        return value

    def uint(self, n): return int.from_bytes(self.take(n), 'big')
    def string(self): return self.take(self.uint(1)).decode('ascii')

    def fields(self, description):
        result = {'start': self.pos, 'fields': [], 'subsections': []}
        for field in description['fields']:
            count = field.get('array_len', 1)
            require(type(count) is int and 0 <= count < (1 << 20), 'field array bound')
            for i in range(count):
                start = self.pos
                typ = field['type']
                nested = None
                if typ == 'struct':
                    nested = self.fields(field['struct'])
                elif typ == 'capability':
                    self.take(self.uint(1))
                else:
                    self.take(field['size'])
                result['fields'].append({'name': field['name'], 'type': typ,
                    'index': field.get('index', 0) + i, 'start': start,
                    'size': self.pos - start, 'nested': nested})
        for sub in description.get('subsections', []):
            start = self.pos
            require(self.uint(1) == 5, 'missing subsection marker')
            name = self.string(); version = self.uint(4)
            require(name == sub['vmsd_name'] and version == sub['version'], 'subsection header/description disagreement')
            body = self.fields(sub)
            result['subsections'].append({'name': name, 'version': version, 'start': start,
                                          'body_start': body['start'], 'end': self.pos, 'body': body})
        result['end'] = self.pos
        return result

    def ram(self):
        while True:
            word = self.uint(8); flags = word & 4095; addr = word & ~4095
            if flags & 4:
                remaining = addr
                while remaining:
                    name = self.string(); size = self.uint(8)
                    require(name not in self.ram_sizes and 0 < size <= remaining and size % 4096 == 0, 'RAM block size')
                    self.ram_sizes[name] = size; remaining -= size
                flags &= ~4
            if flags & (2 | 8):
                require((flags & (2 | 8)) in (2, 8), 'RAM encoding conflict')
                if flags & 32:
                    flags &= ~32
                else:
                    self.ram_name = self.string()
                require(self.ram_name in self.ram_sizes and addr + 4096 <= self.ram_sizes[self.ram_name], 'RAM page identity/range')
                self.take(1 if flags & 2 else 4096)
                self.ram_pages += 1
                flags &= ~(2 | 8)
            if flags == 0x200: continue
            if flags & 16:
                require(flags == 16, 'RAM EOS extra flags'); return
            require(flags == 0, 'unsupported migration RAM flag')

    def cpu(self, instance, version, body, section_start, sid):
        require(instance==len(self.cpus) and instance in range(8) and version==22,
                'one canonical CPU0..7 sequence at unchanged v22')
        base=body['start'];fields=body['fields'];byname={}
        for field in fields:byname.setdefault(field['name'],[]).append(field)
        require([(f['start'],f['size']) for f in byname.get('cpreg_vmstate_array_len',[])]==[(base+568,4)],
                'original CPU count field offset')
        count=int.from_bytes(self.raw[base+568:base+572],'big',signed=True)
        require(0<count<65536,'actual variable-length CPU CPreg list')
        indexes=byname.get('cpreg_vmstate_indexes',[]);values=byname.get('cpreg_vmstate_values',[])
        require(len(indexes)==len(values)==count,'complete index/value arrays')
        for i,(ix,val) in enumerate(zip(indexes,values)):
            require((ix['start'],ix['size'],val['start'],val['size'])==
                (base+572+i*8,8,base+572+(count+i)*8,8),'exact indexed CPU field positions')
        ids=list(struct.unpack_from('>'+str(count)+'Q',self.raw,base+572))
        vals=list(struct.unpack_from('>'+str(count)+'Q',self.raw,base+572+count*8))
        require(ids==sorted(set(ids)) and fields[-1]['start']+fields[-1]['size']==base+637+count*16,
                'complete original base and unique sorted CPregs')
        require(0x603000000013c298 in ids and vals[ids.index(0x603000000013c298)]==0,'old ERRIDR zero')
        forbidden=[0x6030000000130000|encoding(row[1:]) for row in REGISTERS]
        require(not(set(forbidden)&set(ids)),'all DSU registers and holes absent from generic raw CPregs')
        matches=[s for s in body['subsections'] if s['name']==SUBSECTION]
        require(len(matches)<=1,'one DSU subsection per CPU')
        row={'instance':instance,'version':version,'section_id':sid,'section_start':section_start,
            'body_start':base,'body_end':body['end'],'base_bytes':637+count*16,'cpreg_count':count,
            'cpreg_ids':ids,'cpreg_values':vals,'profile':None,'frequency_hz':None,'actlr_el2':None,
            'shared_bank':None,'subsections':[{k:s[k] for k in ('name','version','start','body_start','end')}
                                            for s in body['subsections']],
            'body_sha256':digest(self.raw[base:body['end']])}
        if matches:
            sub=matches[0];at=sub['body_start'];fs=sub['body']['fields']
            require(sub['version']==1 and sub['end']-at==(132 if instance==0 else 24),
                    'exact DSU owner132/nonowner24 payload')
            expected=[('tb371fc_dsu_profile',at,4),('tb371fc_dsu_cpu_index',at+4,4),
                      ('tb371fc_dsu_frequency_hz',at+8,8),('tb371fc_dsu_actlr_el2',at+16,8)]
            if instance==0:expected.append(('tb371fc_dsu_wire',at+24,108))
            require([(f['name'],f['start'],f['size']) for f in fs]==expected and
                    sub['body']['subsections']==[],'literal dedicated wire field order')
            profile,index,freq,actlr=struct.unpack_from('>IIQQ',self.raw,at)
            require(profile==1 and index==instance and 0<freq<=10**9 and actlr&~ACTLR_WRITE==0,
                    'valid canonical immutable profile and local permission')
            row.update(profile=profile,frequency_hz=freq,actlr_el2=actlr)
            require(0x603000000013e081 not in ids,'on ACTLR_EL2 has one dedicated owner')
            if instance==0:
                nested=fs[-1]['nested'];require(nested is not None and nested['subsections']==[],'whole shared struct')
                names=('magic','cpu_mask','irq_intid','event_counters','frequency_hz','pmcr','count_enable',
                       'overflow','interrupt_enable','selector')
                sizes=(4,4,4,4,8,4,4,4,4,4);off=at+24;expected_bank=[]
                for name,size in zip(names,sizes):expected_bank.append((name,off,size));off+=size
                for name in ('event_count','event_type'):
                    for i in range(6):expected_bank.append((name,off,4));off+=4
                expected_bank += [('cycle_count',off,8),('fraction',off+8,8)]
                require([(f['name'],f['start'],f['size']) for f in nested['fields']]==expected_bank,
                        'all 108 shared wire bytes have fixed meaning')
                data=self.raw[at+24:sub['end']]
                words=struct.unpack('>4IQ5I6I6I2Q',data)
                bank=dict(zip(names,words[:10]));bank.update(event_count=list(words[10:16]),
                    event_type=list(words[16:22]),cycle_count=words[22],fraction=words[23])
                require(tuple(bank[k] for k in names[:5])==(0x44535531,255,82,6,freq),'shared immutable topology and frequency')
                require(bank['pmcr']&~1==0 and all(bank[k]&~ENABLE_MASK==0 for k in
                    ('count_enable','overflow','interrupt_enable')) and bank['selector']<32 and
                    bank['fraction']<10**9 and all(x&~TYPE_MASK==0 for x in bank['event_type']),
                    'whole shared-bank semantic validity')
                row['shared_bank']={'offset':at+24,'bytes':108,'sha256':digest(data),'values':bank}
        else:
            require(0x603000000013e081 in ids and vals[ids.index(0x603000000013e081)]==0,
                    'default-off original ACTLR_EL2 CONST remains')
        self.cpus.append(row)

    def parse(self, mode, frequency):
        require(self.take(8)==b'QEVM\0\0\0\x03','stream magic/version3')
        pending=None
        while self.pos<self.end:
            start=self.pos;tag=self.uint(1)
            if tag==7:
                require(not self.sections and pending is None,'configuration before devices')
                if 'configuration' in self.desc:self.fields(self.desc['configuration'])
                else:require(self.take(self.uint(4))==b'tb371fc','exact board configuration')
            elif tag in (1,4):
                require(pending is None,'previous full footer required')
                sid=self.uint(4);name=self.string();instance=self.uint(4);version=self.uint(4)
                key=(name,instance);require(sid not in self.sections,'unique section ID')
                self.sections[sid]=(key,version)
                if key==('ram',0):require((tag,version)==(1,4),'RAM declaration');self.ram()
                else:
                    require(tag==4 and key in self.devices,'known complete device section')
                    d=self.devices[key];require(d['version']==version,'device version/description')
                    body=self.fields(d)
                    if name=='cpu':self.cpu(instance,version,body,start,sid)
                pending=sid
            elif tag in (2,3):
                require(pending is None,'iterative footer order');sid=self.uint(4)
                require(self.sections.get(sid)==(('ram',0),4),'only RAM iterative');self.ram();pending=sid
            elif tag==0x7e:
                require(pending is not None and self.uint(4)==pending,'exact section footer')
                if self.cpus and self.cpus[-1]['section_id']==pending:self.cpus[-1]['section_end']=self.pos
                pending=None
            elif tag==8:require((self.uint(2),self.uint(2))==(11,0),'fixed switchover command')
            else:require(False,'unknown whole-stream marker')
        require(pending is None and self.pos==self.end and self.take(2)==b'\0\6','complete forward EOF/trailer')
        size=self.uint(4);require(self.take(size)==self.raw[self.end+6:] and self.pos==len(self.raw),'all stream bytes consumed')
        require([c['instance'] for c in self.cpus]==list(range(8)),'complete canonical eight CPU list')
        require(all(c['profile']==(1 if mode else None) and c['frequency_hz']==(frequency if mode else None)
                    for c in self.cpus),'all eight mode and frequency markers')
        payload=sum(s['end']-s['body_start'] for c in self.cpus for s in c['subsections'] if s['name']==SUBSECTION)
        require(payload==(300 if mode else 0),'one300-byte shared profile, no eight-way duplicated bank')
        return {'bytes':len(self.raw),'sha256':digest(self.raw),'cpu_sections':self.cpus,
            'ram_blocks':self.ram_sizes,'ram_pages_parsed':self.ram_pages,'dedicated_payload_bytes':payload,
            'complete_forward_parse':True,'all_bytes_consumed':True}


def parse_stream(raw, mode, frequency=FREQUENCY):
    return Stream(raw).parse(mode,frequency)


def state_from_wire(wire):
    c=wire['cpu_sections'][0]
    return c['shared_bank']['values'] if c['profile']==1 else None


def compare_state_wire(state, wire):
    bank=state_from_wire(wire)
    require(bank is not None,'on wire bank required')
    mapping={'pmcr':'pmcr','counter-enable':'count_enable','overflow-status':'overflow',
        'interrupt-enable':'interrupt_enable','selected-counter':'selector','cycle-counter':'cycle_count',
        'fractional-phase':'fraction','virtual-reference-frequency-hz':'frequency_hz'}
    for key,field in mapping.items():
        require(state[key]==(bank[field]|PMCR_FIXED if key=='pmcr' else bank[field]),'full getter/wire '+key)
    for key,field in (('event-counters-hex','event_count'),('event-types-hex','event_type')):
        require(state[key]==''.join(f'{n:08x}' for n in bank[field]),'complete six-slot wire '+key)
    return True


def modular_interval_contains(value, lower, upper, width):
    modulus=1<<width
    require(0<=value<modulus and 0<=lower<=upper,'counter interval arguments')
    return (lower-value+modulus-1)//modulus <= (upper-value)//modulus


def ns_interval(tick, frequency=19_200_000):
    return ((tick*10**9+frequency-1)//frequency,
            ((tick+1)*10**9+frequency-1)//frequency-1)


def elapsed_edges(begin_before, begin_after, end_before, end_after, frequency):
    return reference_cycle_bounds(begin_before,begin_after,end_before,end_after,19_200_000,frequency)


def shared_values(state):
    return {'pmcr':state['pmcr']&1,'enable':state['counter-enable'],
        'overflow':state['overflow-status'],'inten':state['interrupt-enable'],
        'sel':state['selected-counter'],'cycle':state['cycle-counter'],
        'counts':[int(state['event-counters-hex'][i:i+8],16) for i in range(0,48,8)],
        'types':[int(state['event-types-hex'][i:i+8],16) for i in range(0,48,8)]}


def verify_epoch(cpus, mode, recipe, frequency, *, before=None, after=None):
    """Cross-CPU semantic oracle; actual elapsed ticks bound only live counts.

    Control and static reads remain exact. Timing intervals come from the two
    actual guest CNTPCT samples around each tested operation, never elapsed
    host wall time or a producer-supplied success flag.
    """
    require([c['cpu'] for c in cpus]==list(range(8)),'eight complete decoded mailboxes')
    if recipe==1:
        for c in cpus:
            for e in c['events']:
                if e['write'] or e['traps']:continue
                expected=PMCR_FIXED if e['reg']==0 else 1<<17 if e['reg']==11 else 0
                require(e['output']==expected,'literal legal permission read value')
        return {'kind':'permissions','operations':sum(len(c['events']) for c in cpus),
                'exact_exceptions':sum(c['actual_exceptions'] for c in cpus)}
    require(mode,'functional recipes require explicit on profile')
    if recipe==6:
        require(before is not None,'incoming continuation has complete pre-cont stored bank')
        initial=shared_values(before);start=before['last-sync-ns'];fraction=before['fractional-phase']
        events=cpus[0]['events'];stop=next(e for e in events if e['reg']==0 and e['write'])
        def from_initial(e):
            if not initial['pmcr']:return (0,0)
            a=ns_interval(e['time_before'])[0];b=ns_interval(e['time_after'])[1]
            require(a>=start,'incoming guest clock is not before restored virtual baseline')
            return ((fraction+(a-start)*frequency)//10**9,(fraction+(b-start)*frequency)//10**9)
        stop_bounds=from_initial(stop)
        selected=initial['sel'];final_counts={};final_cycle=None
        for e in events:
            reg=e['reg']
            if e['write']:
                if reg==5:selected=e['input']&31
                continue
            if reg in (8,10):
                idx=31 if reg==8 else selected
                seed=initial['cycle'] if idx==31 else initial['counts'][idx]
                enabled=bool(initial['enable']&(1<<idx)) and (idx==31 or
                    (initial['types'][idx]&0xffff==0x11 and
                     bool(initial['types'][idx]&(1<<31))==bool(initial['types'][idx]&(1<<29))))
                lo,hi=(from_initial(e) if e['phase']==50 else stop_bounds) if enabled else (0,0)
                require(modular_interval_contains(e['output'],seed+lo,seed+hi,64 if idx==31 else 32),
                        'actual continued count lies in exact reference-clock quantization interval')
                if e['phase']==51:
                    if idx==31:final_cycle=e['output']
                    else:final_counts[idx]=e['output']
            elif reg==9:require(e['output']==initial['types'][selected],'continued type preserved')
            elif reg==0:require(e['output']==PMCR_FIXED|(initial['pmcr'] if e['phase']==50 else 0),'continued global E')
            elif reg==1:require(e['output']==initial['enable'],'continued enable aliases')
            elif reg==6:require(e['output']==initial['inten'],'continued interrupt enable')
            elif reg==4:
                require(e['output']&initial['overflow']==initial['overflow'] and e['output']&~ENABLE_MASK==0,
                        'restored sticky overflow is retained')
        require(set(final_counts)==set(range(6)) and final_cycle is not None,'all continued final counters')
        if after:
            a=shared_values(after)
            require(a['counts']==[final_counts[i] for i in range(6)] and a['cycle']==final_cycle and
                    a['pmcr']==0 and a['types']==initial['types'] and a['enable']==initial['enable'] and
                    a['inten']==initial['inten'],'post-cont pure bank equals all stopped read values')
        return {'kind':'live-incoming-continuation','stop_edge_interval':list(stop_bounds),
                'initial_state':before,'final_counts':[final_counts[i] for i in range(6)],'final_cycle':final_cycle}

    state={'pmcr':0,'enable':0,'overflow':0,'inten':0,'sel':0,'cycle':0,'counts':[0]*6,'types':[0]*6}
    intervals={};starts={};timing=[]
    ordered=[]
    if recipe==2:
        for phase_group in ((10,11,12),(13,14,15)):
            for c in cpus:ordered += [(c['cpu'],e) for e in c['events'] if e['phase'] in phase_group]
    else:ordered=[(0,e) for e in cpus[0]['events']]
    previous_time=0
    def counter_value_ok(value,index):
        if index in intervals:
            lo,hi=intervals[index];return modular_interval_contains(value,lo,hi,64 if index==31 else 32)
        return value==(state['cycle'] if index==31 else state['counts'][index])
    def freeze_counting(begin,end):
        lo,hi=elapsed_edges(begin['time_before'],begin['time_after'],end['time_before'],end['time_after'],frequency)
        for i in list(range(6))+[31]:
            enabled=state['enable']&(1<<i)
            if i!=31:enabled=enabled and state['types'][i]&0xffff==0x11 and bool(state['types'][i]&(1<<31))==bool(state['types'][i]&(1<<29))
            if not enabled:continue
            seed=state['cycle'] if i==31 else state['counts'][i]
            old=intervals.get(i,(seed,seed));intervals[i]=(old[0]+lo,old[1]+hi)
        timing.append({'phase':begin['phase'],'first_event':begin['seq'],'last_event':end['seq'],
                       'reference_edge_interval':[lo,hi]})
    irq_used=0
    for cpu,e in ordered:
        require(previous_time<=e['time_before'],'literal cross-CPU operation blocks were actually ordered')
        previous_time=e['time_after'];reg,value=e['reg'],e['input'];phase=e['phase']
        if recipe==4 and irq_used<len(cpus[0]['irq_events']):
            q=cpus[0]['irq_events'][irq_used]
            if q['time_after']<=e['time_before']:
                if irq_used:
                    begin=starts['active'];pseudo={'time_before':q['time_before'],'time_after':q['time_after'],'seq':e['seq']}
                    freeze_counting(begin,pseudo)
                    require(timing[-1]['reference_edge_interval'][1]>=frequency//100,'actual overflow cannot precede required reference edges')
                state['enable']=0;state['overflow']=0;irq_used+=1
                starts.pop('active',None)
        if not e['write']:
            if reg==10:
                require(e['output']==0 if state['sel']>=6 else counter_value_ok(e['output'],state['sel']),
                        'actual selected counter value')
                if state['sel']<6 and (not state['pmcr'] or not(state['enable']&(1<<state['sel']))):
                    state['counts'][state['sel']]=e['output'];intervals.pop(state['sel'],None)
            elif reg==8:
                require(counter_value_ok(e['output'],31),'actual full64 cycle value')
                if not state['pmcr'] or not(state['enable']&(1<<31)):
                    state['cycle']=e['output'];intervals.pop(31,None)
            else:
                wanted={0:PMCR_FIXED|state['pmcr'],1:state['enable'],2:state['enable'],
                        3:state['overflow'],4:state['overflow'],5:state['sel'],6:state['inten'],7:state['inten'],
                        9:state['types'][state['sel']] if state['sel']<6 else 0,11:1<<17,12:0}[reg]
                require(e['output']==wanted,'actual exact DSU control/static read')
            continue
        if reg==0:
            if state['pmcr'] and not(value&1) and 'active' in starts:freeze_counting(starts.pop('active'),e)
            if value&2:state['counts']=[0]*6;intervals={k:v for k,v in intervals.items() if k==31}
            if value&4:state['cycle']=0;intervals.pop(31,None)
            if not state['pmcr'] and value&1:starts['active']=e
            state['pmcr']=value&1
        elif reg in (1,2):state['enable']=(state['enable']|value if reg==1 else state['enable']&~value)&ENABLE_MASK
        elif reg in (3,4):state['overflow']=(state['overflow']|value if reg==3 else state['overflow']&~value)&ENABLE_MASK
        elif reg in (6,7):state['inten']=(state['inten']|value if reg==6 else state['inten']&~value)&ENABLE_MASK
        elif reg==5:state['sel']=value&31
        elif reg==9 and state['sel']<6:state['types'][state['sel']]=value&TYPE_MASK
        elif reg==10 and state['sel']<6:state['counts'][state['sel']]=value&0xffffffff;intervals.pop(state['sel'],None)
        elif reg==8:state['cycle']=value;intervals.pop(31,None)
    if recipe==4:require(irq_used==3,'three real IRQ handlers inserted into exact operation history')
    if recipe==3:
        delays=cpus[0]['delays'];require(len(timing)==2,'two enabled time windows, one disabled delay')
        events=cpus[0]['events']
        for d in delays:
            earlier=[e for e in events if e['time_after']<=d['start']]
            later=[e for e in events if e['time_before']>=d['end']]
            require(earlier and later and earlier[-1]['phase']==(21 if d['phase']==22 else d['phase']) and
                    later[0]['phase']==d['phase'],'delay lies between exact surrounding instruction phases')
        require(all(x['reference_edge_interval'][0]>0 for x in timing),'real nonzero enabled elapsed time')
        banks={}
        for phase in (21,22,23):
            values=[e['output'] for e in events if e['phase']==phase and not e['write'] and e['reg']==10]
            cycles=[e['output'] for e in events if e['phase']==phase and not e['write'] and e['reg']==8]
            require(len(values)==6 and len(cycles)==1,'all six stopped slots and cycle each time window')
            banks[phase]=(values,cycles[0])
        a,ac=banks[21];b,bc=banks[22];c,cc=banks[23]
        require(a[0]-1000==a[1]-1100==ac-10000>0 and a[2:]==[1200,1300,1400,1500],
                'shared tick edges and explicit NS filter/unadvertised events')
        require(b==a and bc==ac,'disabled elapsed interval never catches up')
        require(c[0]==b[0] and c[1]-b[1]==cc-bc>0 and c[2:]==b[2:],
                'per-counter disable preserves slot while other slot and cycle advance together')
    if after:
        actual=shared_values(after)
        if recipe==5:
            begin=starts['active'];a=ns_interval(begin['time_before'])[0];b=ns_interval(begin['time_after'])[1]
            end=after['last-sync-ns'];require(end>=b,'paused snapshot after enable instruction')
            lo=max(0,(end-b)*frequency//10**9);hi=((end-a)*frequency+10**9-1)//10**9
            required=allowed=state['overflow']
            for i in list(range(6))+[31]:
                seed=state['cycle'] if i==31 else state['counts'][i]
                v=actual['cycle'] if i==31 else actual['counts'][i]
                require(modular_interval_contains(v,seed+lo,seed+hi,64 if i==31 else 32),'armed pause counts match actual elapsed reference')
                modulus=1<<(64 if i==31 else 32)
                if seed+lo>=modulus:required|=1<<i
                if seed+hi>=modulus:allowed|=1<<i
            require(actual['overflow']&required==required and actual['overflow']&~allowed==0,
                    'software status plus only actually reachable natural wrap bits')
            timing.append({'kind':'armed-pause','reference_edge_interval':[lo,hi]})
        else:
            for i in list(range(6))+[31]:
                require(counter_value_ok(actual['cycle'] if i==31 else actual['counts'][i],i),'pure snapshot complete counter witness')
            require(actual['overflow']==state['overflow'],'pure stopped sticky state')
        for key in ('pmcr','enable','inten','sel','types'):
            require(actual[key]==state[key],'all shared post-epoch control state '+key)
    return {'kind':{2:'cross-CPU-shared',3:'virtual-reference-clock',4:'real-counter-overflow-and-GIC',5:'armed-shared-migration'}[recipe],
            'operations':len(ordered),'timing_bounds':timing,'actual_irq_handlers':irq_used}



ALTERNATE=19_200_000
PURE=(('qtest-default-off',None,FREQUENCY),('qtest-on',True,FREQUENCY),
      ('qtest-frequency-min',True,1),('qtest-frequency-max',True,1_000_000_000))
FUNCTIONAL=(('default-off',None,1,FREQUENCY,False),('explicit-off',False,1,FREQUENCY,False),
    ('on-permissions',True,1,FREQUENCY,False),('shared-reset',True,2,FREQUENCY,False),
    ('clock-reference',True,3,FREQUENCY,False),('clock-alternate',True,3,ALTERNATE,False),
    ('overflow-irq',True,4,FREQUENCY,False),('ras-coexist',True,1,FREQUENCY,True))
INVALID=(('invalid-cortex-a55','cortex-a55',FREQUENCY),
         ('invalid-cortex-a77','cortex-a77',FREQUENCY),
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

def need(ok, why):
    if not ok: raise ValueError('DSU evidence: ' + why)


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


def compile_evidence(root,repo,elf):
    command=json.loads((root/'build/command.json').read_bytes())
    expected=['aarch64-linux-gnu-gcc','-O2','-Wall','-Wextra','-Werror','-ffreestanding','-fno-builtin',
        '-fno-stack-protector','-fno-pie','-mstrict-align','-mgeneral-regs-only','-march=armv8.2-a',
        '-nostdlib','-static','-Wl,--build-id=none','-T',str(repo/'tb371fc/tests/dsu_pmu/link.ld'),
        str(repo/'tb371fc/tests/dsu_pmu/start.S'),str(repo/'tb371fc/tests/dsu_pmu/probe.c'),
        '-o',str(root/'build/probe.elf')]
    equal(command,expected,'fixed freestanding general-register compiler argv')
    equal(json.loads((root/'build/execution.json').read_bytes()),
        {'returncode':0,'timed_out':False,'actually_waited_and_reaped':True,'passed':True},'actual compiler wait/status')
    equal((root/'build/stdout.log').read_bytes(),b'','compiler stdout')
    equal((root/'build/stderr.log').read_bytes(),b'','compiler stderr with -Werror')
    need(elf['instructions'] and len(elf['instructions'])==38,'all38 exact ELF system-register instruction witnesses')


COUNTERS=('qom_get','qom_set','qtest','memory','device_mmio','ram','cont')
SUMMARY={'checks':54,'processes':54,'normal_exit_processes':18,'rejected_processes':36,
 'actual_Popen_wait_calls':90,'successful_incoming':4,'roundtrips':2,'rejected_migrations':32,
 'actual_stream_files':33,'canonical_streams':4,'mutated_streams':29,'guest_epochs':16,
 'cpus_per_epoch':8,'guest_instruction_operations':13061,'real_GIC82_handlers':3,
 'real_arithmetic_overflow_handlers':2,'software_overflow_handlers':1,
 'retained_cpu_vmstate_version':22,'dedicated_profile_version':1,'shared_bank_bytes':108,
 'complete_profile_payload_bytes':300,'common_bytes_per_cpu':24,'DSU_raw_cpreg_entries':0,
 'typed_shared_getters':28,'typed_CPU_getters_per_CPU':3,'enabled_two_pass_pure_getters':104,
 'harness_methods_before':51,'boot_runner_methods':45}
EXPECTED_VALUES={'schema':SCHEMA,'summary':SUMMARY,'binary_unchanged':True,
 'off_wire_original_and_on_NO_RAW_verified':True,'native_executed':False,
 'original_inputs_used':False,'physical_PMU_measured':False,'physical_clock_measured':False,
 'cache_or_bus_events_modeled':False,'Android_or_UI_verified':False}
EXPECTED_LENGTHS={'checks':54,'processes':54,'roundtrips':2,'rejected_migrations':32,
 'streams':33,'guest_epochs':16,'source_files':7}
INVALID_NAMES=tuple(x[0] for x in INVALID)


def raw_protocol(directory, cleanup, invalid=False):
    raw=(directory/'protocol.jsonl').read_bytes()
    need(raw.endswith(b'\n'),'complete protocol final line')
    rows=[json.loads(x) for x in raw.splitlines()]
    need(rows and rows[-1]=={'cleanup':cleanup},'full cleanup journal last')
    calls=[];pending=None;greeting=0;nextid=1;waits=[];drains={};events=[]
    sent=[];replies=[];irq=[];counts={k:0 for k in COUNTERS}
    for i,row in enumerate(rows):
        if set(row)=={'qmp_send'}:
            need(pending is None,'serial request boundary');q=row['qmp_send']
            equal(q.get('id'),nextid,'monotonic exact QMP request IDs');nextid+=1
            before=dict(counts);k={'qom-get':'qom_get','qom-set':'qom_set','cont':'cont'}.get(q['execute'])
            if k:counts[k]+=1
            pending={'kind':'qmp','request':q,'index':i,'before':before,'after':dict(counts),'irq_before':len(irq)}
        elif set(row)=={'qmp_receive'}:
            q=row['qmp_receive']
            if 'QMP' in q:
                need(greeting==0 and pending is None and not calls,'single initial QMP greeting');greeting+=1
            elif 'event' in q:events.append({'index':i,'value':q})
            else:
                need(pending is not None and pending['kind']=='qmp' and q.get('id')==pending['request']['id'],
                     'every actual QMP reply has its complete request')
                pending.update(reply=q,irq_after=len(irq));calls.append(pending);pending=None
        elif set(row)=={'qtest_send'}:
            need(pending is None,'serial QTest boundary');q=row['qtest_send'];words=q.split()
            before=dict(counts);counts['qtest']+=1
            if words[0] in ('read','write'):
                address,size=int(words[1],0),int(words[2],0)
                need(0x80000000<=address<address+size<=0x88000000,'host only explicit RAM bytes')
                for k in ('memory','ram'):counts[k]+=1
            else:need(q in ('irq_intercept_in /machine/gic','endianness'),'only nonmutating input observer/fence')
            pending={'kind':'qtest','request':q,'index':i,'before':before,'after':dict(counts),'irq_before':len(irq)}
            sent.append(q)
        elif set(row)=={'qtest_receive'}:
            line=row['qtest_receive'];replies.append(line)
            if line.startswith('IRQ '):
                need(line in ('IRQ raise 50','IRQ lower 50'),'only actual DSU GIC input50')
                irq.append({'action':line.split()[1],'number':50});continue
            need(pending is not None and pending['kind']=='qtest' and line.startswith('OK'),'complete QTest response')
            pending.update(reply=line,irq_after=len(irq));calls.append(pending);pending=None
        elif set(row)=={'actual_wait'}:waits.append(row['actual_wait'])
        elif set(row)=={'drain'}:
            d=row['drain'];label=d['channel'];need(label not in drains,'single full channel drain');drains[label]=d
            if label=='qtest':
                tail=bytes.fromhex(d['drain_hex'])
                need(not tail or tail.endswith(b'\n'),'complete IRQ tail framing')
                for line in tail.decode().splitlines():
                    need(line in ('IRQ raise 50','IRQ lower 50'),'all drained IRQ lines allowed and complete')
                    replies.append(line);irq.append({'action':line.split()[1],'number':50})
        elif set(row)=={'channel','terminal'}:
            need(row['channel'] in ('qmp','qtest') and row['terminal'] in ('eof','reset'),'explicit transport terminal')
        elif set(row)=={'cleanup'}:need(i==len(rows)-1,'only last cleanup')
        else:need(False,'unrecognized protocol record')
    need(pending is None,'every complete submitted request was answered')
    equal(greeting,0 if invalid else 1,'actual connected QMP count')
    equal(counts,cleanup['counters'],'raw full counters');equal(waits,cleanup['wait_calls'],'every actual Popen.wait record')
    equal(irq,cleanup['irq_events'],'all IRQ events including full post-wait drain')
    equal(set(drains),set() if invalid else {'qmp','qtest'},'full opened channel drain set')
    for channel,d in drains.items():equal(d,cleanup['drains'][channel],'actual sidecar/raw drain')
    if invalid:
        equal(calls,[],'invalid startup had no QMP/QTest operations')
        if (directory/'qtest-server.log').exists():equal((directory/'qtest-server.log').read_bytes(),b'','optional unopened server file empty')
    else:
        s=[];r=[];control=[]
        for line in (directory/'qtest-server.log').read_text().splitlines():
            match=re.fullmatch(r'\[([RSI]) \+?[0-9]+\.[0-9]+\] ?(.*)',line)
            need(match is not None,'complete server framing')
            role,body=match.groups();(s if role=='R' else r if role=='S' else control).append(body)
        equal(s,sent,'all server requests byte for byte');equal(r,replies,'all server replies/IRQ and drained tail')
        need(control in (['OPENED'],['OPENED','CLOSED']),'one owned QTest connection')
        equal(drains['qtest']['total_received_bytes'],sum(len(x.encode())+1 for x in replies),'complete transport received bytes')
    return calls,events,irq


def validate_cleanup(directory,name,c):
    normal=name in NORMAL;invalid=name in INVALID_NAMES
    equal(c['expected_exit_code'],0 if normal else 1,'literal expected exit')
    equal(c['exit_code'],c['expected_exit_code'],'actual exit');equal(c['actually_waited_and_reaped'],True,'wait/reap')
    for k in ('forced_terminate','forced_kill','signals_to_preexisting_processes'):equal(c[k],False,k)
    need(type(c['pid'])is int and c['pid']>0,'owned new process PID')
    equal(json.loads((directory/'child.json').read_bytes()),
        {'pid':c['pid'],'created_by_this_fixture':True,'signals_to_preexisting_processes':False},'whole child record')
    equal(len(c['wait_calls']),1 if normal else 2,'actual wait cardinality')
    for w in c['wait_calls']:
        equal(set(w),{'called','returned_exit_code','timeout_seconds'},'wait exact fields')
        equal(w['called'],'Popen.wait','real wait API');equal(w['returned_exit_code'],c['exit_code'],'wait exit')
        need(type(w['timeout_seconds'])is int and w['timeout_seconds']>0,'positive bounded wait')
    equal(c['quit_sent'],normal,'quit sent');equal(c['quit_acknowledged'],normal,'quit current ACK')
    equal(c['quit_error'],None,'no cleanup error');equal(c['quit_not_applicable'],None if normal else 'already-exited','quit route')
    equal(c['unexpected_qtest_drain_lines'],[],'no unparsed QTest tail')
    if not normal:equal(c['irq_events'],[],'negative load never publishes high staged IRQ, even in drained tail')
    equal(set(c['drains']),{'qmp','qtest'},'both opened/unopened channels explicitly accounted')
    for label,d in c['drains'].items():
        equal(d['channel'],label,'drain channel')
        if invalid:
            equal(d,{'channel':label,'not_opened':True,'terminal':None,'drain_bytes':0,
                     'reason':'not-connected-before-child-exit-or-tcg'},'unopened complete evidence')
            continue
        need('not_opened' not in d,'channel was opened')
        equal(d['error'],None,'no drain error');need(d['terminal']=='eof' or not normal and d['terminal']=='reset','complete terminal')
        raw=bytes.fromhex(d['drain_hex']);equal(len(raw),d['drain_bytes'],'full drain bytes')
        if label=='qmp':
            for line in raw.splitlines():
                v=json.loads(line);need('id' not in v and v.get('event') in ('SHUTDOWN','STOP','RESUME','MIGRATION','RESET'),
                                       'only complete known asynchronous tail events')
        else:
            need(not raw or raw.endswith(b'\n'),'complete QTest tail lines')
            need(all(x in ('IRQ raise 50','IRQ lower 50') for x in raw.decode().splitlines()),'only observed GIC50 tail')
    equal(set(c['counters']),set(COUNTERS),'exact full counter fields');equal(c['counters']['device_mmio'],0,'host does no device MMIO')
    equal(set(c['stdio']),{'qemu.stdout','qemu.stderr'},'two complete stdio files')
    for filename,p in c['stdio'].items():
        raw=(directory/filename).read_bytes()
        equal(p,{'bytes':len(raw),'sha256':sha(raw),'complete_after_actual_wait':True},'stdio after actual wait')
        if normal or filename=='qemu.stdout':equal(raw,b'','empty normal diagnostics/stdout')


class Flow:
    """Exhaustive ordered association of actual protocol with guest/file evidence."""
    def __init__(self,calls,events,irqs,root,name,mode,frequency,elf):
        self.calls,self.events,self.irqs=calls,events,irqs
        self.root,self.directory,self.name=root,root/name,name
        self.mode,self.frequency,self.elf=mode,frequency,elf
        self.i=0;self.intercepted=False
    def next(self,kind):
        need(self.i<len(self.calls),'missing actual operation in '+self.name)
        c=self.calls[self.i];self.i+=1;equal(c['kind'],kind,'literal protocol operation kind');return c
    def counters(self):
        return dict(self.calls[self.i-1]['after']) if self.i else {k:0 for k in COUNTERS}
    def q(self,execute,args=None,error=False):
        c=self.next('qmp');request={'execute':execute,'id':c['request']['id']}
        if args is not None:request['arguments']=args
        equal(c['request'],request,'literal QMP request')
        if error:
            equal(set(c['reply']),{'id','error'},'complete QMP error')
            equal(c['reply']['error']['class'],'GenericError','actual immutable-property rejection')
            need(type(c['reply']['error']['desc'])is str and c['reply']['error']['desc'],'full QMP error description')
            return c['reply']
        equal(set(c['reply']),{'id','return'},'complete successful QMP response')
        return c['reply']['return']
    def qt(self,command):
        c=self.next('qtest');equal(c['request'],command,'literal QTest request');return c['reply'].split()[1:]
    def memory(self,address,size,data=None):
        command=(f'read {address:#x} {size:#x}' if data is None else
                 f'write {address:#x} {size:#x} 0x{data.hex()}')
        words=self.qt(command)
        if data is not None:
            equal(len(data),size,'whole RAM write size');equal(words,[],'RAM write ACK');return None
        need(len(words)==1 and words[0].startswith('0x'),'complete RAM read encoding')
        raw=bytes.fromhex(words[0][2:]);equal(len(raw),size,'complete RAM read bytes');return raw
    def stopped(self):
        result=self.q('query-status');equal(result['running'],False,'actual stopped status');return result
    def pure(self,claim,cold=False):
        before=self.counters();states=[]
        for _ in range(2):
            cpus=[]
            for cpu in range(8):
                cpus.append({p:self.q('qom-get',{'path':f'/machine/cpu{cpu}','property':p})
                    for p in (CPU_PROP,PROFILE_PROP,FREQUENCY_PROP)})
            equal(cpus,[pure_cpu_expected(self.mode,self.frequency)]*8,'all eight exact typed CPU profiles')
            shared=({p:self.q('qom-get',{'path':PATH,'property':p}) for p in GETTERS} if self.mode else None)
            if self.mode:validate_shared_shape(shared,self.frequency,cold=cold)
            states.append({'cpus':cpus,'shared':shared})
        equal(states[0],states[1],'all pure fields unchanged across both complete passes')
        after=self.counters()
        equal(after['qom_get']-before['qom_get'],104 if self.mode else 48,'full pure getter cardinality')
        for k in COUNTERS:
            if k!='qom_get':equal(after[k],before[k],'no other pure-observation operation '+k)
        equal(claim,{'first':states[0],'second':states[1],'before':before,'after':after},'raw two-pass pure-state association')
        return states[0]['shared']
    def intercept(self):
        need(self.mode and not self.intercepted,'single enabled shared-bank IRQ observer')
        equal(self.qt('irq_intercept_in /machine/gic'),[],'GIC input observer preserves guest delivery')
        self.intercepted=True
    def fence(self,claim):
        need(self.intercepted,'actual input observer installed')
        equal(self.qt('endianness'),['little'],'IRQ synchronization fence')
        n=self.calls[self.i-1]['irq_after'];events=self.irqs[:n];level=False
        for event in events:
            equal(event['number'],50,'observed SPI50 input')
            new=event['action']=='raise';need(new!=level,'literal IRQ transitions, not fabricated duplicate levels');level=new
        actual={'line':level,'events':events};equal(claim,actual,'all actual IRQ events through fence');return actual
    def zero(self):
        self.memory(CONTROL,CONTROL_BYTES,bytes(CONTROL_BYTES))
        for cpu in range(8):self.memory(BOX_BASE+cpu*BOX_STRIDE,BOX_BYTES,bytes(BOX_BYTES))
    def snapshot(self,claim,epoch,recipe,label):
        self.stopped();control=self.memory(CONTROL,CONTROL_BYTES)
        equal(struct.unpack('<16Q',control),(epoch,NONCE|epoch,recipe,16 if recipe==2 else 8,self.frequency)+(0,)*11,
              'full actual control and eight successful PSCI CPU_ON results')
        cp={'file':label+'-control.bin','bytes':CONTROL_BYTES,'sha256':sha(control)}
        equal(read_file(self.directory,cp['file']),control,'complete saved control bytes equal raw reply')
        cpus=[];files=[]
        for cpu in range(8):
            raw=self.memory(BOX_BASE+cpu*BOX_STRIDE,BOX_BYTES);filename=f'{label}-cpu{cpu}.bin'
            equal(read_file(self.directory,filename),raw,'complete mailbox file equals actual guest RAM')
            files.append({'file':filename,'bytes':len(raw),'sha256':sha(raw)})
            cpus.append(decode_mailbox(raw,cpu,self.mode,epoch,recipe,self.elf,self.frequency))
        actual={'label':label,'mode':self.mode,'frequency_hz':self.frequency,'epoch':epoch,'recipe':recipe,
                'control':cp,'files':files,'cpus':cpus}
        equal({k:claim[k] for k in actual},actual,'full mailbox report independently decoded from actual RAM')
        return actual
    def epoch(self,claim,epoch,recipe,cold=False,before=None):
        self.stopped()
        if cold:self.zero()
        control=struct.pack('<16Q',epoch,NONCE|epoch,recipe,0,self.frequency,*([0]*11))
        self.memory(CONTROL,CONTROL_BYTES,control);equal(self.q('cont'),{},'one actual guest epoch starts')
        polls=[]
        while True:
            batch=[]
            for cpu in range(8):
                words=struct.unpack('<32Q',self.memory(BOX_BASE+cpu*BOX_STRIDE,256))
                equal(words[10],0,'every actual guest poll has no fatal flag')
                batch.append({'cpu':cpu,'epoch':words[7],'recipe':words[8],'count':words[9],
                              'fatal':words[10],'done':words[12]})
            polls.append(batch)
            if all(x['done']==epoch for x in batch):break
            need(len(polls)<30000,'bounded complete poll sequence')
        equal(claim['polls'],polls,'every actual eight-CPU header poll')
        equal(self.q('stop'),{},'stop before full mailbox and pure observation');self.stopped()
        state=self.pure(claim['after'])
        actual=self.snapshot(claim,epoch,recipe,'epoch-'+str(epoch))
        semantic=verify_epoch(actual['cpus'],self.mode,recipe,self.frequency,before=before,after=state)
        equal(claim['semantic'],semantic,'literal instruction and integer-time semantics from raw bytes')
        if self.mode:
            irq=self.fence(claim['irq']);equal(irq['line'],state['irq-level'],'actual GIC input equals derived bank IRQ')
        else:equal(claim['irq'],None,'disabled fixture has no invented IRQ observations')
        if recipe==4:
            equal(claim['irq']['events'],[{'action':a,'number':50} for _ in range(3) for a in ('raise','lower')],
                  'software32 and natural32/64 complete actual IRQ pulses')
        equal(set(claim),set(actual)|{'polls','after','semantic','irq'},'complete epoch report shape')
        return actual
    def migration(self,incoming,path,claim):
        arguments={'channels':[{'channel-type':'main','addr':{'transport':'file','filename':str(path.resolve()),'offset':0}}]}
        equal(self.q('migrate-incoming' if incoming else 'migrate',arguments),{},'actual complete file migration request')
        progress=0
        while True:
            value=self.q('query-migrate');progress+=1
            need(value.get('status') not in ('failed','cancelled') and progress<30000,'complete bounded migration progress')
            if value.get('status')=='completed':break
        equal(claim,value,'actual completed migration status')
    def incoming(self,claim,path,wire):
        self.pure(claim['before'],cold=True)
        if self.mode:
            self.intercept();equal(self.qt('endianness'),['little'],'cold incoming IRQ fence')
            equal(self.calls[self.i-1]['irq_after'],0,'cold destination actual IRQ remains low')
        equal(claim['before_load_counters'],self.counters(),'actual pre-load counters')
        self.migration(True,path,claim['migration'])
        state=self.pure(claim['after'])
        equal(self.counters()['cont'],0,'no guest execution before complete post-load pure observation')
        equal(self.counters()['ram'],0,'no RAM read before complete post-load pure observation')
        self.stopped()
        if self.mode:
            irq=self.fence(claim['irq']);compare_state_wire(state,wire)
            equal(state['incoming-started-cpus'],255,'all eight incoming sections started')
            equal(state['incoming-completed-cpus'],255,'all eight incoming CPU profiles validated')
            equal(state['migration-quarantined'],False,'successful bank committed after CPU7')
            equal(irq['line'],state['irq-level'],'actual restore IRQ line')
            equal(irq['events'],[{'action':'raise','number':50}] if state['irq-level'] else [],'single valid IRQ publication')
        else:equal(claim['irq'],None,'disabled incoming has no bank IRQ')
        equal(claim['cont_before_postload_pure'],0,'reported first pure guest count')
        equal(claim['ram_before_postload_pure'],0,'reported first pure RAM count')
        equal(set(claim),{'before','after','migration','before_load_counters','cont_before_postload_pure',
                         'ram_before_postload_pure','irq'},'incoming report shape')
        return state
    def save(self,claim,path):
        self.stopped();before=self.pure(claim['state_before'])
        self.migration(False,path,claim['migration'])
        raw=read_file(self.root,claim['file']);wire=parse_stream(raw,self.mode,self.frequency)
        equal(claim['wire'],wire,'whole canonical migration stream and all CPU fields')
        after=self.pure(claim['state_after']);equal(before,after,'paused bank unchanged by whole save')
        if self.mode:compare_state_wire(after,wire)
        equal({k:claim[k] for k in ('file','kind','mode','frequency_hz')},
              {'file':'streams/'+path.name,'kind':'canonical','mode':self.mode,'frequency_hz':self.frequency},'canonical stream identity')
        equal(set(claim),{'file','kind','mode','frequency_hz','migration','wire','state_before','state_after'},'complete canonical stream fields')
        return raw,wire
    def finish(self,normal):
        if normal:equal(self.q('quit'),{},'current actual quit ACK')
        equal(self.i,len(self.calls),'exhausted every actual operation in '+self.name)


def command_identity(directory,name,qemu,root):
    command=json.loads(read_file(directory,'command.json'))
    need(type(command)is list and all(type(x)is str for x in command),'complete literal argv')
    mode=True;frequency=FREQUENCY;specified=True;ras=False;accel='tcg';cpu=None
    if name in tuple(x[0] for x in PURE):
        _,specified,frequency=next(x for x in PURE if x[0]==name);mode=specified is True;accel='qtest'
    elif name in tuple(x[0] for x in FUNCTIONAL):
        _,specified,_,frequency,ras=next(x for x in FUNCTIONAL if x[0]==name);mode=specified is True
    elif name in INVALID_NAMES:
        _,cpu,frequency=next(x for x in INVALID if x[0]==name)
    elif name in ('off-source','off-target','off-return','cross-on-to-off'):mode=specified=False
    elif name=='cross-frequency':frequency=ALTERNATE
    machine='tb371fc,counter-exact=on'
    if specified is not None:machine+=',dsu-pmu='+('on' if specified else 'off')
    machine+=',dsu-pmu-frequency-hz='+str(frequency)
    if ras:machine+=',ras-zero-records-compat=on'
    if cpu:machine='none'
    endpoint=command[command.index('-qmp')+1]
    match=re.fullmatch(r'unix:(.+)/m,server=on,wait=off',endpoint);need(match is not None,'owned private QMP socket')
    expected=[qemu,'-machine',machine,'-accel','qtest' if accel=='qtest' else 'tcg,thread=single',
        '-m','128M','-smp','1' if cpu else '8','-S','-display','none','-monitor','none',
        '-serial','null','-serial','null','-d','guest_errors,invalid_mem','-qmp',endpoint,
        '-qtest',f'unix:{match.group(1)}/t,server=on,wait=off','-qtest-log',str(directory/'qtest-server.log')]
    if cpu:expected+=['-cpu',cpu+','+CPU_PROP+'=on,has_el2=on,has_el3=off']
    else:expected+=['-device','loader,file='+str(root/'build/probe.elf')+',cpu-num=0']
    incoming=name.endswith(('-target','-return')) or name not in NORMAL+INVALID_NAMES
    if incoming:expected+=['-incoming','defer']
    equal(command,expected,'exact whole command, virtual profile and guest ELF')
    return mode,frequency,incoming


def validate_qom_access(f,claim):
    subjects=[(f'/machine/cpu{i}',{CPU_PROP:'bool',PROFILE_PROP:'uint32',FREQUENCY_PROP:'uint64'}) for i in range(8)]
    if f.mode:subjects.append((PATH,{**{k:'uint64' for k in UINT_GETTERS},**{k:'bool' for k in BOOL_GETTERS},
                                   **{k:'string' for k in HEX_GETTERS}}))
    types=[];rejected=[]
    for path,wanted in subjects:
        rows=f.q('qom-list',{'path':path})
        need(type(rows)is list and len(rows)==len({x['name'] for x in rows}),'complete unique QOM property metadata')
        mapping={x['name']:x['type'] for x in rows}
        equal({k:mapping.get(k) for k in wanted},wanted,'actual exact metadata types')
        types.append({'path':path,'types':wanted})
        for prop,typ in wanted.items():
            arg={'path':path,'property':prop,'value':False if typ=='bool' else '' if typ=='string' else 0}
            response=f.q('qom-set',arg,error=True);rejected.append({'arguments':arg,'response':response})
    rows=f.q('qom-list',{'path':'/machine'})
    equal(sum(x['name']=='dsu-pmu-device' for x in rows),int(f.mode),'bank child absent with default/explicit off')
    equal(claim['machine_children'],rows,'complete machine child list')
    for prop,value in (('dsu-pmu',not f.mode),('dsu-pmu-frequency-hz',f.frequency+1)):
        arg={'path':'/machine','property':prop,'value':value}
        response=f.q('qom-set',arg,error=True);rejected.append({'arguments':arg,'response':response})
    equal(claim['types'],types,'full typed metadata report');equal(claim['rejected'],rejected,'every actual immutable-property error')
    f.pure(claim['unchanged'],cold=True)
    equal(set(claim),{'types','rejected','machine_children','unchanged'},'QOM metadata report shape')


def mutation_bytes(original,wire,spec):
    """Reconstruct a finite bad stream; preserve every unrelated original byte."""
    name,kind,cpu,relative,width,value,_=spec
    row=wire['cpu_sections'][cpu];sub=next(x for x in row['subsections'] if x['name']==SUBSECTION)
    if kind=='word':start=sub['body_start']+relative;end=start+width;replacement=value.to_bytes(width,'big')
    elif kind=='version':start=sub['body_start']-4;end=start+4;replacement=value.to_bytes(4,'big')
    elif kind in ('absent','duplicate'):
        start,end=sub['start'],sub['end'];replacement=b'' if kind=='absent' else original[start:end]*2
    elif kind=='truncate':start=sub['body_start']+131;end=len(original);replacement=b''
    elif kind=='repeat-cpu0':start,end=row['section_start'],row['section_end'];replacement=original[start:end]*2
    else:
        equal(kind,'swap-cpu01','known structural mutation kind');a,b=wire['cpu_sections'][:2]
        need(a['section_end']<=b['section_start'],'canonical ordered ARM sections with possible CPU-common gap')
        start,end=a['section_start'],b['section_end']
        gap=original[a['section_end']:b['section_start']]
        replacement=original[b['section_start']:b['section_end']]+gap+original[a['section_start']:a['section_end']]
    old=original[start:end];need(old!=replacement,'actual finite mutation changes bytes')
    modified=original[:start]+replacement+original[end:]
    meta={'name':name,'kind':kind,'cpu':cpu,'relative':relative,'width':width,'value':value,
          'offset':start,'old_bytes':len(old),'old_sha256':sha(old),'new_bytes':len(replacement),'new_sha256':sha(replacement),
          'original_bytes':len(original),'original_sha256':sha(original),'bytes':len(modified),'sha256':sha(modified)}
    return modified,meta


def validate_rejection(f,row,path,needle,cleanup):
    equal(row['mode'],f.mode,'rejected destination mode');equal(row['frequency_hz'],f.frequency,'rejected destination frequency')
    equal(row['expected_exit_code'],1,'literal expected failed load');f.pure(row['initial_state'],cold=True)
    if f.mode:
        f.intercept();actual=f.fence(row['initial_irq']);equal(actual,{'line':False,'events':[]},'cold failure starts with IRQ low')
    else:equal(row['initial_irq'],None,'off target no IRQ observer')
    counters=f.counters();equal(row['before_load_counters'],counters,'actual negative pre-load counters')
    equal(row['irq_event_index'],f.calls[f.i-1]['irq_after'],'exact observed prefix before failed incoming')
    args={'channels':[{'channel-type':'main','addr':{'transport':'file','filename':str(path.resolve()),'offset':0}}]}
    equal(f.q('migrate-incoming',args),{},'submitted actual bad stream')
    equal(row['incoming_file'],'streams/'+path.name,'negative whole-stream source identity')
    equal(row['diagnostic_needle'],needle,'expected exact diagnostic branch')
    stderr=read_file(f.directory,'qemu.stderr').decode()
    need(needle in stderr and 'load of migration failed' in stderr,'actual full diagnostic from expected rejected incoming')
    equal(row['actual_exit_code'],1,'actual failed incoming exit')
    equal(row['complete_postload_irq_events'],[],'entire failed incoming IRQ suffix including final drain')
    equal(cleanup['irq_events'],[],'failed stream never publishes its saved high IRQ')
    for k in ('cont','ram','memory'):equal(f.counters()[k],counters[k],'negative load no guest or memory operation '+k)
    f.finish(False)


def judge_dsu_pmu_details(case,data):
    if case.name!=NAME:return
    for key,wanted in EXPECTED_VALUES.items():equal(data.get(key),wanted,key)
    equal(data.get('passed'),True,'actual gate verdict')
    for key,size in EXPECTED_LENGTHS.items():
        need(type(data.get(key))is list and len(data[key])==size,'complete '+key)
    equal(set(data),set(EXPECTED_VALUES)|set(EXPECTED_LENGTHS)|{'passed','qemu_sha256','probe_elf','actual_files'},
          'complete successful report fields, no hidden failure or omitted evidence')
    equal([x['fixture'] for x in data['checks']],list(ORDER),'literal complete 54-fixture order')
    equal([x['fixture'] for x in data['processes']],list(ORDER),'literal complete cleanup order')
    for row in data['checks']:
        equal(row.get('name'),row['fixture'],'check identity');equal(row.get('passed'),True,'actual individual verdict')
    root=Path(case.result).resolve().parent;repo=Path(__file__).resolve().parents[2]
    qemu=str(Path(case.command[case.command.index('--qemu')+1]).resolve())
    equal(sha(Path(qemu).read_bytes()),data['qemu_sha256'],'full compiled QEMU bytes')
    elf=parse_probe_elf(read_file(root,'build/probe.elf'))
    equal(data['probe_elf'],elf,'whole actual ELF and every exact system instruction')
    compile_evidence(root,repo,elf)
    source_names=['tb371fc/tests/dsu_pmu/'+n for n in ('start.S','probe.c','link.ld')]+[
        'tb371fc/tests/'+n for n in ('dsu_pmu_test_contract.py','dsu_pmu_test_transport.py','test_dsu_pmu.py','qce_bam_test_transport.py')]
    sources=[]
    for name in source_names:
        raw=(repo/name).read_bytes();sources.append({'relative_path':name,'bytes':len(raw),'sha256':sha(raw)})
    equal(data['source_files'],sources,'seven complete source files, not summary pins alone')
    inventory={}
    for row in data['actual_files']:
        file=row['file'];need(file not in inventory,'unique full-file pin');read_file(root,file,row);inventory[file]=row
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.resolve()!=Path(case.result).resolve()}
    equal(set(inventory),actual,'all actual complete artifact files accounted')
    equal([x['file'] for x in data['actual_files']],sorted(actual),'ordered complete full-file inventory')
    equal({p.name for p in root.iterdir() if p.is_dir()},set(ORDER)|{'build','streams'},'exact owned fixture directories')
    checks={x['fixture']:x for x in data['checks']};cleanups={x['fixture']:x for x in data['processes']}
    flows={};all_calls=[]
    for name in ORDER:
        directory=root/name;c=json.loads(read_file(directory,'cleanup.json'))
        equal(cleanups[name],{'fixture':name,**c},'complete cleanup raw/report equality');validate_cleanup(directory,name,c)
        mode,frequency,_=command_identity(directory,name,qemu,root)
        calls,events,irq=raw_protocol(directory,c,name in INVALID_NAMES);all_calls+=calls
        f=Flow(calls,events,irq,root,name,mode,frequency,elf);flows[name]=f
        if name not in INVALID_NAMES:equal(f.q('qmp_capabilities'),{},'QMP negotiation before any fixture operation')
    equal(sum(len(c['wait_calls']) for c in cleanups.values()),90,'all actual 90 Popen.wait results')
    equal(sum(c['exit_code']==0 for c in cleanups.values()),18,'18 actual normal exits')
    equal(sum(c['exit_code']==1 for c in cleanups.values()),36,'36 actual expected startup/load failures')
    epochs=[];actual_epochs=[]
    def append_epoch(name,record):
        epochs.append({'fixture':name,'epoch':record['epoch'],'recipe':record['recipe']});actual_epochs.append(record)
    for name,mode,frequency in PURE:
        f=flows[name];row=checks[name]
        equal(row['mode'],mode is True,'pure fixture mode');equal(row['frequency_hz'],frequency,'pure fixture explicit reference rate')
        equal(row['accel'],'qtest','pure fixture accelerator');equal(row['epochs'],[],'no guest epochs for frequency boundaries')
        f.pure(row['initial_state'],cold=True);validate_qom_access(f,row['qom_access'])
        equal(f.counters()['cont'],0,'pure boundary never continues guest');equal(f.counters()['ram'],0,'pure boundary has no RAM access')
        equal(set(row),{'fixture','mode','frequency_hz','accel','epochs','initial_state','qom_access','name','passed'},'pure check shape')
        f.finish(True)
    for name,mode,recipe,frequency,ras in FUNCTIONAL:
        f=flows[name];row=checks[name]
        equal((row['mode'],row['frequency_hz'],row['accel'],row['ras']),(mode is True,frequency,'tcg',ras),'functional literal identity')
        f.pure(row['initial_state'],cold=True)
        if mode:
            f.intercept();equal(f.fence(row['initial_irq']),{'line':False,'events':[]},'cold actual functional IRQ low')
        else:equal(row['initial_irq'],None,'off functional observer absent')
        if ras:
            profiles=[{p:f.q('qom-get',{'path':f'/machine/cpu{i}','property':p})
                for p in ('tb371fc-ras-zero-records-compat','tb371fc-ras-zero-records-profile')} for i in range(8)]
            equal(profiles,[{'tb371fc-ras-zero-records-compat':True,'tb371fc-ras-zero-records-profile':1}]*8,'independent existing RAS profile intact')
            equal(row['ras_profiles'],profiles,'all actual RAS coexistence getters')
        equal(len(row['epochs']),2 if name=='shared-reset' else 1,'literal functional epoch cardinality')
        e=f.epoch(row['epochs'][0],1,recipe,cold=True);append_epoch(name,e)
        fields={'fixture','mode','frequency_hz','accel','ras','initial_state','epochs','initial_irq','name','passed'}
        if name=='shared-reset':
            f.zero();index=f.calls[f.i]['index'];equal(f.q('system_reset'),{},'actual whole-machine reset request');f.stopped()
            # The producer queried status before recording this flag; bind it to
            # an event already consumed by that response, not a later tail.
            need(any(index<x['index'] and x['index']<f.calls[f.i]['index'] and x['value'].get('event')=='RESET' for x in f.events),
                 'RESET event precedes the next pure observation')
            equal(row['reset_event_seen'],True,'report RESET flag');f.pure(row['reset_state'],cold=True)
            equal(f.fence(row['reset_irq'])['line'],False,'whole-bank reset deasserts real IRQ')
            e=f.epoch(row['epochs'][1],2,1);append_epoch(name,e)
            fields|={'reset_event_seen','reset_state','reset_irq'}
        if ras:fields.add('ras_profiles')
        equal(set(row),fields,'complete functional check fields');f.finish(True)
    for name,cpu,frequency in INVALID:
        row=checks[name];f=flows[name]
        needle=('tb371fc-dsu-pmu requires the TB371FC A55/A77 shared software-emulated bank, EL2 on and EL3 off'
                if cpu else 'dsu-pmu-frequency-hz requires 1..1000000000 before machine creation')
        equal(row,{'fixture':name,'actual_exit_code':1,'diagnostic_needle':needle,'name':name,'passed':True},'configuration rejection report')
        need(needle in read_file(f.directory,'qemu.stderr').decode(),'full actual invalid-profile diagnostic')
        f.finish(False)
    streams={x['file']:x for x in data['streams']};equal(len(streams),33,'33 unique complete streams')
    equal({p.name for p in (root/'streams').iterdir()},{Path(k).name for k in streams},'whole stream file set')
    saved={};loops=[]
    for mode,label in ((True,'on'),(False,'off')):
        loop={'mode':mode,'fixtures':[],'streams':[],'incoming':[]};prior=None;prior_epoch=None
        for leg,suffix in enumerate(('-source','-target','-return')):
            name=label+suffix;row=checks[name];f=flows[name];loop['fixtures'].append(name)
            equal((row['mode'],row['frequency_hz'],row['leg']),(mode,FREQUENCY,leg),'roundtrip exact fixture identity')
            fields={'fixture','mode','frequency_hz','leg','epochs','name','passed'}
            if leg==0:
                f.pure(row['initial_state'],cold=True)
                if mode:
                    f.intercept();equal(f.fence(row['initial_irq']),{'line':False,'events':[]},'source IRQ cold')
                else:equal(row['initial_irq'],None,'off source no bank')
                fields|={'initial_state','initial_irq'}
            else:
                state=f.incoming(row['incoming'],root/prior['file'],prior['wire'])
                loop['incoming'].append({'fixture':name,**row['incoming']})
                last_epoch=1 if leg==1 else (3 if mode else 2);last_recipe=5 if mode else 1
                restored=f.snapshot(row['restored_guest'],last_epoch,last_recipe,'restored')
                equal(set(row['restored_guest']),set(restored),'complete restored snapshot shape')
                equal(restored['cpus'],prior_epoch['cpus'],'all complete prior guest records restored before continuing')
                equal(restored['control']['sha256'],prior_epoch['control']['sha256'],'whole restored control bytes')
                equal([x['sha256'] for x in restored['files']],[x['sha256'] for x in prior_epoch['files']],
                      'all eight full 53312-byte mailboxes unchanged by transport')
                fields|={'incoming','restored_guest'}
            equal(len(row['epochs']),2 if mode and leg==1 else 1,'literal continuation epoch count')
            if mode:
                e=(f.epoch(row['epochs'][0],1,5,cold=True) if leg==0 else
                   f.epoch(row['epochs'][0],2 if leg==1 else 4,6,before=state))
            else:e=f.epoch(row['epochs'][0],leg+1,1,cold=leg==0)
            append_epoch(name,e)
            if mode and leg==1:
                e=f.epoch(row['epochs'][1],3,5);append_epoch(name,e)
            if leg<2:
                filename='streams/'+label+('-initial.mig' if leg==0 else '-return.mig');record=streams[filename]
                equal(row['outgoing'],filename,'saved stream association');raw,wire=f.save(record,root/filename)
                loop['streams'].append(filename);prior=record;prior_epoch=e;fields.add('outgoing')
                if leg==0:saved[label]=(raw,wire)
            equal(set(row),fields,'complete roundtrip check fields');f.finish(True)
        loops.append(loop)
    equal(data['roundtrips'],loops,'two whole roundtrips with four incoming validations')
    equal(data['guest_epochs'],epochs,'all sixteen fresh guest execution epochs')
    equal(sum(sum(c['instruction_operations'] for c in e['cpus']) for e in actual_epochs),13061,'all actual guest system-register events')
    equal(sum(sum(len(c['irq_events']) for c in e['cpus']) for e in actual_epochs),3,'three guest GIC82 handlers')
    for cpu in range(8):
        old=saved['off'][1]['cpu_sections'][cpu]['cpreg_ids'];new=saved['on'][1]['cpu_sections'][cpu]['cpreg_ids']
        equal(new,[v for v in old if v!=0x603000000013e081],'NO_RAW is only the dedicated AA64 ACTLR_EL2 difference')
    rejected=[]
    for name,label,mode,frequency,needle in (
        ('cross-on-to-off','on',False,FREQUENCY,'0 != 1'),
        ('cross-off-to-on','off',True,FREQUENCY,'Incoming CPU DSU PMU mode differs from destination'),
        ('cross-frequency','on',True,ALTERNATE,'124f800 != 5f5e100')):
        row=checks[name];validate_rejection(flows[name],row,root/'streams'/(label+'-initial.mig'),needle,cleanups[name]);rejected.append(row)
    original,wire=saved['on']
    for spec in MUTATIONS:
        name=spec[0];raw,mutation=mutation_bytes(original,wire,spec);filename='streams/'+name+'.mig'
        equal(read_file(root,filename),raw,'whole finite mutant and every unchanged byte')
        equal(streams[filename],{'file':filename,'kind':'finite-invalid-mutation','bytes':len(raw),'sha256':sha(raw),'mutation':mutation},
              'independent complete finite mutation reconstruction')
        row=checks[name];equal(row['mutation'],mutation,'actual rejected stream mutation identity')
        validate_rejection(flows[name],row,root/filename,spec[-1],cleanups[name]);rejected.append(row)
    equal(data['rejected_migrations'],rejected,'all 32 failed loads tied to exact source streams')
    rejection_fields={'fixture','mode','frequency_hz','incoming_file','diagnostic_needle','expected_exit_code',
        'initial_state','initial_irq','before_load_counters','irq_event_index','actual_exit_code','complete_postload_irq_events','name','passed'}
    for row in rejected:equal(set(row),rejection_fields|({'mutation'} if row['fixture'] in tuple(x[0] for x in MUTATIONS) else set()),'failed load complete fields')
    need(all(f.i==len(f.calls) for f in flows.values()),'all actual protocol sequences exhausted')
    qmp=[c['request']['execute'] for c in all_calls if c['kind']=='qmp']
    equal(qmp.count('cont'),16,'sixteen actual guest epochs')
    equal(qmp.count('migrate'),4,'four actual canonical whole saves')
    equal(qmp.count('migrate-incoming'),36,'four successful and thirty-two failed loads')
    equal(qmp.count('system_reset'),1,'one whole-bank reset')


