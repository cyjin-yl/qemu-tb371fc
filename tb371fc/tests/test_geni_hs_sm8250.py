#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent compiled acceptance for the optional SM8250 GENI HS UART.

The value oracle is the declared reference contract, pinned public register
layout, and bounded captured-header/producer facts. It never reads the device C
source or a generated include to discover expected values. Real QEMU processes,
QTest transactions, two independent socket peers, QMP and complete migration
streams provide the observations. No guest CPU, native kernel, firmware
execution, physical reset value, DMA transfer, Bluetooth peer or Android success
is inferred. Importing this module performs no emulator operation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import time
import traceback

from geni_hs_test_transport import (HsRig, digest, write_json, wait_migration,
                                    file_channels, HS_BASE, OLD_BASE, WRAPPER, GCC, RAM)

if not __debug__:
    raise SystemExit('Acceptance requires assertions; do not use Python -O.')

TYPE, DEVICE, LEGACY = 'qcom-sm8250-geni-hs', '/machine/uart-hs', '/machine/uart'
DEFAULT_MACHINE = 'tb371fc,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off'
IDENTITY, FW_REVISION, WRAPPER_VERSION = 0x47485331, (2 << 8) | 1, 0x20050000
FIFO_BYTES, REG_COUNT, GUARD_BYTES, LEGACY_BODY_BYTES, VMSTATE_BYTES = 64, 4096, 48, 16534, 16608
DYNAMIC_STR = ('registers-hex', 'tx-fifo-hex', 'rx-fifo-hex')
DYNAMIC_UINT = ('tx-count', 'rx-count', 'tx-remaining', 'tx-sent', 'rx-received',
                'se-clock-period', 'm-ahb-clock-period', 's-ahb-clock-period', 'fifo-bytes')
DYNAMIC_BOOL = ('tx-active', 'rx-active', 'tx-zero-pending', 'reset-held', 'irq-level')
DYNAMIC_FIELDS = DYNAMIC_STR + DYNAMIC_UINT + DYNAMIC_BOOL
IDENTITY_UINT = ('reference-identity', 'physical-base', 'window-bytes',
                 'wrapper-address', 'wrapper-window-bytes', 'main-fw-revision',
                 'secondary-fw-revision', 'wrapper-version', 'vmstate-bytes', 'profile-wire-bytes')
IDENTITY_BOOL = ('enabled-reference', 'firmware-executed', 'dma-modeled',
                 'physical-state-captured', 'dma-mask-modeled')
IDENTITY_FIELDS = IDENTITY_UINT + IDENTITY_BOOL
FIRST_GETTERS = 49

STATUS, FW_MAIN, FW_SECONDARY, DMA_MODE = 0x40, 0x68, 0x6c, 0x258
TX_LEN = 0x270
M_CMD, M_CTRL, M_STATUS, M_EN, M_CLEAR, M_EN_SET, M_EN_CLEAR = (
    0x600, 0x604, 0x610, 0x614, 0x618, 0x61c, 0x620)
S_CMD, S_CTRL, S_STATUS, S_EN, S_CLEAR, S_EN_SET, S_EN_CLEAR = (
    0x630, 0x634, 0x640, 0x644, 0x648, 0x64c, 0x650)
TX_FIFO, RX_FIFO, TX_STATUS, RX_STATUS = 0x700, 0x780, 0x800, 0x804
TX_WM, RX_WM, RX_RFR_WM, GLOBAL_EN = 0x80c, 0x810, 0x814, 0xe1c
TX_TOTAL, RX_TOTAL = 0x910, 0x914
TX_DMA_EN, TX_DMA_SET, TX_DMA_CLEAR = 0xc48, 0xc4c, 0xc50
RX_DMA_EN, RX_DMA_SET, RX_DMA_CLEAR = 0xd48, 0xd4c, 0xd50
START = 1 << 27
DONE, OVERRUN, ILLEGAL, FAILURE, CANCEL, ABORT = (1 << n for n in range(6))
RX_READ_ERR, RX_WATERMARK, RX_LAST, TX_WRITE_ERR, TX_WATERMARK = (
    1 << n for n in (24, 26, 27, 29, 30))
M_COLD, S_COLD = 0x0cc00030, 0x0c000030
HS_VOTE, HS_RESET = GCC + 0x52008, GCC + 0x17000
OLD_VOTE, OLD_RESET = GCC + 0x52010, GCC + 0x1e000
HS_SE, HS_M_AHB, HS_S_AHB = 1 << 16, 1 << 6, 1 << 7
OLD_SE = 1 << 8
PERIOD_UNIT = 1_000_000_000 << 32


def identity_expected(reference):
    return {'reference-identity': IDENTITY, 'physical-base': HS_BASE, 'window-bytes': 0x4000,
            'wrapper-address': WRAPPER, 'wrapper-window-bytes': 4,
            'main-fw-revision': FW_REVISION, 'secondary-fw-revision': FW_REVISION,
            'wrapper-version': WRAPPER_VERSION, 'vmstate-bytes': VMSTATE_BYTES,
            'profile-wire-bytes': GUARD_BYTES, 'enabled-reference': bool(reference),
            'firmware-executed': False, 'dma-modeled': False,
            'physical-state-captured': False, 'dma-mask-modeled': True}


def validate_engine(state):
    assert set(state) == set(DYNAMIC_FIELDS)
    assert all(type(state[k]) is int and 0 <= state[k] < (1 << 64) for k in DYNAMIC_UINT)
    assert all(type(state[k]) is bool for k in DYNAMIC_BOOL)
    for name, size in (('registers-hex', 4 * REG_COUNT), ('tx-fifo-hex', FIFO_BYTES),
                       ('rx-fifo-hex', FIFO_BYTES)):
        assert type(state[name]) is str and re.fullmatch('[0-9a-f]+', state[name])
        assert len(state[name]) == size * 2
    assert state['fifo-bytes'] == FIFO_BYTES
    assert state['tx-count'] <= FIFO_BYTES and state['rx-count'] <= FIFO_BYTES
    assert state['tx-count'] <= state['tx-remaining']


def engine_world(r, engine='hs'):
    path = DEVICE if engine == 'hs' else LEGACY
    state = {name: r.qom(name, path) for name in DYNAMIC_FIELDS}
    validate_engine(state)
    return state


def world(r):
    before = r.counters()
    hs = engine_world(r)
    identity = {name: r.qom(name) for name in IDENTITY_FIELDS}
    assert all(type(identity[k]) is int for k in IDENTITY_UINT)
    assert all(type(identity[k]) is bool for k in IDENTITY_BOOL)
    assert identity == identity_expected(r.reference), identity
    legacy = engine_world(r, 'legacy')
    after = r.counters()
    assert after['qom_get'] - before['qom_get'] == FIRST_GETTERS
    assert {k:v for k,v in before.items() if k != 'qom_get'} == {
        k:v for k,v in after.items() if k != 'qom_get'}
    return {'hs': hs, 'identity': identity, 'legacy': legacy}


def registers(state):
    return struct.unpack('>4096I', bytes.fromhex(state['registers-hex']))


def register(state, offset):
    assert offset % 4 == 0 and 0 <= offset < 0x4000
    return registers(state)[offset // 4]


def gate(r, mask, enabled, *, legacy=False):
    address = OLD_VOTE if legacy else HS_VOTE
    value = r.read_absolute(address)
    r.write_absolute(address, value | mask if enabled else value & ~mask)


def tx(r, data, engine='hs'):
    assert isinstance(data, bytes)
    r.write(TX_LEN, len(data), engine=engine)
    r.write(M_CMD, START, engine=engine)
    for off in range(0, len(data), 4):
        r.write(TX_FIFO, int.from_bytes(data[off:off + 4], 'little'), engine=engine)


def core_diagnostics(text):
    return [(op, int(a, 16), int(n), region, reason) for op,a,n,region,reason in re.findall(
        r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), size ([0-9]+), region '([^']+)', reason: (.+)", text)]


def device_lines(text):
    return [line for line in text.splitlines() if line.startswith(TYPE + ': rejected ') or
            line.startswith(TYPE + ': wrapper rejected ')]


def rejected(r, offset, *, write=False, value=0, width='l', reason='unsupported-register',
             wrapper=False, shape=False):
    before, oldlog = world(r), r.logs()
    size = {'b':1, 'w':2, 'l':4, 'q':8}[width]
    op = 'write' if write else 'read'
    address = (WRAPPER if wrapper else HS_BASE) + offset
    command = f'{op}{width} {address:#x}' + (f' {value:#x}' if write else '')
    r.command(command)
    newlog, after = r.logs(), world(r)
    assert after == before
    added_core = core_diagnostics(newlog)[len(core_diagnostics(oldlog)):]
    added_device = device_lines(newlog)[len(device_lines(oldlog)):]
    if shape:
        assert not wrapper, 'Wrapper boundary shape delivery has a separate bounded check'
        assert added_core == [(op, offset, size, TYPE, 'rejected')], added_core
        assert added_device == [], added_device
        expected_line = None
    else:
        stem = TYPE + ': rejected ' + ('wrapper ' if wrapper else '')
        expected_line = stem + f'{op} offset={offset:#x} size={size}'
        if write:
            expected_line += f' value={value:#x}'
        expected_line += ' reason=' + reason
        assert added_device == [expected_line], (command, added_device, expected_line)
        assert added_core == [], added_core
    r.no_output('hs'); r.no_output('legacy')
    return {'command':command, 'expected_specific_diagnostic':expected_line,
            'actual_core_diagnostics':added_core, 'actual_device_diagnostics':added_device,
            'full_state_unchanged':True, 'error_return_used_as_register_value':False}


def unmapped(r, address, *, write=False, value=0):
    before, oldlog = world(r), r.logs()
    command = f"{'writel' if write else 'readl'} {address:#x}" + (f' {value:#x}' if write else '')
    r.command(command)
    assert world(r) == before
    added = core_diagnostics(r.logs())[len(core_diagnostics(oldlog)):]
    assert added == [('write' if write else 'read', address, 4, '(null)', 'rejected')], added
    assert device_lines(r.logs())[len(device_lines(oldlog)):] == []
    return {'command':command,'actual_core_rejections':added,'full_state_unchanged':True}


def mappings(r, name):
    tree = r.monitor('human-monitor-command', {'command-line':'info mtree -f'})
    (r.directory / name).write_text(tree)
    found = {(int(a,16),int(b,16),n) for a,b,n in re.findall(
        r'([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (' + re.escape(TYPE) + r'[^\s]*)(?:\s|$)', tree)}
    expected = {(HS_BASE,HS_BASE+0x3fff,TYPE), (WRAPPER,WRAPPER+3,TYPE+'/wrapper-version')} if r.reference else set()
    assert found == expected, (found, expected)
    return {'mtree_file':name,'active_leaves':sorted(found),'active_leaf_count':len(found),
            'active_mapped_bytes':0x4004 if r.reference else 0}


def assert_irq(r, expected, engine='hs'):
    assert engine=='hs' and r.irq_intercepted
    assert r.qom('irq-level', DEVICE if engine=='hs' else LEGACY) is expected
    # No MMIO value stands in for the signal. A zero-time QTest command drains
    # actual asynchronous output transitions captured by the established hook.
    r.step(0)
    assert r.irq[0] is expected,(expected,r.irq,r.irq_events)


def identity_and_pure_observation(r):
    before = r.counters(); first = world(r); repeated = world(r); after = r.counters()
    assert first == repeated and after['qom_get'] - before['qom_get'] == 2 * FIRST_GETTERS
    assert after['memory'] == before['memory'] == 0
    props = {x['name']:x['type'] for x in r.monitor('qom-list', {'path':DEVICE})}
    for field in DYNAMIC_STR: assert props[field] == 'string'
    for field in DYNAMIC_UINT + IDENTITY_UINT: assert props[field] == 'uint64'
    for field in DYNAMIC_BOOL + IDENTITY_BOOL: assert props[field] == 'bool'
    assert not any(k.startswith('qtest-') for k in props)
    bring_up(r)
    assert r.read(FW_MAIN) == r.read(FW_SECONDARY) == FW_REVISION
    assert r.read_absolute(WRAPPER) == WRAPPER_VERSION
    assert (r.read(0xe24) >> 16) & 0x3f == FIFO_BYTES // 4
    assert (r.read(0xe28) >> 24) & 0x3f == 32
    return {'first_snapshot':first,'repeated_snapshot':repeated,'pure_getters_per_world':FIRST_GETTERS,
            'first_observation_memory_delta':0,'first_observation_cont_delta':0,
            'identity_oracle':'captured UART protocol byte2 and actual producer firmware low byte1',
            'physical_firmware_selection_claimed':False,'physical_wrapper_reset_claimed':False}


def exact_original_cold_probe_and_dma_masks(r):
    assert r.read(FW_MAIN) >> 8 & 255 == 2
    assert r.read(FW_MAIN) & 255 == 1
    assert r.read(FW_SECONDARY) & 255 == 1
    version = r.read_absolute(WRAPPER)
    assert (version >> 28, (version >> 16) & 0xfff, version & 0xffff) == (2,5,0)
    r.write(M_EN, DONE); r.write(S_EN, OVERRUN)
    r.write(M_EN, r.read(M_EN) | M_COLD)
    r.write(S_EN, r.read(S_EN) | S_COLD)
    assert r.read(M_EN) == M_COLD | DONE
    assert r.read(S_EN) == S_COLD | OVERRUN
    r.write_absolute(RAM, 0xa55a7319)
    before = world(r)
    r.write(TX_DMA_SET, 0x400f)
    r.write(RX_DMA_SET, 0x402f)
    assert r.read(TX_DMA_EN) == 0x400f and r.read(RX_DMA_EN) == 0x402f
    after = world(r)
    for name in DYNAMIC_UINT + DYNAMIC_BOOL:
        assert after['hs'][name] == before['hs'][name]
    assert not register(after['hs'],M_STATUS)&DONE and not register(after['hs'],S_STATUS)&DONE
    assert r.read_absolute(RAM) == 0xa55a7319
    r.no_output(); r.no_output('legacy'); assert_irq(r,False)
    r.write(TX_DMA_CLEAR, 0x4000); r.write(RX_DMA_CLEAR, 0x20)
    assert r.read(TX_DMA_EN)==0xf and r.read(RX_DMA_EN)==0x400f
    return {'main_irq_or_mask':M_COLD,'secondary_irq_or_mask':S_COLD,
            'mode3_mask_writes':[[TX_DMA_SET,0x400f],[RX_DMA_SET,0x402f]],
            'dma_mask_storage_only':True,'dma_transfer_completed':False,
            'ram_witness_unchanged':True,'no_backend_bytes_or_completion':True}


def exact_mapping_and_neighbor_holes(r):
    result=mappings(r,'mtree-enabled.txt')
    result['holes']=[unmapped(r,a) for a in
        (HS_BASE-4,HS_BASE+0x4000,WRAPPER-4,WRAPPER+4,0x988000,0x99c000)]
    return result


def canonical_packing_words():
    # Independent reduction of the pinned vendor packing routine for bpw=8,
    # four words and LSB-to-MSB. The four ten-bit vectors begin at bit0/8/16/24,
    # carry length-minus-one7, and only the fourth vector has STOP set.
    vectors=[((index*8)<<5)|(7<<1)|int(index==3) for index in range(4)]
    assert vectors==[0x00e,0x10e,0x20e,0x30f]
    words=(vectors[0]|(vectors[1]<<10),vectors[2]|(vectors[3]<<10))
    assert words==(0x4380e,0xc3e0e)
    return words


def set_canonical_packing(r):
    cfg0,cfg1=canonical_packing_words()
    values=((0x254,0),(0x260,cfg0),(0x264,cfg1),(0x284,cfg0),(0x288,cfg1),
            (0x268,8),(0x28c,8))
    for offset,value in values:
        r.write(offset,value);assert r.read(offset)==value
    return {'bits_per_word':8,'words_per_fifo_element':4,'msb_to_lsb':False,
            'cfg0':cfg0,'cfg1':cfg1,'byte_granularity':0,
            'physical_reset_value_claimed':False}


def tx_partial_word_real_bytes(r):
    packing=set_canonical_packing(r)
    r.write(GLOBAL_EN,4); r.write(M_EN,DONE)
    r.write(TX_LEN,5); r.write(M_CMD,START)
    r.write(TX_FIFO,0x80ff0100)
    assert r.receive(4)==b'\x00\x01\xff\x80'
    assert r.read(STATUS)&1 and r.read(TX_TOTAL)==4
    r.write(TX_FIFO,0xeeeeee7f)
    assert r.receive(1)==b'\x7f'
    assert r.read(TX_TOTAL)==5 and not r.read(STATUS)&1
    assert r.read(M_STATUS)&DONE; assert_irq(r,True);r.no_output()
    assert r.qom('tx-count')==r.qom('tx-remaining')==0
    return {'exact_output_hex':'0001ff807f','padding_emitted':False,'accepted_bytes':5,
            'actual_packing_configuration':packing}


def core_irq_mask_aliases_and_w1c(r):
    r.write(GLOBAL_EN,0);tx(r,b'I');assert r.receive(1)==b'I'
    assert r.read(M_STATUS)&DONE;assert_irq(r,False)
    r.write(M_EN_SET,DONE|CANCEL);assert r.read(M_EN)==DONE|CANCEL
    r.write(GLOBAL_EN,4);assert_irq(r,True)
    r.write(M_CLEAR,CANCEL);assert_irq(r,True)
    r.write(M_EN_CLEAR,DONE);assert r.read(M_EN)==CANCEL;assert_irq(r,False)
    r.write(M_EN_SET,DONE);assert_irq(r,True)
    r.write(M_CLEAR,DONE);assert_irq(r,False)
    r.write(S_EN_SET,RX_LAST|RX_READ_ERR);r.write(S_EN_CLEAR,RX_LAST)
    assert r.read(S_EN)==RX_READ_ERR
    r.write(M_EN,TX_WATERMARK);r.write(TX_WM,2)
    assert r.read(M_STATUS)&TX_WATERMARK;assert_irq(r,True)
    r.write(M_CLEAR,TX_WATERMARK);assert_irq(r,True)
    r.write(TX_WM,0);r.write(M_CLEAR,TX_WATERMARK);assert_irq(r,False)
    return {'mask_set_clear_real_state':True,'w1c_and_level_watermark_distinguished':True}


def rx_partial_empty_and_no_peer_invention(r):
    packing=set_canonical_packing(r)
    r.write(GLOBAL_EN,8);r.write(S_EN,RX_LAST|RX_READ_ERR);r.write(RX_WM,2)
    r.write(S_CMD,START)
    assert r.read(RX_STATUS)==0 and r.qom('rx-received')==0;r.no_output()
    assert r.read(RX_FIFO)==0
    assert r.read(S_STATUS)&RX_READ_ERR;assert_irq(r,True)
    r.write(S_CLEAR,RX_READ_ERR);r.write(M_CLEAR,RX_READ_ERR);assert_irq(r,False)
    data=b'\x00\xff\x01\x80\x7f\x55\xa5';r.send(data)
    r.until(lambda:r.qom('rx-count')==7,'seven real RX bytes')
    assert r.read(RX_STATUS)==0xb0000002;assert_irq(r,True)
    assert r.read(RX_FIFO)==int.from_bytes(data[:4],'little')
    assert r.read(RX_STATUS)==0xb0000001
    assert r.read(RX_FIFO)==int.from_bytes(data[4:],'little')
    assert r.read(RX_STATUS)==0 and r.read(RX_TOTAL)==7
    r.write(S_CLEAR,RX_LAST);r.write(M_CLEAR,RX_LAST);assert_irq(r,False)
    r.no_output()
    return {'input_hex':data.hex(),'received_bytes':7,'partial_bytes':3,
            'empty_read_sets_error':True,'synthetic_peer_response':False,
            'actual_packing_configuration':packing}


def rx_backpressure_preserves_all_bytes(r):
    r.write(S_CMD,START); data=bytes(range(80));r.send(data)
    r.until(lambda:r.qom('rx-count')==FIFO_BYTES,'RX capacity reached')
    assert r.qom('rx-received')==FIFO_BYTES
    actual=bytearray()
    for _ in range(20):
        r.until(lambda:r.qom('rx-count')>=4,'RX refill from real peer')
        actual.extend(r.read(RX_FIFO).to_bytes(4,'little'))
    assert bytes(actual)==data
    assert r.qom('rx-count')==0 and r.read(RX_TOTAL)==80
    return {'input_bytes':80,'fifo_capacity_bytes':FIFO_BYTES,'all_received_in_order':True}


def tx_fifo_overflow_and_clock_pause(r):
    gate(r,HS_SE,False)
    r.write(TX_LEN,68);r.write(M_CMD,START)
    data=bytes(range(64))
    for off in range(0,64,4):r.write(TX_FIFO,int.from_bytes(data[off:off+4],'little'))
    before=engine_world(r);assert before['tx-count']==64 and before['tx-sent']==0
    r.write(TX_FIFO,0xdeadbeef)
    after=engine_world(r)
    for k in DYNAMIC_FIELDS:
        if k!='registers-hex':assert after[k]==before[k],k
    expected_regs=list(registers(before));expected_regs[M_STATUS//4]|=TX_WRITE_ERR
    assert registers(after)==tuple(expected_regs)
    assert r.read(M_STATUS)&TX_WRITE_ERR and not r.read(M_STATUS)&DONE
    r.no_output();gate(r,HS_SE,True);assert r.receive(64)==data
    r.write(TX_FIFO,0x73727170);assert r.receive(4)==b'pqrs'
    assert r.read(TX_TOTAL)==68 and r.read(M_STATUS)&DONE
    return {'fifo_full_bytes':64,'overflow_discarded_bytes':4,'overflow_reported':True,
            'clock_stopped_emitted_bytes':0,'final_sent_bytes':68}


def commands_cancel_abort_and_overrun(r):
    r.write(TX_LEN,8);r.write(M_CMD,START);r.write(M_CMD,START)
    assert r.read(M_STATUS)&OVERRUN
    r.write(TX_FIFO,0x41544144);assert r.receive(4)==b'DATA'
    r.write(M_CTRL,4)
    assert not r.read(STATUS)&1 and r.read(M_STATUS)&(DONE|CANCEL)==DONE|CANCEL
    r.write(M_CLEAR,0xffffffff);r.write(M_CMD,START);r.write(M_CTRL,2)
    assert r.read(M_STATUS)&(DONE|ABORT)==DONE|ABORT and r.read(TX_STATUS)==0
    r.write(S_CMD,START);r.send(b'rx!')
    r.until(lambda:r.qom('rx-count')==3,'RX bytes before abort')
    r.write(S_CTRL,2)
    assert r.read(RX_STATUS)==0 and not r.read(STATUS)&0x1000
    assert r.read(S_STATUS)&(DONE|ABORT)==DONE|ABORT
    r.write(M_CLEAR,0xffffffff);r.write(S_CLEAR,0xffffffff)
    r.write(M_CMD,2<<27);r.write(S_CMD,2<<27)
    assert r.read(M_STATUS)&ILLEGAL and r.read(S_STATUS)&ILLEGAL
    assert not r.read(M_STATUS)&DONE and not r.read(S_STATUS)&DONE
    r.no_output()
    return {'cancel_abort_overrun_and_illegal_are_observed':True,'fabricated_success':False}


def configure_se(r, cfg=1):
    # Vendor-supported XO / 1 recipe, programmed through the actual GCC RCG.
    for off,value in ((0x17738,0),(0x1773c,0),(0x17740,0),(0x17734,cfg),(0x17730,1)):
        r.write_absolute(GCC+off,value)
    for _ in range(100):
        r.step(1000)
        if not r.read_absolute(GCC+0x17730)&1:
            break
    else:
        raise AssertionError('S6 RCG UPDATE did not complete within bounded virtual time')


def bring_up(r):
    assert r.reference
    configure_se(r)
    gate(r,HS_SE|HS_M_AHB|HS_S_AHB,True)
    r.until(lambda:r.qom('se-clock-period')==PERIOD_UNIT//19_200_000,'S6 XO clock')
    assert r.qom('m-ahb-clock-period')==r.qom('s-ahb-clock-period')==PERIOD_UNIT//100_000_000
    return {'se_hz':19_200_000,'explicit_bus_reference_hz':100_000_000,
            'vote_register':HS_VOTE,'vote_mask':HS_SE|HS_M_AHB|HS_S_AHB,
            'rcg_config':1,'physical_clock_measurement_claimed':False}


def three_clock_gates_and_real_irq_suppression(r):
    r.write(GLOBAL_EN,4);r.write(M_EN,DONE|TX_WATERMARK);r.write(TX_WM,2)
    r.write(TX_LEN,5);r.write(M_CMD,START);assert_irq(r,True)
    gate(r,HS_SE,False)
    assert r.qom('se-clock-period')==0;assert_irq(r,False)
    r.write(TX_FIFO,0x44434241);r.write(TX_FIFO,0x45)
    assert r.qom('tx-count')==5 and r.qom('tx-sent')==0
    assert not r.read(M_STATUS)&DONE;r.no_output()
    gate(r,HS_SE,True);assert r.receive(5)==b'ABCDE';assert_irq(r,True)
    r.write(TX_WM,0);r.write(M_CLEAR,0xffffffff)
    rejects=[]
    for mask,name in ((HS_M_AHB,'m-ahb-clock-period'),(HS_S_AHB,'s-ahb-clock-period')):
        r.write(TX_LEN,0x1234);gate(r,mask,False);assert r.qom(name)==0
        rejects.append(rejected(r,TX_LEN,write=True,value=0x5678,reason='bus-unavailable'))
        rejects.append(rejected(r,TX_LEN,reason='bus-unavailable'))
        gate(r,mask,True);assert r.read(TX_LEN)==0x1234
    return {'se_pause_preserved_five_bytes':True,'ahb_gated_rejections':rejects,
            'no_completion_while_se_stopped':True}


def hwcg_request_removal_is_real(r):
    cbcr=GCC+0x17008
    old=r.read_absolute(cbcr)
    r.write_absolute(cbcr,(old&2)|2)
    r.command('set_irq_in /machine/gcc hw-request 132 0')
    assert r.qom('s-ahb-clock-period')==0
    rejected(r,FW_MAIN,reason='bus-unavailable')
    r.command('set_irq_in /machine/gcc hw-request 132 1')
    assert r.qom('s-ahb-clock-period')==PERIOD_UNIT//100_000_000
    assert r.read(FW_MAIN)==FW_REVISION
    r.write_absolute(cbcr,0)
    r.command('set_irq_in /machine/gcc hw-request 132 0')
    assert r.qom('s-ahb-clock-period')==PERIOD_UNIT//100_000_000
    r.command('set_irq_in /machine/gcc hw-request 132 1')
    return {'gcc_hwcg_index':132,'hwcg_control_register':cbcr,
            'missing_demand_stops_hwcg_clock':True,'disabled_hwcg_uses_software_vote':True}


def wrapper_reset_isolation_and_fixed_identity(r):
    initial=world(r)
    r.write(GLOBAL_EN,12);r.write(M_EN,DONE|TX_WATERMARK);r.write(S_EN,RX_LAST)
    r.write(TX_WM,2);r.write(TX_LEN,8);r.write(M_CMD,START)
    r.write(TX_FIFO,0x44434241);assert r.receive(4)==b'ABCD'
    r.write(S_CMD,START);r.send(b'RX!')
    r.until(lambda:r.qom('rx-count')==3,'RX before BCR')
    gate(r,HS_SE,False);r.write(TX_FIFO,0x48474645)
    old=engine_world(r,'legacy')
    r.write_absolute(HS_RESET,1)
    assert r.qom('reset-held') is True;assert_irq(r,False)
    assert engine_world(r,'legacy')==old
    rejected(r,TX_LEN,write=True,value=7,reason='bus-unavailable')
    r.no_output();r.write_absolute(HS_RESET,0);gate(r,HS_SE,True)
    after=world(r)
    assert after['identity']==initial['identity'] and after['legacy']==initial['legacy']
    assert after['hs']==initial['hs']
    tx(r,b'OK');assert r.receive(2)==b'OK'
    hs=engine_world(r)
    r.write_absolute(OLD_RESET,1);r.write_absolute(OLD_RESET,0)
    assert engine_world(r)==hs
    return {'reset24_affected_hs_only':True,'reset26_affected_console_only':True,
            'mutable_state_reset_to_observed_fresh_state':True,'fixed_identity_preserved':True,
            'physical_firmware_retention_claimed':False}


def two_engine_fifo_and_backend_isolation(r):
    hs0=engine_world(r);old0=engine_world(r,'legacy')
    assert r.read(FW_MAIN,engine='legacy')==0x200
    tx(r,b'HS\x00\xff');assert r.receive(4)==b'HS\x00\xff';r.no_output('legacy')
    assert engine_world(r,'legacy')==old0
    hs1=engine_world(r)
    tx(r,b'OLD\x80',engine='legacy');assert r.receive(4,'legacy')==b'OLD\x80';r.no_output()
    assert engine_world(r)==hs1
    for engine,mask in (('hs',RX_LAST),('legacy',RX_WATERMARK)):
        r.write(S_EN,mask,engine=engine);r.write(S_CMD,START,engine=engine)
    r.send(b'hsrx');r.send(b'console','legacy')
    r.until(lambda:r.qom('rx-count')==4,'HS RX')
    r.until(lambda:r.qom('rx-count',LEGACY)==7,'legacy RX')
    assert r.read(RX_FIFO)==int.from_bytes(b'hsrx','little')
    assert r.read(RX_FIFO,engine='legacy')==int.from_bytes(b'cons','little')
    assert r.read(RX_FIFO,engine='legacy')==int.from_bytes(b'ole','little')
    old1=engine_world(r,'legacy');gate(r,HS_SE,False)
    assert engine_world(r,'legacy')==old1
    return {'independent_backend_ids':['hs','legacy'],'different_rx_payloads_verified':True,
            'different_irq_masks':True,'independent_clock_gate':True,
            'legacy_firmware_view_unchanged':0x200,'source_initial_hs':hs0}


def strict_shapes_readonly_and_unknown_registers(r):
    observations=[]
    for width in ('b','w','q'):
        observations.append(rejected(r,FW_MAIN,width=width,shape=True))
        observations.append(rejected(r,M_EN,write=True,width=width,value=1,shape=True))
    for delta in (1,2,3):
        observations.append(rejected(r,M_EN+delta,shape=True))
        observations.append(rejected(r,M_EN+delta,write=True,value=1,shape=True))
    for off in (FW_MAIN,FW_SECONDARY,STATUS,TX_STATUS,RX_STATUS,TX_TOTAL,RX_TOTAL,0xe24,0xe28):
        observations.append(rejected(r,off,write=True,value=0xdeadbeef,reason='read-only'))
    observations.append(rejected(r,0,wrapper=True,write=True,value=0xdeadbeef,reason='read-only'))
    for off in (0x10,0x200,0x1000,0x1004,0x1010,0x3ffc):
        observations.append(rejected(r,off))
        observations.append(rejected(r,off,write=True,value=0x12345678))
    observations.append(rejected(r,GLOBAL_EN,write=True,value=0x10,reason='reserved-bits'))
    for off in (0x268,0x28c):
        observations.append(rejected(r,off,write=True,value=7,reason='unsupported-format'))
    cfg0,cfg1=canonical_packing_words()
    for off,value in ((0x260,cfg0^1),(0x264,cfg1^1),(0x284,cfg0^1),(0x288,cfg1^1),
                      (0x254,1)):
        observations.append(rejected(r,off,write=True,value=value,reason='unsupported-format'))
    for off in (0x260,0x264,0x284,0x288):
        observations.append(rejected(r,off,write=True,value=1<<20,reason='reserved-bits'))
    for off in (0x22c,0x25c,0x26c,0x280,0x2a4,0x2a8,0x2ac):
        observations.append(rejected(r,off,write=True,value=1,reason='reserved-bits'))
    # Buffered reads preserve an accepted prefix but do not turn the rejected
    # suffix into meaningful data. This is distinct from scalar readq.
    before=world(r);old=r.logs()
    reply=r.command(f'read {HS_BASE+FW_MAIN:#x} 5')
    assert len(reply)==1 and reply[0].startswith('0x')
    value=bytes.fromhex(reply[0][2:]);assert len(value)==5 and value[:4]==b'\x01\x02\x00\x00'
    core=core_diagnostics(r.logs())[len(core_diagnostics(old)):]
    assert core==[('read',FW_SECONDARY,1,TYPE,'rejected')],core
    assert device_lines(r.logs())[len(device_lines(old)):]==[] and world(r)==before
    return {'rejections':observations,'five_byte_read_prefix_hex':value[:4].hex(),
            'failed_suffix_interpreted':False,'whole_buffer_read_succeeded':False}


def unsupported_dma_transfer_has_no_effect(r):
    r.write_absolute(RAM,0x67534831)
    observations=[rejected(r,DMA_MODE,write=True,value=1,reason='unsupported-dma')]
    for off in (0xc30,0xc34,0xc38,0xc3c,0xc40,0xc44,0xc54,0xc58,0xc5c,
                0xd30,0xd34,0xd38,0xd3c,0xd40,0xd44,0xd54,0xd58,0xd5c,0xd60):
        observations.append(rejected(r,off,reason='unsupported-dma'))
        observations.append(rejected(r,off,write=True,value=RAM if off in (0xc30,0xd30) else 4,
                                     reason='unsupported-dma'))
    assert r.read_absolute(RAM)==0x67534831
    assert r.read(DMA_MODE)==0
    assert not r.read(M_STATUS)&DONE and not r.read(S_STATUS)&DONE
    assert_irq(r,False);r.no_output();r.no_output('legacy')
    return {'all_unsupported_transfer_accesses':observations,'ram_witness_unchanged':True,
            'dma_data_moved':False,'dma_completion_fabricated':False}


def immutable_qom_properties(r):
    before=world(r);failures=[]
    for name in IDENTITY_FIELDS+DYNAMIC_FIELDS:
        old=before['identity'][name] if name in IDENTITY_FIELDS else before['hs'][name]
        proposed=(not old) if type(old) is bool else (old^1 if type(old) is int else '00')
        response=r.raw_monitor('qom-set',{'path':DEVICE,'property':name,'value':proposed})
        assert 'error' in response,response
        text=response['error'].get('desc','').lower()
        assert any(x in text for x in ('read-only','read only','not writable','after','realized')),text
        failures.append({'property':name,'error':response['error']})
    assert world(r)==before
    return {'qom_write_rejections':failures,'all_state_unchanged':True}


def disconnected_backend_retains_pending(r):
    tx(r,b'WAIT!')
    state=engine_world(r)
    assert state['tx-count']==state['tx-remaining']==5 and state['tx-sent']==0
    assert state['tx-active'] and not register(state,M_STATUS)&DONE
    r.step(10000);assert engine_world(r)==state
    r.connect_backend('hs')
    assert r.receive(5)==b'WAIT!'
    assert r.qom('tx-sent')==5 and r.qom('tx-count')==0 and r.read(M_STATUS)&DONE
    r.no_output()
    return {'unconnected_bytes_retained':5,'discarded_bytes':0,'reconnect_exact_output_hex':'5741495421'}


def absent_backend_cannot_complete(r):
    tx(r,b'NONE')
    state=engine_world(r)
    assert state['tx-count']==state['tx-remaining']==4 and state['tx-sent']==0
    assert state['tx-active'] and not register(state,M_STATUS)&DONE
    r.step(10000);assert engine_world(r)==state
    r.write(M_CTRL,4)
    assert r.qom('tx-count')==0 and r.qom('tx-sent')==0
    assert r.read(M_STATUS)&CANCEL
    return {'backend_absent':True,'bytes_sent':0,'pending_until_explicit_cancel':True}


def real_socket_tx_backpressure_and_resume(r):
    peer=r.backend['hs'];peer.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024)
    limit=1<<20
    r.write(TX_LEN,limit);r.write(M_CMD,START)
    submitted=bytearray()
    for number in range(limit//64):
        piece=bytes(((number*64+i)*17+3)&255 for i in range(64))
        for offset in range(0,64,4):
            r.write(TX_FIFO,int.from_bytes(piece[offset:offset+4],'little'))
        submitted.extend(piece)
        if r.qom('tx-count'):
            break
    else:
        raise AssertionError('Real socket did not reach backpressure before the bounded 1 MiB limit')
    blocked=engine_world(r)
    assert 0<blocked['tx-count']<=64 and blocked['tx-sent']+blocked['tx-count']==len(submitted)
    assert blocked['tx-remaining']==limit-blocked['tx-sent']
    assert blocked['tx-active'] and not register(blocked,M_STATUS)&DONE
    assert r.receive(len(submitted))==bytes(submitted)
    r.until(lambda:r.qom('tx-count')==0,'socket writability drains pending TX')
    assert r.qom('tx-sent')==len(submitted)
    assert not r.read(M_STATUS)&DONE
    r.write(M_CTRL,4)
    return {'os_socket_receive_buffer':peer.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF),
            'saturation_limit_bytes':limit,'submitted_bytes':len(submitted),
            'pending_bytes_at_backpressure':blocked['tx-count'],
            'all_submitted_bytes_received_exactly':True,'completion_before_full_command':False,
            'explicit_cancel_of_unwritten_remainder':True}


def zero_length_waits_for_engine_clock(r):
    r.write(GLOBAL_EN,4);r.write(M_EN,DONE);gate(r,HS_SE,False)
    r.write(TX_LEN,0);r.write(M_CMD,START)
    assert not r.read(M_STATUS)&DONE;assert_irq(r,False);r.no_output()
    assert r.qom('tx-zero-pending') is True
    gate(r,HS_SE,True)
    assert r.read(M_STATUS)&DONE;assert_irq(r,True)
    assert r.qom('tx-zero-pending') is False and r.qom('tx-sent')==0;r.no_output()
    return {'zero_length_completion_waited_for_clock':True,'bytes_sent':0}


def default_off_keeps_both_windows_unmapped(args,out):
    details=[]
    for name,reference in (('default-omitted',None),('explicit-off',False)):
        r=None
        try:
            r=HsRig(args,out/name,reference=reference)
            first=world(r);assert first['identity']==identity_expected(False)
            before=mappings(r,'mtree-before-reset.txt')
            probes=[]
            for address in (HS_BASE,HS_BASE+FW_MAIN,HS_BASE+0x3ffc,WRAPPER):
                probes.append(unmapped(r,address))
                probes.append(unmapped(r,address,write=True,value=0xdeadbeef))
            r.send(b'no fabricated RX')
            assert engine_world(r)==first['hs']
            tx(r,b'unchanged-console',engine='legacy')
            assert r.receive(17,'legacy')==b'unchanged-console'
            assert engine_world(r)==first['hs'];r.no_output()
            r.monitor('system_reset');r.step(1000)
            assert engine_world(r)==first['hs']
            after=mappings(r,'mtree-after-reset.txt')
            details.append({'fixture':name,'global_property_omitted':reference is None,
                            'before':before,'after_reset':after,'generic_hole_checks':probes,
                            'legacy_console_real_bytes':17,'hs_backend_consumed_bytes':0})
        finally:
            if r:r.close()
    return {'default_and_explicit_off':details,'new_clock_wiring_in_default_profile':False}


def paused_tcg_has_real_pure_observers(args,out):
    r=None
    try:
        r=HsRig(args,out/'paused-tcg',accel='tcg',auto_cont=False)
        before=r.monitor('query-status');first=world(r);second=world(r)
        after=r.monitor('query-status')
        assert before['running'] is after['running'] is False
        assert first==second and r.cont_count==r.memory_count==r.qtest_count==0
        for path in (DEVICE,DEVICE+'/se-clk',DEVICE+'/m-ahb',DEVICE+'/s-ahb'):
            names={x['name'] for x in r.monitor('qom-list',{'path':path})}
            assert not any(name.startswith('qtest-') for name in names)
        r.no_output();r.no_output('legacy')
        return {'fixture':r.directory.name,'accel':'tcg','status_before':before,'status_after':after,
                'first_world':first,'repeated_world':second,'getters_per_world':FIRST_GETTERS,
                'guest_cpu_executed':False,'memory_commands':0,'cont_commands':0,
                'qtest_channel_created':False}
    finally:
        if r:r.close()


def absent_clock_parent_remains_zero(args,out):
    r=None
    try:
        r=HsRig(args,out/'missing-clock-parent',boot_configured=False)
        gate(r,HS_SE|HS_M_AHB|HS_S_AHB,True)
        configure_se(r,0x101)  # GPLL0 main / 1; boot-configured=off leaves PLL off.
        assert r.qom('se-clock-period')==0
        r.write(TX_LEN,1);r.write(M_CMD,START);r.write(TX_FIFO,ord('P'))
        state=engine_world(r);assert state['tx-count']==1 and state['tx-sent']==0
        r.no_output();configure_se(r,1)
        assert r.qom('se-clock-period')==PERIOD_UNIT//19_200_000
        assert r.receive(1)==b'P' and r.read(M_STATUS)&DONE
        return {'fixture':r.directory.name,'disabled_pll_parent_period':0,
                'xo_parent_period':PERIOD_UNIT//19_200_000,'pending_byte_released_only_with_real_parent':True}
    finally:
        if r:r.close()


def encode_guard(reference):
    value=struct.pack('>IIQIQIIIII',int(reference),IDENTITY,HS_BASE,0x4000,
                      WRAPPER,4,FW_REVISION,FW_REVISION,WRAPPER_VERSION,FIFO_BYTES)
    assert len(value)==GUARD_BYTES
    return value


def encode_functional(state):
    validate_engine(state)
    value=bytes.fromhex(state['registers-hex']+state['tx-fifo-hex']+state['rx-fifo-hex'])
    value+=struct.pack('>5I',*(state[k] for k in
                             ('tx-count','rx-count','tx-remaining','tx-sent','rx-received')))
    value+=bytes((state['tx-active'],state['rx-active']))
    assert len(value)==LEGACY_BODY_BYTES
    return value


def encode_periods_flags(state):
    value=struct.pack('>3Q',*(state[k] for k in
                            ('se-clock-period','m-ahb-clock-period','s-ahb-clock-period')))
    value+=bytes((state['reset-held'],state['tx-zero-pending']))
    assert len(value)==26
    return value


def native_payload(snapshot):
    raw=encode_guard(snapshot['identity']['enabled-reference'])
    raw+=encode_functional(snapshot['hs'])+encode_periods_flags(snapshot['hs'])
    assert len(raw)==VMSTATE_BYTES
    return raw


def legacy_payload(snapshot):
    name=b'qcom-geni-uart/clocks'
    raw=encode_functional(snapshot['legacy'])
    raw+=bytes((5,len(name)))+name+struct.pack('>I',1)
    raw+=encode_periods_flags(snapshot['legacy'])
    assert len(raw)==16587
    return raw


def find_section(data,name,version,payload):
    assert data[:4]==b'QEVM' and struct.unpack_from('>I',data,4)[0]==3
    encoded=name.encode();needle=bytes((len(encoded),))+encoded+struct.pack('>II',0,version)
    candidates=[];pos=0
    while True:
        at=data.find(needle,pos)
        if at<0:break
        header=at-5;body=at+len(needle);pos=at+1
        if header>=8 and data[header] in (1,4):
            candidates.append((header,body,at+1))
    assert len(candidates)==1,(name,candidates)
    header,body,name_at=candidates[0]
    sid=struct.unpack_from('>I',data,header+1)[0]
    footer=b'\x7e'+struct.pack('>I',sid)
    assert data[body:body+len(payload)]==payload,(name,'complete payload differs')
    assert data[body+len(payload):body+len(payload)+5]==footer,(name,'footer or unexpected subsection')
    return {'name':name,'instance_id':0,'version':version,'header_offset':header,
            'name_offset':name_at,'version_offset':body-4,'section_id':sid,
            'payload_offset':body,'payload_bytes':len(payload),
            'payload_sha256':hashlib.sha256(payload).hexdigest(),
            'footer_offset':body+len(payload),'section_end_offset':body+len(payload)+5,
            'footer_hex':footer.hex(),'all_payload_bytes_match_source_pure_observation':True}


def inspect_stream(path,snapshot):
    data=path.read_bytes();hs=native_payload(snapshot);old=legacy_payload(snapshot)
    native=find_section(data,TYPE,1,hs)
    legacy=find_section(data,'qcom-geni-uart',1,old)
    assert native['section_id']!=legacy['section_id']
    # The complete Clock VMSD is a uint64 period plus optional clock/muldiv.
    # Exact payload comparison and footer position above establish that this
    # actual UART clock subsection contains no optional ratio subtree; absence
    # is observed in the stream, never assumed from its name or a PASS field.
    assert old[LEGACY_BODY_BYTES:LEGACY_BODY_BYTES+27]==(
        bytes((5,21))+b'qcom-geni-uart/clocks'+struct.pack('>I',1))
    assert data[native['payload_offset']:native['payload_offset']+GUARD_BYTES]==encode_guard(
        snapshot['identity']['enabled-reference'])
    for label,raw in (('hs',hs),('legacy',old)):
        Path(str(path)+'.'+label+'.payload.bin').write_bytes(raw)
    result={'file':path.name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
            'sections':{'hs':native,'legacy':legacy},'hs_full_wire_bytes':VMSTATE_BYTES,
            'hs_guard_bytes':GUARD_BYTES,'hs_guard_precedes_every_mutable_field':True,
            'legacy_full_wire_bytes':len(old),'legacy_body_bytes':LEGACY_BODY_BYTES,
            'legacy_clock_subsection_observed':True,'legacy_clock_muldiv_subsections_observed':0,
            'all_4096_register_words_and_both_full_fifos_verified':True,
            'all_counters_boolean_bytes_and_clock_witnesses_verified':True,
            'complete_machine_other_device_payload_oracle_claimed':False}
    write_json(Path(str(path)+'.sections.json'),result)
    return result


def prepare_pending_source(r,enabled):
    if enabled:
        bring_up(r)
        set_canonical_packing(r)
    for engine in (('legacy','hs') if enabled else ('legacy',)):
        r.write(GLOBAL_EN,12,engine=engine)
        r.write(M_EN,DONE|RX_LAST,engine=engine);r.write(S_EN,RX_LAST,engine=engine)
        r.write(TX_WM,0,engine=engine);r.write(RX_WM,2,engine=engine)
        r.write(TX_LEN,8,engine=engine);r.write(M_CMD,START,engine=engine)
        prefix=b'ABCD' if engine=='hs' else b'OLD!'
        r.write(TX_FIFO,int.from_bytes(prefix,'little'),engine=engine)
        assert r.receive(4,engine)==prefix
        r.write(S_CMD,START,engine=engine)
        data=b'source!' if engine=='hs' else b'old'
        r.send(data,engine)
        path=DEVICE if engine=='hs' else LEGACY
        r.until(lambda p=path,n=len(data):r.qom('rx-count',p)==n,'source pending RX '+engine)
    gate(r,OLD_SE,False,legacy=True)
    r.write(TX_FIFO,int.from_bytes(b'TAIL','little'),engine='legacy')
    r.set_clock(250_000 if enabled else 350_000)
    r.monitor('stop');assert r.monitor('query-status')['running'] is False
    if enabled:
        # Deliberately create pending bytes with a live engine clock while VM
        # paused. A broken target post-load/clock/peer callback would drain them.
        r.write(TX_FIFO,int.from_bytes(b'EFGH','little'))
    snapshot=world(r)
    old=snapshot['legacy']
    assert old['tx-count']==old['tx-remaining']==4 and old['tx-sent']==4
    assert old['rx-count']==old['rx-received']==3
    assert old['se-clock-period']==0 and old['tx-active'] and old['rx-active']
    if enabled:
        hs=snapshot['hs']
        assert hs['tx-count']==hs['tx-remaining']==4 and hs['tx-sent']==4
        assert hs['rx-count']==hs['rx-received']==7
        assert hs['se-clock-period']==PERIOD_UNIT//19_200_000
        assert hs['tx-active'] and hs['rx-active']
        assert register(hs,M_EN)==DONE|RX_LAST and register(hs,S_EN)==RX_LAST
        assert register(hs,M_STATUS)&RX_LAST and register(hs,S_STATUS)&RX_LAST
    else:
        assert not snapshot['hs']['tx-active'] and not snapshot['hs']['rx-active']
        assert snapshot['hs']['tx-count']==snapshot['hs']['rx-count']==0
    r.no_output();r.no_output('legacy')
    return snapshot


def save_source(r,path,enabled,witness):
    snapshot=prepare_pending_source(r,enabled)
    r.write_absolute(RAM,witness);assert r.read_absolute(RAM)==witness
    assert world(r)==snapshot
    r.monitor('migrate',file_channels(path));result=wait_migration(r)
    status=r.monitor('query-status')
    assert result['status']=='completed' and status['status']=='postmigrate' and not status['running']
    assert world(r)==snapshot
    r.no_output();r.no_output('legacy')
    stream=inspect_stream(path,snapshot)
    saved={'scenario':'enabled' if enabled else 'disabled','file':path.name,
           'sha256':digest(path),'bytes':path.stat().st_size,'snapshot':snapshot,
           'source_directory':r.directory.name,'source_virtual_ns':r.last_clock,
           'ram_witness':witness,'query_migrate':result,'query_status':status,
           'sections':stream['sections'],'full_wire_validation':stream,
           'source_first_four_tx_bytes_already_delivered_per_active_engine':True,
           'source_pending_legacy_tx_hex':'5441494c',
           'source_pending_hs_tx_hex':'45464748' if enabled else '',
           'source_pending_hs_rx_hex':'736f7572636521' if enabled else '',
           'source_pending_legacy_rx_hex':'6f6c64','original_stream_modified':False}
    write_json(r.directory/'stopped-source.json',saved)
    return saved


def prewarm_target(r,saved):
    initial=world(r)
    r.set_clock(saved['source_virtual_ns'])
    r.write_absolute(RAM,saved['ram_witness']^0xffffffff)
    assert r.read_absolute(RAM)==saved['ram_witness']^0xffffffff
    assert r.device_mmio_count==r.cont_count==0
    assert world(r)==initial
    r.send(b'NEW')
    assert world(r)==initial
    r.no_output();r.no_output('legacy')
    record={'initial_world':initial,'counters':r.counters(),'ram_dirtied':True,
            'device_state_dirtied':False,'queued_host_rx_hex':'4e4557',
            'host_rx_consumed_before_incoming':False,'reference':r.reference}
    write_json(r.directory/'prewarm.json',record)
    return record


def receive_source(r,path,saved):
    before=r.counters()
    assert before['device_mmio']==before['cont']==0
    r.monitor('migrate-incoming',file_channels(path));result=wait_migration(r)
    assert result['status']=='completed'
    status=r.monitor('query-status');assert status['running'] is False
    q0=r.qom_get_count;first=world(r);q1=r.qom_get_count;again=world(r);q2=r.qom_get_count
    assert first==again==saved['snapshot']
    assert q1-q0==q2-q1==FIRST_GETTERS
    after=r.counters()
    for key in before:
        if key!='qom_get':assert after[key]==before[key],(key,before,after)
    r.no_output();r.no_output('legacy')
    observations={'first_world':first,'repeated_world':again,'first_qom_get_count':q1-q0,
                  'repeated_qom_get_count':q2-q1,'counters_before_incoming':before,
                  'counters_after_two_pure_worlds':after,'query_migrate':result,'query_status':status,
                  'device_mmio_delta_before_observations':0,'any_memory_delta_before_observations':0,
                  'cont_delta_before_observations':0,'backend_receive_delta_before_observations':0,
                  'first_world_includes_both_engines':True,'no_backend_consumption_or_output_observed':True,
                  'guest_cpu_executed':False}
    # This is the first post-incoming memory command; both full observations
    # above include every FIFO byte and raw register before this RAM witness.
    assert r.read_absolute(RAM)==saved['ram_witness']
    assert r.irq_intercepted and r.irq[0] is first['hs']['irq-level']
    observations['actual_hs_irq_after_ram_fence']=r.irq[0]
    observations['actual_hs_irq_transitions_before_cont']=list(r.irq_events)
    observations['gic_or_guest_interrupt_handling_verified']=False
    observations['ram_witness']=saved['ram_witness']
    observations['ram_loaded_after_two_worlds_before_cont']=True
    observations['counters_after_ram_witness']=r.counters()
    assert r.device_mmio_count==r.cont_count==0
    write_json(r.directory/'destination-before-first-device-mmio.json',observations)
    return observations


def continue_restored(r,enabled):
    r.monitor('cont')
    if enabled:
        assert r.receive(4)==b'EFGH'
        r.until(lambda:r.qom('rx-count')==10,'saved plus previously queued target RX')
        expected=b'source!NEW';actual=bytearray()
        for n in (4,4,2):
            value=r.read(RX_FIFO)
            actual.extend(value.to_bytes(4,'little')[:n])
            if n<4:assert value>>(8*n)==0
        assert bytes(actual)==expected
        assert r.read(TX_TOTAL)==8 and r.read(RX_TOTAL)==10
        assert r.read(M_STATUS)&DONE
        r.no_output();r.write(M_CLEAR,0xffffffff);r.write(S_CLEAR,0xffffffff)
    else:
        assert r.qom('rx-count')==r.qom('tx-count')==0
        r.no_output();mappings(r,'mtree-disabled-after-restore.txt')
    # Legacy was deliberately migrated with its serial clock gated. Its old
    # callback semantics must neither alias the HS FIFO nor send a prefix twice.
    r.no_output('legacy');hs_before=engine_world(r)
    gate(r,OLD_SE,True,legacy=True)
    assert r.receive(4,'legacy')==b'TAIL'
    assert r.read(RX_FIFO,engine='legacy')==int.from_bytes(b'old','little')
    assert r.read(TX_TOTAL,engine='legacy')==8 and r.read(RX_TOTAL,engine='legacy')==3
    assert engine_world(r)==hs_before
    r.no_output('legacy')
    if enabled:
        tx(r,b'NEXT');assert r.receive(4)==b'NEXT'
    tx(r,b'OLD2',engine='legacy');assert r.receive(4,'legacy')==b'OLD2'
    return {'pending_hs_bytes_after_cont':4 if enabled else 0,
            'pending_legacy_bytes_after_clock_restore':4,'already_sent_prefix_replayed':False,
            'prequeued_target_hs_rx_consumed_only_after_cont':enabled,
            'legacy_and_hs_dynamic_states_independent':True,'post_restore_new_io_verified':True}


GUARD_MUTATIONS=(
    ('profile',0,4,2),('identity',4,4,1),('se_base',8,8,4),('se_window',16,4,4),
    ('wrapper_address',20,8,4),('wrapper_window',28,4,1),('main_revision',32,4,1),
    ('secondary_revision',36,4,1),('wrapper_version',40,4,1),('fifo_geometry',44,4,1))
COUNTERS_OFFSET=GUARD_BYTES+16384+128
FLAGS_OFFSET=COUNTERS_OFFSET+20
CLOCKS_OFFSET=FLAGS_OFFSET+2
DYNAMIC_MUTATIONS=(
    ('unsupported_register','enabled',GUARD_BYTES+0x10,4,1,'invalid migration state'),
    ('global_irq_reserved_bit','enabled',GUARD_BYTES+GLOBAL_EN,4,0x10,'invalid migration state'),
    ('unsupported_dma_mode','enabled',GUARD_BYTES+DMA_MODE,4,1,'invalid migration state'),
    ('tx_count_out_of_range','enabled',COUNTERS_OFFSET,4,64,'invalid migration state'),
    ('rx_count_out_of_range','enabled',COUNTERS_OFFSET+4,4,64,'invalid migration state'),
    ('active_remaining_zero','enabled',COUNTERS_OFFSET+8,4,4,'invalid migration state'),
    ('command_accounting_overflow','enabled',COUNTERS_OFFSET+12,4,1<<24,'invalid migration state'),
    ('inactive_pending_tx','enabled',FLAGS_OFFSET,1,1,'invalid migration state'),
    ('noncanonical_boolean','enabled',FLAGS_OFFSET,1,2,'invalid migration state'),
    ('reset_with_active_state','enabled',CLOCKS_OFFSET+24,1,1,'invalid migration state'),
    ('zero_pending_with_active_tx','enabled',CLOCKS_OFFSET+25,1,1,'invalid migration state'),
    ('se_period_witness','enabled',CLOCKS_OFFSET,8,1,'input clock witness mismatch'),
    ('m_ahb_period_witness','enabled',CLOCKS_OFFSET+8,8,1,'input clock witness mismatch'),
    ('s_ahb_period_witness','enabled',CLOCKS_OFFSET+16,8,1,'input clock witness mismatch'),
    ('disabled_rx_state','disabled',COUNTERS_OFFSET+4,4,1,'invalid disabled migration state'))
STRUCTURAL_MUTATIONS=('missing_guard','unknown_section_identity','unsupported_section_version')


def mutate_one_bit(source,path,absolute,width,xor):
    original=source.read_bytes();changed=bytearray(original)
    old=int.from_bytes(original[absolute:absolute+width],'big')
    changed[absolute:absolute+width]=(old^xor).to_bytes(width,'big')
    positions=[n for n,(a,b) in enumerate(zip(original,changed)) if a!=b]
    expected=absolute+width-1-(xor.bit_length()-1)//8
    assert positions==[expected]
    assert (original[expected]^changed[expected]).bit_count()==1
    path.write_bytes(changed)
    assert source.read_bytes()==original
    return {'field_absolute_offset':absolute,'field_bytes':width,'old_value':old,
            'xor':xor,'changed_byte_offsets':positions,'changed_bits':1,
            'source_sha256':hashlib.sha256(original).hexdigest(),
            'mutated_sha256':digest(path),'bytes':len(changed),'original_stream_unchanged':True}


def expect_load_failure(r,path,saved,diagnostic,*,core=False):
    r.set_clock(saved['source_virtual_ns'])
    initial=world(r);r.send(b'EARLY');assert world(r)==initial
    counters=r.counters();assert counters['memory']==counters['device_mmio']==counters['cont']==0
    command_error=None
    observer=r.observe_failed_load_output()
    try:
        r.monitor('migrate-incoming',file_channels(path))
    except (AssertionError,OSError,RuntimeError) as exc:
        command_error=str(exc)
    exit_code=r.process.wait(timeout=20)
    stderr=r.logs()
    assert exit_code==1,(exit_code,stderr)
    needle=diagnostic if core else TYPE+': '+diagnostic
    assert needle in stderr,(needle,stderr)
    assert 'load of migration failed' in stderr.lower()
    assert 'error while loading state' in stderr.lower() or core
    assert r.memory_count==r.device_mmio_count==r.cont_count==0
    terminal_checks=observer.finish(specific_failure_verified=True)
    after=r.counters()
    for key in ('memory','device_mmio','cont','backend_receive_calls','backend_received_bytes'):
        assert after[key]==counters[key]
    result={'target_directory':r.directory.name,'target_exit_code':exit_code,
            'expected_diagnostic':needle,'specific_failure_observed':True,
            'core_section_validation_failure':core,'command_error':command_error,
            'initial_target_world':initial,'counters_before_incoming':counters,
            'counters_after_process_exit':after,'prequeued_target_rx_hex':'4541524c59',
            'no_early_backend_output':True,'backend_failure_observation':terminal_checks,
            'output_observed_continuously_from_before_incoming':True,
            'no_destination_memory_commands':True,
            'no_cont_command':True,'accepted_after_state_repair':False,
            'failed_process_state_readback_claimed':False,'stderr_sha256':digest(r.log_path)}
    write_json(r.directory/'load-failure.json',result)
    return result


def migration_checks(args,out,report):
    paths,saved={},{}
    for index,scenario in enumerate(('enabled','disabled')):
        enabled=scenario=='enabled';source=target=None
        try:
            source=HsRig(args,out/('migration-'+scenario+'-source'),reference=enabled)
            path=out/('geni-hs-'+scenario+'.migration');paths[scenario]=path
            record=save_source(source,path,enabled,0x47485310+index);saved[scenario]=record
            report['migration_sources'].append(record);source.close()
            target=HsRig(args,out/('migration-'+scenario+'-target'),reference=enabled,incoming=True)
            prewarm=prewarm_target(target,record)
            first=receive_source(target,path,record)
            mapping=mappings(target,'mtree-after-two-pure-worlds.txt')
            continued=continue_restored(target,enabled)
            item={'name':'migration_'+scenario,'source_status':'completed','target_status':'completed',
                  'passed':True,'stream_file':path.name,'stream_sha256':digest(path),
                  'stream_modified':False,'source_directory':source.directory.name,
                  'target_directory':target.directory.name,'prewarm':prewarm,
                  'first_target_observation':first,'active_mapping_after_restore':mapping,
                  'continuation':continued,'complete_hs_wire_bytes_verified':VMSTATE_BYTES,
                  'complete_legacy_wire_bytes_verified':16587,'continued_after_restore':True,
                  'both_engine_payloads_distinct':True}
            report['migrations'].append(item)
            report['checks'].append({'name':item['name'],'passed':True,
                                     'first_pure_getters':FIRST_GETTERS,
                                     'repeated_pure_getters':FIRST_GETTERS,
                                     'first_mmio_delta':0,'first_memory_delta':0,'first_cont_delta':0,
                                     'full_wire_bytes':VMSTATE_BYTES+16587})
        finally:
            if target:target.close()
            if source:source.close()

    for label,scenario,target_enabled in (('on_to_off','enabled',False),('off_to_on','disabled',True)):
        r=None
        try:
            r=HsRig(args,out/('reject-profile-'+label),reference=target_enabled,incoming=True)
            detail=expect_load_failure(r,paths[scenario],saved[scenario],'invalid migration profile')
            item={'name':'profile_'+label+'_rejected','passed':True,'stream_file':paths[scenario].name,
                  'stream_sha256':digest(paths[scenario]),'stream_modified':False,**detail}
            report['rejected_migrations'].append(item);report['checks'].append(item)
        finally:
            if r:r.close()

    cases=[(name,'enabled',offset,width,xor,'invalid migration profile')
           for name,offset,width,xor in GUARD_MUTATIONS]+list(DYNAMIC_MUTATIONS)
    for name,scenario,relative,width,xor,diagnostic in cases:
        original=paths[scenario];record=saved[scenario]
        absolute=record['sections']['hs']['payload_offset']+relative
        path=out/('geni-hs-corrupt-'+name+'.migration')
        mutation=mutate_one_bit(original,path,absolute,width,xor)
        r=None
        try:
            r=HsRig(args,out/('reject-bit-'+name),reference=scenario=='enabled',incoming=True)
            detail=expect_load_failure(r,path,record,diagnostic)
            item={'name':'single_bit_'+name+'_rejected','passed':True,'source_file':original.name,
                  'stream_file':path.name,'stream_modified':True,'field_relative_offset':relative,
                  **mutation,**detail}
            report['corrupted_stream_rejections'].append(item);report['checks'].append(item)
        finally:
            if r:r.close()

    original=paths['enabled'];record=saved['enabled'];raw=original.read_bytes()
    sec=record['sections']['hs']
    for name in STRUCTURAL_MUTATIONS:
        data=bytearray(raw);core=False
        if name=='missing_guard':
            at=sec['payload_offset'];del data[at:at+GUARD_BYTES]
            assert bytes(data[:at])==raw[:at] and bytes(data[at:])==raw[at+GUARD_BYTES:]
            diagnostic='invalid migration profile'
            change={'removed_offset':at,'removed_bytes':GUARD_BYTES,
                    'guard_removed_inside_present_section':True,'entire_missing_device_section_tested':False}
        elif name=='unknown_section_identity':
            at=sec['name_offset']+len(TYPE)-1;data[at]^=1
            assert data[at]!=raw[at] and (data[at]^raw[at]).bit_count()==1
            assert bytes(data[:at])==raw[:at] and bytes(data[at+1:])==raw[at+1:]
            diagnostic='Unknown savevm section or instance';core=True
            change={'changed_byte_offsets':[at],'changed_bits':1,
                    'original_section_name':TYPE,'mutated_section_name':TYPE[:-1]+chr(data[at])}
        else:
            at=sec['version_offset']+3;assert data[at]==1;data[at]^=1
            diagnostic=TYPE+': incoming version_id 0 is too old for local minimum version_id 1';core=True
            change={'changed_byte_offsets':[at],'changed_bits':1,'old_version':1,'new_version':0}
        path=out/('geni-hs-structural-'+name+'.migration');path.write_bytes(data)
        assert digest(original)==record['sha256']
        r=None
        try:
            r=HsRig(args,out/('reject-structural-'+name),incoming=True)
            detail=expect_load_failure(r,path,record,diagnostic,core=core)
            item={'name':'structural_'+name+'_rejected','passed':True,'source_file':original.name,
                  'source_sha256':record['sha256'],'stream_file':path.name,
                  'stream_sha256':digest(path),'stream_modified':True,'bytes':len(data),
                  'original_stream_unchanged':True,**change,**detail}
            report['structural_stream_rejections'].append(item);report['checks'].append(item)
        finally:
            if r:r.close()
    assert all(digest(paths[k])==saved[k]['sha256'] for k in paths)


# Each tuple is one independently retained fixture, with explicit exceptional
# transport requirements. Existing UART suites and their fixture policies are
# unchanged and remain separate compiled CI gates.
ORDINARY=(
    (identity_and_pure_observation,{},False),
    (exact_original_cold_probe_and_dma_masks,{},True),
    (exact_mapping_and_neighbor_holes,{},True),
    (tx_partial_word_real_bytes,{},True),
    (core_irq_mask_aliases_and_w1c,{},True),
    (rx_partial_empty_and_no_peer_invention,{},True),
    (rx_backpressure_preserves_all_bytes,{},True),
    (tx_fifo_overflow_and_clock_pause,{},True),
    (commands_cancel_abort_and_overrun,{},True),
    (three_clock_gates_and_real_irq_suppression,{},True),
    (hwcg_request_removal_is_real,{},True),
    (wrapper_reset_isolation_and_fixed_identity,{},True),
    (two_engine_fifo_and_backend_isolation,{},True),
    (strict_shapes_readonly_and_unknown_registers,{},True),
    (unsupported_dma_transfer_has_no_effect,{},True),
    (immutable_qom_properties,{},True),
    (disconnected_backend_retains_pending,{'hs_backend':'disconnected'},True),
    (absent_backend_cannot_complete,{'hs_backend':'absent'},True),
    (real_socket_tx_backpressure_and_resume,{},True),
    (zero_length_waits_for_engine_clock,{},True))
SPECIAL=(default_off_keeps_both_windows_unmapped,paused_tcg_has_real_pure_observers,
         absent_clock_parent_remains_zero)
CHECK_NAMES=(tuple(f.__name__ for f,_,_ in ORDINARY)+tuple(f.__name__ for f in SPECIAL)+
             ('migration_enabled','migration_disabled','profile_on_to_off_rejected','profile_off_to_on_rejected')+
             tuple('single_bit_'+x[0]+'_rejected' for x in GUARD_MUTATIONS)+
             tuple('single_bit_'+x[0]+'_rejected' for x in DYNAMIC_MUTATIONS)+
             tuple('structural_'+x+'_rejected' for x in STRUCTURAL_MUTATIONS))
EXPECTED_CHECKS=55
REPORT_CONSTANTS={
    'schema':'tb371fc-geni-hs-sm8250-v1','suite':'geni-hs-sm8250-reference',
    'expected_checks':EXPECTED_CHECKS,'expected_completed_migrations':2,
    'expected_rejected_migrations':2,'expected_corrupted_stream_rejections':25,
    'expected_structural_stream_rejections':3,'expected_unmodified_streams':2,
    'expected_stream_files':30,'vmstate_version':1,'vmstate_minimum_version':1,
    'vmstate_payload_bytes':VMSTATE_BYTES,'profile_guard_bytes':GUARD_BYTES,
    'legacy_functional_payload_bytes':LEGACY_BODY_BYTES,'legacy_complete_payload_bytes':16587,
    'first_observation_qom_get_count':FIRST_GETTERS,'repeated_observation_qom_get_count':FIRST_GETTERS,
    'pure_dynamic_getters_per_engine':17,'pure_hs_identity_getters':15,
    'reference_identity':IDENTITY,'physical_base':HS_BASE,'window_bytes':0x4000,
    'wrapper_address':WRAPPER,'wrapper_window_bytes':4,'main_firmware_revision':FW_REVISION,
    'secondary_firmware_revision':FW_REVISION,'wrapper_version':WRAPPER_VERSION,
    'fifo_capacity_bytes':FIFO_BYTES,'fifo_width_bits':32,'fifo_depth_words':16,
    'cold_primary_irq_or_mask':M_COLD,'cold_secondary_irq_or_mask':S_COLD,
    'dma_tx_irq_valid_mask':0x400f,'dma_rx_irq_valid_mask':0x77ff,
    'reference_enabled_by_default':False,'default_off_mapped_bytes':0,'enabled_mapped_bytes':0x4004,
    'two_engine_backend_isolation_tested':True,'migration_destination_ram_dirtied':True,
    'migration_destination_device_state_dirtied':False,'firmware_executed':False,
    'physical_state_captured':False,'dma_mask_modeled':True,'dma_transfer_modeled':False,
    'bluetooth_peer_modeled':False,'guest_cpu_executed':False,'native_kernel_probe_observed':False,
    'android_boot_passed':False,'full_migration_stream_hashes_verified':True,
    'test_oracle_reads_device_implementation':False}


def verify_process_cleanup(out):
    expected={f'ordinary-{i:02d}-{test.__name__}':0
              for i,(test,_,_) in enumerate(ORDINARY)}
    expected.update({name:0 for name in
                     ('default-omitted','explicit-off','paused-tcg','missing-clock-parent')})
    expected.update({'migration-'+profile+'-'+role:0
                     for profile in ('enabled','disabled') for role in ('source','target')})
    expected.update({'reject-profile-'+direction:1 for direction in ('on_to_off','off_to_on')})
    expected.update({'reject-bit-'+case[0]:1 for case in GUARD_MUTATIONS+DYNAMIC_MUTATIONS})
    expected.update({'reject-structural-'+name:1 for name in STRUCTURAL_MUTATIONS})
    files={path.parent.name:path for path in out.glob('*/cleanup.json')}
    assert len(expected)==58 and set(files)==set(expected),(sorted(files),sorted(expected))
    observed=[]
    for name in sorted(expected):
        path=files[name];actual=json.loads(path.read_text())
        assert set(actual)=={'forced_terminate','forced_kill','quit_acknowledged','exit_code'},actual
        assert actual['forced_terminate'] is False and actual['forced_kill'] is False,(name,actual)
        assert actual['exit_code']==expected[name],(name,actual,expected[name])
        assert actual['quit_acknowledged'] is (expected[name]==0),(name,actual)
        observed.append({'fixture':name,'cleanup_file':name+'/cleanup.json',
                         'cleanup_sha256':digest(path),'expected_exit_code':expected[name],**actual})
    return observed


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu',required=True)
    parser.add_argument('--machine',default=DEFAULT_MACHINE)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args();args.qemu=os.path.abspath(args.qemu);out=args.out.resolve()
    out.mkdir(parents=True,exist_ok=False)
    started=time.monotonic();before=digest(args.qemu)
    report={**REPORT_CONSTANTS,'qemu':args.qemu,'machine':args.machine,'work_dir':str(out),
            'qemu_sha256_before':before,'qemu_sha256_after':None,
            'test_script_sha256':digest(__file__),
            'transport_helper_sha256':digest(Path(__file__).with_name('geni_hs_test_transport.py')),
            'source_register_revision':'98719830b0de5584f5588bbe9f4c8304d97a64c6',
            'hs_irq_observation_method':'QTest intercept of actual sysbus output; pure QOM crosscheck',
            'qtest_intercept_replaces_gic_connection':True,
            'gic_or_guest_interrupt_handling_verified':False,
            'reference_word_derivation':{'protocol_byte':2,'firmware_low_byte':1,
                'formula':'(protocol_byte << 8) | firmware_low_byte','both_actual_producer_writes_equal':True,
                'physical_0x998000_selection_proven':False,'wrapper_version_is_declared_model_profile':True},
            'checks':[],'migrations':[],'rejected_migrations':[],'corrupted_stream_rejections':[],
            'structural_stream_rejections':[],'migration_sources':[],'actual_stream_files':[],
            'process_cleanup':[],'process_cleanup_verified':False,
            'binary_unchanged':False,'two_engine_backend_isolation_tested':False,
            'migration_destination_ram_dirtied':False,'full_migration_stream_hashes_verified':False,
            'passed':False}
    try:
        assert len(DYNAMIC_FIELDS)==17 and len(IDENTITY_FIELDS)==15
        assert 2*len(DYNAMIC_FIELDS)+len(IDENTITY_FIELDS)==FIRST_GETTERS
        assert GUARD_BYTES+LEGACY_BODY_BYTES+26==VMSTATE_BYTES
        assert len(CHECK_NAMES)==len(set(CHECK_NAMES))==EXPECTED_CHECKS
        assert len(GUARD_MUTATIONS)+len(DYNAMIC_MUTATIONS)==25
        for index,(test,options,setup) in enumerate(ORDINARY):
            r=None
            try:
                r=HsRig(args,out/f'ordinary-{index:02d}-{test.__name__}',**options)
                setup_evidence=bring_up(r) if setup else None
                result=test(r)
                report['checks'].append({'name':test.__name__,'passed':True,
                                         'fixture':r.directory.name,'clock_setup':setup_evidence,**result})
                if test is two_engine_fifo_and_backend_isolation:
                    report['two_engine_backend_isolation_tested']=True
            finally:
                if r:r.close()
        for test in SPECIAL:
            result=test(args,out)
            report['checks'].append({'name':test.__name__,'passed':True,**result})
        migration_checks(args,out,report)
        files=[{'name':p.name,'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(out.glob('*.migration'))]
        report['actual_stream_files']=files
        expected={'geni-hs-enabled.migration','geni-hs-disabled.migration'}
        expected|={'geni-hs-corrupt-'+x[0]+'.migration' for x in GUARD_MUTATIONS+DYNAMIC_MUTATIONS}
        expected|={'geni-hs-structural-'+x+'.migration' for x in STRUCTURAL_MUTATIONS}
        assert len(expected)==len(files)==30 and {x['name'] for x in files}==expected
        assert [x['name'] for x in report['checks']]==list(CHECK_NAMES)
        assert all(x['passed'] is True for x in report['checks'])
        assert len(report['migrations'])==2 and len(report['rejected_migrations'])==2
        assert len(report['corrupted_stream_rejections'])==25 and len(report['structural_stream_rejections'])==3
        assert all(x['changed_bits']==1 and x['original_stream_unchanged'] for x in report['corrupted_stream_rejections'])
        assert all(x['specific_failure_observed'] and x['no_early_backend_output'] and
                   x['no_destination_memory_commands'] and x['no_cont_command'] and
                   x['accepted_after_state_repair'] is False for x in
                   report['rejected_migrations']+report['corrupted_stream_rejections']+report['structural_stream_rejections'])
        assert all(x['source_status']==x['target_status']=='completed' and x['continued_after_restore'] and
                   x['complete_hs_wire_bytes_verified']==VMSTATE_BYTES and
                   x['first_target_observation']['first_qom_get_count']==FIRST_GETTERS and
                   x['first_target_observation']['repeated_qom_get_count']==FIRST_GETTERS and
                   x['first_target_observation']['ram_loaded_after_two_worlds_before_cont'] for x in report['migrations'])
        report['process_cleanup']=verify_process_cleanup(out)
        report['process_cleanup_verified']=True
        report['full_migration_stream_hashes_verified']=True
        report['migration_destination_ram_dirtied']=True
        assert report['two_engine_backend_isolation_tested']
        report['passed']=True
    except BaseException as exc:
        report['error']=str(exc);report['traceback']=traceback.format_exc()
    finally:
        report['qemu_sha256_after']=digest(args.qemu)
        report['binary_unchanged']=report['qemu_sha256_after']==before
        report['passed']=report['passed'] and report['binary_unchanged']
        report['duration_seconds']=time.monotonic()-started
        write_json(out/'result.json',report)
    print(json.dumps(report,indent=2,sort_keys=True))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
