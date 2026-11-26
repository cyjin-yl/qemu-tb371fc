#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DSU compiled-test ABI and independent primary-derived literal expectations.

This module performs no I/O, process launch, producer/model import, or work on
import. Timing bounds use actual generic-counter brackets and integer units.
The virtual frequency is an explicit model contract, not a hardware capture.
"""
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

