#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual GCC MMIO/Clock/GPIO and migration tests against a compiled QEMU.

The machine must instantiate /machine/gcc at 0x100000, with 19.2 MHz xo
and 32 kHz sleep inputs. Tests without -kernel use cold GCC defaults.
Logs are retained. No tablet access, no mocked register implementation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import traceback

from test_geni_uart import Rig, connect_unix
from test_geni_migration import wait_migration

if not __debug__:
    raise SystemExit('Run these checks without Python -O.')

LOCK, ACTIVE, ACK = 1 << 31, 1 << 30, 1 << 29
FSM, UPDATE, RESET_N, OUTCTRL = 1 << 20, 1 << 22, 4, 1
OFF, ROOT, BUSY = 1 << 31, 2, 1
UART_CMD, UART_CBCR, VOTE = 0x1e4d0, 0x1e4cc, 0x52010
UART_CFG = (6 << 8) | (2 << 12) | 1
PERIOD_UNIT = 1_000_000_000 << 32
CPUSS_CMD, CPUSS_DIV, CPUSS_CBCR = 0x48010, 0x48028, 0x48000
SYS_NOC_CPUSS_CBCR, SHARED_VOTE = 0x48198, 0x52000
SYS_NOC_CPUSS_VOTE, CPUSS_VOTE = 1, 1 << 21
SYS_NOC_CPUSS_CLK = 'gcc_sys_noc_cpuss_ahb_clk'
CPUSS_CLK = 'gcc_cpuss_ahb_clk'
CPUSS_SRC = 'gcc_cpuss_ahb_clk_src'
CPUSS_POSTDIV = 'gcc_cpuss_ahb_postdiv_clk_src'


class GccRig(Rig):
    def __init__(self, args, directory, incoming=False, intercept='reset'):
        self.args = args
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log_path = self.directory / 'qemu.stderr'
        self.log = self.log_path.open('wb')
        command = [args.qemu, '-machine', args.machine, '-accel', 'qtest',
                   '-m', '128M', '-display', 'none', '-monitor', 'none',
                   '-serial', 'null', '-S', '-d', 'unimp,guest_errors',
                   '-qtest', f'unix:{directory}/qtest,server=on,wait=off',
                   '-qtest-log', '/dev/null',
                   '-qmp', f'unix:{directory}/qmp,server=on,wait=off']
        if incoming:
            command.extend(['-incoming', 'defer'])
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=self.log)
        self.connections = []
        try:
            self.qtest = connect_unix(self.directory / 'qtest', self.process)
            self.connections.append(self.qtest)
            self.qfile = self.qtest.makefile('rwb', buffering=0)
            self.qmp = connect_unix(self.directory / 'qmp', self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile('rwb', buffering=0)
            self.events = []
            self.irq = {i: False for i in range(45)}
            greeting = json.loads(self.qmpfile.readline())
            assert 'QMP' in greeting, greeting
            self.monitor('qmp_capabilities')
            self.command(f'irq_intercept_out {args.qom_path} {intercept}')
            self.last_clock = 0
            # QTest CPUs execute no guest instructions, but virtual timers
            # need the VM event loop enabled. -S alone disables their dispatch.
            if not incoming:
                self.monitor('cont')
        except BaseException:
            self.close()
            raise

    def step(self, ns):
        self.last_clock = int(self.command(f'clock_step {ns}')[0], 0)
        return self.last_clock

    def period(self, name):
        return self.monitor('qom-get', {'path': self.args.qom_path + '/' + name,
                                       'property': 'qtest-clock-period'})

    def expect_hz(self, name, hz):
        expected = PERIOD_UNIT // hz if hz else 0
        actual = self.period(name)
        assert actual == expected, (name, hz, expected, actual)

    def gpio(self, name, number, level):
        self.command(f'set_irq_in {self.args.qom_path} {name} {number} {int(level)}')

    def expect_line(self, number, level):
        self.read(0x52018)  # Drain the QTest stream before checking async GPIOs.
        assert self.irq[number] == level, (number, self.irq, level)

    def reset(self):
        self.monitor('system_reset')
        self.wait_for(lambda: self.read(4) == 0 and self.read(0x52018) == 0,
                      'cold GCC reset')


def pll(r, base=0, vote=1, multiplier=31, alpha=0x4000, wait=True):
    r.write(base + 4, multiplier)
    r.write(base + 0x40, alpha)
    r.write(base + 0xc, 0x107)
    r.write(base, FSM | RESET_N)
    r.write(0x52018, r.read(0x52018) | vote)
    if wait:
        r.step(5000)
        assert r.read(base) & (LOCK | ACTIVE) == LOCK | ACTIVE


def uart_config(r, numerator=384, wait=True):
    r.write(UART_CMD + 4, UART_CFG)
    r.write(UART_CMD + 8, numerator)
    r.write(UART_CMD + 12, (~(15625 - numerator)) & 0xffff)
    r.write(UART_CMD + 16, (~15625) & 0xffff)
    r.write(UART_CMD, ROOT | BUSY)
    if wait:
        r.step(1000)
        assert not r.read(UART_CMD) & BUSY


def cold_defaults(r):
    assert r.read(0) == r.read(4) == r.read(0x52018) == 0
    assert r.read(UART_CBCR) & OFF
    assert r.read(0x77004) == 1
    assert r.read(0x77000) == 0
    r.expect_hz('xo', 19_200_000)
    r.expect_hz('sleep', 32_000)
    r.expect_hz('gpll0', 0)
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 0)


def invalid_pll_does_not_lock(r):
    r.write(0, FSM | RESET_N | UPDATE)
    r.write(0x52018, 1)
    for multiplier in (0, 1, 0xffff):
        r.write(4, multiplier)
        r.step(20_000)
        assert not r.read(0) & (LOCK | ACTIVE | ACK)
        r.expect_hz('gpll0', 0)


def pll_vote_lock_and_stop(r):
    pll(r, wait=False)
    assert not r.read(0) & LOCK
    r.step(4999)
    assert not r.read(0) & LOCK
    r.step(1)
    assert r.read(0) & (LOCK | ACTIVE) == LOCK | ACTIVE
    r.expect_hz('gpll0', 600_000_000)
    r.expect_hz('gpll0_out_even', 300_000_000)
    assert r.read(0x38) == 1
    r.gpio('remote-vote', 0, True)
    r.write(0x52018, 0)
    assert r.read(0) & (LOCK | ACTIVE) == LOCK | ACTIVE
    r.expect_hz('gpll0', 600_000_000)
    r.gpio('remote-vote', 0, False)
    assert not r.read(0) & (LOCK | ACTIVE)
    r.expect_hz('gpll0', 0)


def pll_manual_output_gate(r):
    r.write(4, 31)
    r.write(0x40, 0x4000)
    r.write(0, RESET_N)
    r.write(0x38, 1)
    r.step(5000)
    assert r.read(0) & LOCK
    assert not r.read(0) & ACTIVE
    r.expect_hz('gpll0', 0)
    r.write(0xc, 7)
    r.write(0, RESET_N | OUTCTRL)
    r.expect_hz('gpll0', 600_000_000)
    r.write(0, RESET_N)
    r.expect_hz('gpll0', 0)
    assert r.read(0) & LOCK
    r.write(0x38, 0)
    assert not r.read(0) & LOCK


def pll_shadow_and_latch(r):
    pll(r)
    r.write(4, 32)
    r.expect_hz('gpll0', 600_000_000)
    r.write(0, FSM | RESET_N | UPDATE)
    r.step(999)
    assert not r.read(0) & ACK
    r.expect_hz('gpll0', 600_000_000)
    r.step(1)
    assert r.read(0) & ACK
    r.expect_hz('gpll0', 619_200_000)
    r.write(0, FSM | RESET_N)
    assert not r.read(0) & ACK
    r.write(4, 0)
    r.write(0, FSM | RESET_N | UPDATE)
    r.step(10_000)
    assert not r.read(0) & ACK
    r.expect_hz('gpll0', 619_200_000)


def pll_disabled_latch(r):
    r.write(4, 31)
    r.write(0x40, 0x4000)
    r.write(0, RESET_N | UPDATE)
    r.step(999)
    assert not r.read(0) & ACK
    r.step(1)
    assert r.read(0) & ACK
    assert not r.read(0) & LOCK
    r.expect_hz('gpll0', 0)
    r.write(0, RESET_N)
    assert not r.read(0) & ACK


def independent_pll_vote_bits(r):
    pll(r)
    pll(r, base=0x76000, vote=1 << 4, multiplier=40, alpha=0)
    pll(r, base=0x1c000, vote=1 << 9, multiplier=50, alpha=0)
    r.expect_hz('gpll0', 600_000_000)
    r.expect_hz('gpll4', 768_000_000)
    r.expect_hz('gpll9', 960_000_000)
    r.write(0x52018, (1 << 9) | 1)
    r.expect_hz('gpll4', 0)
    r.expect_hz('gpll9', 960_000_000)
    r.expect_hz('gpll0', 600_000_000)


def rcg_exact_fraction_and_shadow(r):
    pll(r)
    uart_config(r, wait=False)
    assert r.read(UART_CMD) & BUSY
    r.step(999)
    assert r.read(UART_CMD) & BUSY
    r.step(1)
    assert not r.read(UART_CMD) & (OFF | BUSY)
    r.expect_hz('gcc_qupv3_wrap2_s4_clk_src', 7_372_800)
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 0)
    r.write(VOTE, 1 << 8)
    assert not r.read(UART_CBCR) & OFF
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 7_372_800)
    r.write(UART_CMD + 8, 768)
    r.write(UART_CMD + 12, (~(15625 - 768)) & 0xffff)
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 7_372_800)
    assert r.read(UART_CMD) & ((1 << 5) | (1 << 6))
    r.write(UART_CMD, ROOT | BUSY)
    r.step(1000)
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 14_745_600)
    assert not r.read(UART_CMD) & 0xf1


def invalid_rcg_keeps_previous_active_config(r):
    pll(r)
    uart_config(r)
    r.write(VOTE, 1 << 8)
    r.write(UART_CMD + 4, (7 << 8) | 1)
    r.write(UART_CMD, ROOT | BUSY)
    r.step(10_000)
    assert r.read(UART_CMD) & BUSY
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 7_372_800)
    r.write(UART_CMD + 4, UART_CFG)
    r.step(1000)
    assert not r.read(UART_CMD) & BUSY


def invalid_fraction_does_not_ack(r):
    pll(r)
    uart_config(r)
    r.write(VOTE, 1 << 8)
    r.write(UART_CMD + 8, 0)
    r.write(UART_CMD, ROOT | BUSY)
    r.step(10_000)
    assert r.read(UART_CMD) & BUSY
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 7_372_800)
    r.write(UART_CMD + 8, 384)
    r.step(1000)
    assert not r.read(UART_CMD) & BUSY
    r.write(UART_CMD + 16, 0)
    r.write(UART_CMD, ROOT | BUSY)
    r.step(10_000)
    assert r.read(UART_CMD) & BUSY
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 7_372_800)
    r.write(UART_CMD + 16, (~15625) & 0xffff)
    r.step(1000)
    assert not r.read(UART_CMD) & BUSY


def stopped_parent_keeps_requested_branch_off(r):
    uart_config(r)
    r.write(VOTE, 1 << 8)
    assert r.read(UART_CMD) & OFF
    assert r.read(UART_CBCR) & OFF
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 0)
    pll(r)
    assert not r.read(UART_CBCR) & OFF
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 7_372_800)
    r.write(0x52018, 0)
    assert r.read(UART_CBCR) & OFF


def shared_votes_and_remote_voter(r):
    pll(r)
    uart_config(r)
    r.write(VOTE, 1 << 8)
    r.write(VOTE, r.read(VOTE) | (1 << 9))
    assert r.read(VOTE) == (1 << 8) | (1 << 9)
    r.write(VOTE, r.read(VOTE) & ~(1 << 8))
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 0)
    r.expect_hz('gcc_qupv3_wrap2_s5_clk', 19_200_000)
    r.gpio('remote-vote', 127, True)
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 7_372_800)
    assert r.read(VOTE) == 1 << 9
    r.gpio('remote-vote', 127, False)
    r.expect_hz('gcc_qupv3_wrap2_s4_clk', 0)


def sys_noc_cpuss_status_contract(r):
    # The enable vote is elsewhere: bit 0 here is not a branch-enable bit.
    assert r.read(SHARED_VOTE) == 0
    assert r.read(SYS_NOC_CPUSS_CBCR) == OFF
    r.expect_hz(SYS_NOC_CPUSS_CLK, 0)
    # This legacy Clock input remains present for VMState slot compatibility.
    # The branch now follows the internal CPUSS postdivider, not this input.
    r.expect_hz(SYS_NOC_CPUSS_CLK + '_parent', 0)
    for value in (0, OFF, 0xf0000000):
        r.write(SYS_NOC_CPUSS_CBCR, value)
        assert r.read(SYS_NOC_CPUSS_CBCR) == OFF
        assert r.read(SHARED_VOTE) == 0
    for value in (1, 1 << 27):
        r.write(SYS_NOC_CPUSS_CBCR, value)
        assert r.read(SYS_NOC_CPUSS_CBCR) == OFF
        assert r.read(SHARED_VOTE) == 0
    log = r.log_path.read_text(errors='replace')
    assert 'unsupported write 0x00000001 at 0x48198' in log, log
    assert 'unsupported write 0x08000000 at 0x48198' in log, log
    r.write(SHARED_VOTE, SYS_NOC_CPUSS_VOTE)
    assert r.read(SYS_NOC_CPUSS_CBCR) == 0
    r.expect_hz(SYS_NOC_CPUSS_CLK, 19_200_000)
    r.write(SYS_NOC_CPUSS_CBCR, 0xf0000000)
    assert r.read(SYS_NOC_CPUSS_CBCR) == 0
    assert r.read(SHARED_VOTE) == SYS_NOC_CPUSS_VOTE
    # The existing postdivider is read-only and starts at divide-by-one.
    assert r.read(CPUSS_DIV) == 0
    r.write(CPUSS_DIV, 0xf)
    assert r.read(CPUSS_DIV) == 0
    r.expect_hz(SYS_NOC_CPUSS_CLK, 19_200_000)


def sys_noc_cpuss_shared_votes(r):
    # Both branches share a parent and vote word, but have independent votes
    # and halt registers. Preserve another valid vote bit during each RMW.
    sibling_vote = 1 << 10
    r.write(SHARED_VOTE, CPUSS_VOTE | sibling_vote)
    assert r.read(SHARED_VOTE) == CPUSS_VOTE | sibling_vote
    assert r.read(CPUSS_CBCR) == 0
    assert r.read(SYS_NOC_CPUSS_CBCR) == OFF
    r.expect_hz(CPUSS_CLK, 19_200_000)
    r.expect_hz(SYS_NOC_CPUSS_CLK, 0)
    r.write(SHARED_VOTE, r.read(SHARED_VOTE) | SYS_NOC_CPUSS_VOTE)
    assert r.read(SHARED_VOTE) == CPUSS_VOTE | sibling_vote | SYS_NOC_CPUSS_VOTE
    assert r.read(CPUSS_CBCR) == r.read(SYS_NOC_CPUSS_CBCR) == 0
    r.expect_hz(CPUSS_CLK, 19_200_000)
    r.expect_hz(SYS_NOC_CPUSS_CLK, 19_200_000)
    r.write(SHARED_VOTE, r.read(SHARED_VOTE) & ~CPUSS_VOTE)
    assert r.read(SHARED_VOTE) == sibling_vote | SYS_NOC_CPUSS_VOTE
    assert r.read(CPUSS_CBCR) == OFF
    assert r.read(SYS_NOC_CPUSS_CBCR) == 0
    r.expect_hz(CPUSS_CLK, 0)
    r.expect_hz(SYS_NOC_CPUSS_CLK, 19_200_000)
    r.write(SHARED_VOTE, r.read(SHARED_VOTE) & ~SYS_NOC_CPUSS_VOTE)
    assert r.read(SHARED_VOTE) == sibling_vote
    assert r.read(CPUSS_CBCR) == r.read(SYS_NOC_CPUSS_CBCR) == OFF
    r.expect_hz(CPUSS_CLK, 0)
    r.expect_hz(SYS_NOC_CPUSS_CLK, 0)


def sys_noc_cpuss_parent_demand(r):
    r.write(SHARED_VOTE, SYS_NOC_CPUSS_VOTE)
    assert not r.read(CPUSS_CMD) & ROOT
    r.expect_hz(CPUSS_SRC, 19_200_000)
    r.expect_hz(CPUSS_POSTDIV, 19_200_000)
    r.expect_hz(SYS_NOC_CPUSS_CLK, 19_200_000)
    r.expect_hz(SYS_NOC_CPUSS_CLK + '_parent', 0)
    # Branch demand must propagate through divider 18 to RCG 17 without
    # software ROOT_EN. Selecting a stopped PLL must not fake clock-on.
    r.write(CPUSS_CMD + 4, (1 << 8) | 1)
    r.write(CPUSS_CMD, BUSY)
    r.step(1000)
    assert not r.read(CPUSS_CMD) & (ROOT | BUSY)
    assert r.read(CPUSS_CMD) & OFF
    assert r.read(SYS_NOC_CPUSS_CBCR) == OFF
    assert r.read(SHARED_VOTE) == SYS_NOC_CPUSS_VOTE
    r.expect_hz(SYS_NOC_CPUSS_CLK, 0)
    pll(r)
    assert r.read(SYS_NOC_CPUSS_CBCR) == 0
    r.expect_hz(CPUSS_SRC, 600_000_000)
    r.expect_hz(CPUSS_POSTDIV, 600_000_000)
    r.expect_hz(SYS_NOC_CPUSS_CLK, 600_000_000)
    r.expect_hz(CPUSS_CLK, 0)
    r.write(0x52018, 0)
    assert r.read(SYS_NOC_CPUSS_CBCR) == OFF
    assert r.read(SHARED_VOTE) == SYS_NOC_CPUSS_VOTE
    r.expect_hz(SYS_NOC_CPUSS_CLK, 0)
    r.write(CPUSS_CMD + 4, 0)
    r.write(CPUSS_CMD, BUSY)
    r.step(1000)
    assert r.read(SYS_NOC_CPUSS_CBCR) == 0
    r.expect_hz(SYS_NOC_CPUSS_CLK, 19_200_000)
    r.write(SHARED_VOTE, 0)
    assert r.read(CPUSS_CMD) == OFF
    assert r.read(SYS_NOC_CPUSS_CBCR) == OFF
    r.expect_hz(CPUSS_SRC, 0)
    r.expect_hz(SYS_NOC_CPUSS_CLK, 0)


def sys_noc_cpuss_remote_vote(r):
    r.gpio('remote-vote', 143, True)
    assert r.read(SHARED_VOTE) == 0
    assert r.read(SYS_NOC_CPUSS_CBCR) == 0
    assert r.read(CPUSS_CBCR) == OFF
    r.expect_hz(SYS_NOC_CPUSS_CLK, 19_200_000)
    r.expect_hz(CPUSS_CLK, 0)
    r.write(SHARED_VOTE, SYS_NOC_CPUSS_VOTE)
    r.gpio('remote-vote', 143, False)
    assert r.read(SHARED_VOTE) == SYS_NOC_CPUSS_VOTE
    assert r.read(SYS_NOC_CPUSS_CBCR) == 0
    r.expect_hz(SYS_NOC_CPUSS_CLK, 19_200_000)
    r.gpio('remote-vote', 143, True)
    r.write(SHARED_VOTE, 0)
    assert r.read(SHARED_VOTE) == 0
    assert r.read(SYS_NOC_CPUSS_CBCR) == 0
    r.expect_hz(SYS_NOC_CPUSS_CLK, 19_200_000)
    r.gpio('remote-vote', 143, False)
    assert r.read(SYS_NOC_CPUSS_CBCR) == OFF
    assert r.read(CPUSS_CBCR) == OFF
    r.expect_hz(SYS_NOC_CPUSS_CLK, 0)


def sys_noc_cpuss_reset(r):
    pll(r)
    r.write(SHARED_VOTE, SYS_NOC_CPUSS_VOTE | CPUSS_VOTE)
    r.write(CPUSS_CMD + 4, (1 << 8) | 3)
    r.write(CPUSS_CMD, BUSY)
    r.step(1000)
    r.expect_hz(SYS_NOC_CPUSS_CLK, 300_000_000)
    r.write(CPUSS_CMD + 4, (1 << 8) | 1)
    r.write(CPUSS_CMD, BUSY)
    assert r.read(CPUSS_CMD) & BUSY
    r.reset()
    r.step(20_000)
    assert r.read(SHARED_VOTE) == 0
    assert r.read(CPUSS_CMD) == OFF
    assert r.read(CPUSS_CMD + 4) == r.read(CPUSS_DIV) == 0
    assert r.read(CPUSS_CBCR) == r.read(SYS_NOC_CPUSS_CBCR) == OFF
    r.expect_hz(SYS_NOC_CPUSS_CLK, 0)
    r.expect_hz(CPUSS_CLK, 0)
    # Provider-owned GPIO inputs survive reset in the existing GCC contract.
    r.gpio('remote-vote', 143, True)
    r.write(SHARED_VOTE, SYS_NOC_CPUSS_VOTE)
    r.reset()
    # PLL markers were already zero before this reset, so wait for the
    # previously asserted branch vote to clear before observing reset state.
    r.wait_for(lambda: r.read(SHARED_VOTE) == 0,
               'SYS_NOC_CPUSS software vote cleared by reset')
    assert r.read(SHARED_VOTE) == 0
    assert r.read(SYS_NOC_CPUSS_CBCR) == 0
    assert r.read(CPUSS_CBCR) == OFF
    r.expect_hz(SYS_NOC_CPUSS_CLK, 19_200_000)
    r.gpio('remote-vote', 143, False)
    assert r.read(SYS_NOC_CPUSS_CBCR) == OFF
    r.expect_hz(SYS_NOC_CPUSS_CLK, 0)


def hwcg_consumer_request(r):
    r.write(0x77010, 1)
    r.expect_hz('gcc_ufs_phy_axi_clk', 19_200_000)
    r.write(0x77010, 3)
    assert r.read(0x77010) & OFF
    r.gpio('hw-request', 162, True)
    assert not r.read(0x77010) & OFF
    r.expect_hz('gcc_ufs_phy_axi_clk', 19_200_000)
    r.gpio('hw-request', 162, False)
    r.expect_hz('gcc_ufs_phy_axi_clk', 0)


def absent_external_parent_is_not_success(r):
    name = 'gcc_ufs_phy_rx_symbol_0_clk'
    r.expect_hz(name + '_parent', 0)
    r.write(0x77020, 1)
    assert r.read(0x77020) & OFF
    r.expect_hz(name, 0)


def sleep_parent_and_integer_divider(r):
    r.write(0x64008, (5 << 8) | 1)
    r.write(0x64004, ROOT | BUSY)
    r.step(1000)
    r.write(0x64000, 1)
    r.expect_hz('gcc_gp1_clk', 32_000)
    pll(r)
    r.write(0x64008, (1 << 8) | 11)  # GPLL0 / ((11+1)/2)
    r.write(0x64004, ROOT | BUSY)
    r.step(1000)
    r.expect_hz('gcc_gp1_clk', 100_000_000)


def reset_lines_and_shared_cbcr_bits(r):
    # Binding indices and offsets from upstream qcom,gcc-sm8250.h/reset map.
    pairs = [(24, 0x17000), (25, 0x18000), (26, 0x1e000),
             (32, 0x75000), (33, 0x77000), (34, 0xf000), (35, 0x10000)]
    for number, offset in pairs:
        r.write(offset, 1)
        assert r.read(offset) == 1
        r.expect_line(number, True)
        r.write(offset, 0)
        r.expect_line(number, False)
    r.write(0xb024, 1)
    r.write(0xb024, r.read(0xb024) | 4)
    assert r.read(0xb024) & 5 == 5
    r.expect_line(43, True)
    r.write(0xb024, r.read(0xb024) & ~4)
    assert r.read(0xb024) & 5 == 1
    r.expect_line(43, False)


def software_power_transition(r):
    assert r.read(0x77004) == 1
    r.write(0x77004, 0x282000)
    r.step(2999)
    assert not r.read(0x77004) & OFF
    r.step(1)
    assert r.read(0x77004) & OFF
    r.expect_line(4, True)
    r.write(0x77004, 0x282001)
    r.step(3000)
    assert not r.read(0x77004) & OFF
    r.expect_line(4, False)
    # Unsupported autonomous hardware-control mode is rejected, not faked.
    before = r.read(0x77004)
    r.write(0x77004, before | 2)
    assert r.read(0x77004) == before


software_power_transition.intercept = 'power'


def reset_cancels_pending_transitions(r):
    pll(r, wait=False)
    uart_config(r, wait=False)
    r.write(0x77004, 0)
    r.write(0x77000, 1)
    r.expect_line(33, True)
    r.reset()
    r.step(20_000)
    assert r.read(0) == r.read(4) == r.read(0x52018) == 0
    assert r.read(UART_CMD) == OFF
    assert r.read(0x77004) == 1
    r.expect_line(33, False)
    r.expect_hz('gpll0', 0)


def unsupported_registers_are_diagnosed(r):
    assert r.read(UART_CMD + 0x14) == 0
    r.write(UART_CMD + 0x14, 1)  # DFS is deliberately not implemented.
    assert r.read(UART_CMD + 0x14) == 0
    r.write(0x18, 0x1234)  # Analog PLL tuning is not simulated.
    assert r.read(0x18) == 0
    r.write(0xdead0, 0x11223344)
    r.read(0xdead0)
    log = r.log_path.read_text(errors='replace')
    assert 'unsupported write 0x00000001 at 0x1e4e4' in log, log
    assert 'unsupported write 0x00001234 at 0x18' in log, log
    assert 'unsupported read at 0xdead0' in log, log


TESTS = [cold_defaults, invalid_pll_does_not_lock, pll_vote_lock_and_stop,
         pll_manual_output_gate, pll_shadow_and_latch, pll_disabled_latch,
         independent_pll_vote_bits,
         rcg_exact_fraction_and_shadow, invalid_rcg_keeps_previous_active_config,
         invalid_fraction_does_not_ack, stopped_parent_keeps_requested_branch_off, shared_votes_and_remote_voter,
         sys_noc_cpuss_status_contract, sys_noc_cpuss_shared_votes,
         sys_noc_cpuss_parent_demand, sys_noc_cpuss_remote_vote,
         sys_noc_cpuss_reset,
         hwcg_consumer_request, absent_external_parent_is_not_success,
         sleep_parent_and_integer_divider, reset_lines_and_shared_cbcr_bits,
         software_power_transition, reset_cancels_pending_transitions,
         unsupported_registers_are_diagnosed]


def migration(args, directory):
    source = target = None
    checks = []
    try:
        source = GccRig(args, directory / 'source')
        target = GccRig(args, directory / 'target', incoming=True)
        pll(source)
        uart_config(source)
        shared_votes = SYS_NOC_CPUSS_VOTE | CPUSS_VOTE | (1 << 10)
        source.write(SHARED_VOTE, shared_votes)
        source.gpio('remote-vote', 143, True)
        source.write(CPUSS_CMD + 4, (1 << 8) | 3)
        source.write(CPUSS_CMD, BUSY)
        source.step(1000)
        source.expect_hz(SYS_NOC_CPUSS_CLK, 300_000_000)
        source.expect_hz(CPUSS_CLK, 300_000_000)
        # Preserve a shadow config distinct from the 300 MHz active config.
        source.write(CPUSS_CMD + 4, (1 << 8) | 5)
        assert source.read(CPUSS_CMD) & (1 << 4)
        assert not source.read(CPUSS_CMD) & (ROOT | BUSY)
        source.gpio('remote-vote', 127, True)
        source.write(0x77000, 1)
        uart_config(source, numerator=768, wait=False)
        source.write(0x77004, 0)
        source.expect_hz('gcc_qupv3_wrap2_s4_clk', 7_372_800)
        source.expect_line(33, True)
        regs = (0, 4, 0x40, 0x52018, UART_CMD, UART_CMD + 4,
                UART_CMD + 8, UART_CMD + 12, UART_CMD + 16,
                UART_CBCR, VOTE, 0x77000, 0x77004,
                SHARED_VOTE, SYS_NOC_CPUSS_CBCR, CPUSS_CBCR,
                CPUSS_CMD, CPUSS_CMD + 4, CPUSS_DIV)
        source.monitor('stop')
        before = {hex(reg): source.read(reg) for reg in regs}
        source.command('writel 0x80010000 0x47434331')
        # QTest owns each process's virtual clock. Align the test-controlled
        # timeline before migration; this is not a claim about physical time.
        now = source.last_clock
        target.command(f'clock_set {now}')
        checks.append({'check': 'active_clock_pending_update_power_and_reset', 'status': 'pass'})
        uri = f'unix:{directory}/migration.sock'
        target.monitor('migrate-incoming', {'uri': uri})
        source.monitor('migrate', {'uri': uri})
        state = {'source': wait_migration(source), 'target': wait_migration(target)}
        assert source.monitor('query-status')['status'] == 'postmigrate'
        assert not target.monitor('query-status')['running']
        checks.append({'check': 'actual_qmp_migration_completed', 'status': 'pass'})
        after = {hex(reg): target.read(reg) for reg in regs}
        assert before == after, {'before': before, 'after': after}
        assert int(target.command('readl 0x80010000')[0], 0) == 0x47434331
        target.expect_hz('gcc_qupv3_wrap2_s4_clk', 7_372_800)
        target.expect_line(33, True)
        checks.append({'check': 'registers_clock_remote_vote_gpio_and_ram_preserved', 'status': 'pass'})
        assert target.read(SHARED_VOTE) == shared_votes
        assert target.read(SYS_NOC_CPUSS_CBCR) == target.read(CPUSS_CBCR) == 0
        assert target.read(CPUSS_CMD) & (1 << 4)
        assert not target.read(CPUSS_CMD) & (ROOT | BUSY)
        target.expect_hz(SYS_NOC_CPUSS_CLK, 300_000_000)
        target.expect_hz(CPUSS_CLK, 300_000_000)
        target.expect_hz(SYS_NOC_CPUSS_CLK + '_parent', 0)
        checks.append({'check': 'sys_noc_cpuss_active_clock_shadow_and_shared_votes_preserved',
                       'status': 'pass'})
        target.monitor('cont')
        target.step(999)
        assert target.read(UART_CMD) & BUSY
        target.step(1)
        assert not target.read(UART_CMD) & BUSY
        target.expect_hz('gcc_qupv3_wrap2_s4_clk', 14_745_600)
        target.step(1999)
        assert not target.read(0x77004) & OFF
        target.step(1)
        assert target.read(0x77004) & OFF
        checks.append({'check': 'pending_timers_resume_at_test_controlled_deadlines', 'status': 'pass'})
        target.gpio('remote-vote', 127, False)
        target.expect_hz('gcc_qupv3_wrap2_s4_clk', 0)
        target.write(0x77000, 0)
        target.expect_line(33, False)
        checks.append({'check': 'destination_clocks_votes_and_reset_continue', 'status': 'pass'})
        target.write(SHARED_VOTE, target.read(SHARED_VOTE) & ~SYS_NOC_CPUSS_VOTE)
        assert target.read(SHARED_VOTE) == CPUSS_VOTE | (1 << 10)
        assert target.read(SYS_NOC_CPUSS_CBCR) == 0
        target.expect_hz(SYS_NOC_CPUSS_CLK, 300_000_000)
        target.gpio('remote-vote', 143, False)
        assert target.read(SYS_NOC_CPUSS_CBCR) == OFF
        assert target.read(CPUSS_CBCR) == 0
        target.expect_hz(SYS_NOC_CPUSS_CLK, 0)
        target.expect_hz(CPUSS_CLK, 300_000_000)
        checks.append({'check': 'sys_noc_cpuss_migrated_remote_vote_and_sibling_independence',
                       'status': 'pass'})
        target.write(SHARED_VOTE, target.read(SHARED_VOTE) | SYS_NOC_CPUSS_VOTE)
        target.write(CPUSS_CMD, BUSY)
        target.step(999)
        assert target.read(CPUSS_CMD) & BUSY
        target.expect_hz(SYS_NOC_CPUSS_CLK, 300_000_000)
        target.step(1)
        assert not target.read(CPUSS_CMD) & (ROOT | BUSY | (1 << 4))
        target.expect_hz(SYS_NOC_CPUSS_CLK, 200_000_000)
        target.expect_hz(CPUSS_CLK, 200_000_000)
        target.write(SHARED_VOTE, target.read(SHARED_VOTE) &
                     ~(SYS_NOC_CPUSS_VOTE | CPUSS_VOTE))
        assert target.read(SHARED_VOTE) == 1 << 10
        assert target.read(SYS_NOC_CPUSS_CBCR) == target.read(CPUSS_CBCR) == OFF
        target.expect_hz(SYS_NOC_CPUSS_CLK, 0)
        target.expect_hz(CPUSS_CLK, 0)
        checks.append({'check': 'sys_noc_cpuss_destination_parent_update_and_vote_removal',
                       'status': 'pass'})
        return {'success': True, 'checks': checks, 'migration': state,
                'qtest_virtual_ns_at_transfer': now}
    except Exception:
        return {'success': False, 'checks': checks, 'error': traceback.format_exc()}
    finally:
        if source:
            source.close()
        if target:
            target.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--qemu', required=True)
    ap.add_argument('--base', type=lambda s: int(s, 0), default=0x100000)
    ap.add_argument('--qom-path', default='/machine/gcc')
    ap.add_argument('--machine', default='tb371fc,gcc-boot-configured=off,ufs-enabled=off')
    ap.add_argument('--work-dir', type=Path)
    ap.add_argument('--migration-only', action='store_true')
    args = ap.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    directory = args.work_dir or Path(tempfile.mkdtemp(prefix='tb371fc-gcc-'))
    directory.mkdir(parents=True, exist_ok=True)
    checks = []
    with open(args.qemu, 'rb') as binary:
        digest = hashlib.file_digest(binary, 'sha256').hexdigest()
    report = {'schema_version': 1, 'suite': 'SM8250 GCC actual QTest',
              'qemu': args.qemu, 'machine': args.machine, 'qemu_sha256': digest,
              'work_dir': str(directory), 'checks': checks, 'ram_per_process': '128 MiB',
              'scope': 'Clock register/gate state, software reset/power and controlled virtual-time transitions; no analog, silicon timing, voltage or full-SoC equivalence'}
    if args.migration_only:
        report.update(migration(args, directory))
        report['suite'] = 'SM8250 GCC actual process-to-process migration'
        report['expected_checks'] = 8
    else:
        for test in TESTS:
            started = time.monotonic()
            r = None
            try:
                r = GccRig(args, directory / test.__name__,
                           intercept=getattr(test, 'intercept', 'reset'))
                test(r)
                checks.append({'test': test.__name__, 'status': 'pass',
                               'seconds': round(time.monotonic() - started, 4)})
            except Exception:
                checks.append({'test': test.__name__, 'status': 'fail',
                               'error': traceback.format_exc()})
                break
            finally:
                if r:
                    r.close()
        report['expected_checks'] = len(TESTS)
        report['success'] = len(checks) == len(TESTS) and all(c['status'] == 'pass' for c in checks)
    print(json.dumps(report, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
