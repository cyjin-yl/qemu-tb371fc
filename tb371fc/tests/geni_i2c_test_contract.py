#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent literal GENI I2C reference operations; no I/O on import.

Offsets, ordering and bit fields come from the complete pinned 98719830
consumer/header/helper, cross-checked against the complete 94304 consumer.
The profile values are the explicitly declared empty-bus-v1 virtual policy.
They are not observed physical reset values or firmware contents. This module
never reads the device C implementation or generated model data as an oracle.
"""
from dataclasses import dataclass
import struct


TYPE = 'qcom-sm8250-geni-i2c'
PROFILE = 'empty-bus-v1'
PROFILE_ID = 0x47493231
FW_REVISION = 0x00000300
FIFO_WORDS, FIFO_WORD_BITS = 16, 32
HW_PARAM = 0x20100000
IOS_IDLE = 3
WINDOW_BYTES = 0x4000

CFG, S_CFG, FORCE_DEFAULT, OUTPUT_CTRL, CGC = 0x0, 0x4, 0x20, 0x24, 0x28
STATUS, M_CLK, S_CLK, CLK_CTRL, FIFO_DISABLE = 0x40, 0x48, 0x4c, 0x60, 0x64
FW_MAIN, FW_SECONDARY, CLK_SEL = 0x68, 0x6c, 0x7c
BYTE_GRAN, DMA_MODE = 0x254, 0x258
TX_PACK0, TX_PACK1, TX_LEN, RX_LEN, SCL = 0x260, 0x264, 0x26c, 0x270, 0x278
RX_PACK0, RX_PACK1 = 0x284, 0x288
M_CMD, M_CTRL, M_STATUS, M_EN, M_CLEAR = 0x600, 0x604, 0x610, 0x614, 0x618
S_CMD, S_CTRL, S_STATUS, S_EN, S_CLEAR = 0x630, 0x634, 0x640, 0x644, 0x648
TX_FIFO, RX_FIFO, TX_STATUS, RX_STATUS = 0x700, 0x780, 0x800, 0x804
TX_WM, RX_WM, RX_RFR = 0x80c, 0x810, 0x814
IOS, M_GP_LENGTH, S_GP_LENGTH = 0x908, 0x910, 0x914
DMA_TX_PTR_L, DMA_TX_PTR_H, DMA_TX_ATTR, DMA_TX_LEN = 0xc30, 0xc34, 0xc38, 0xc3c
DMA_TX_STATUS, DMA_TX_CLEAR, DMA_TX_EN = 0xc40, 0xc44, 0xc48
DMA_TX_EN_SET, DMA_TX_EN_CLEAR, DMA_TX_LEN_IN, DMA_TX_RESET = 0xc4c, 0xc50, 0xc54, 0xc58
DMA_RX_PTR_L, DMA_RX_PTR_H, DMA_RX_ATTR, DMA_RX_LEN = 0xd30, 0xd34, 0xd38, 0xd3c
DMA_RX_STATUS, DMA_RX_CLEAR, DMA_RX_EN = 0xd40, 0xd44, 0xd48
DMA_RX_EN_SET, DMA_RX_EN_CLEAR, DMA_RX_LEN_IN, DMA_RX_RESET = 0xd4c, 0xd50, 0xd54, 0xd58
GSI_EN, GLOBAL_EN, HW_PARAM0, HW_PARAM1, DMA_GENERAL, DMA_DEBUG = 0xe18, 0xe1c, 0xe24, 0xe28, 0xe30, 0xe40

DONE, OVERRUN, ILLEGAL, FAILURE, CANCEL, ABORT = (1 << n for n in range(6))
NACK_GP = 1 << 10
NACK = FAILURE | NACK_GP
UNSUPPORTED_COMMAND = FAILURE | ILLEGAL
DMA_DONE, DMA_EOT, DMA_SBE, DMA_RESET_DONE = (1 << n for n in range(4))
M_COMMON = 0x33c0007e
S_COMMON = 0x03003e3e
M_MASK = 0xffc07fff
S_MASK = 0x0fc07f3f
DMA_TX_MASK, DMA_RX_MASK = 0x400f, 0x77ff
PACK0, PACK1 = 0x0007f8fe, 0x000ffefe
STOP_STRETCH = 4
ADDRESS_SHIFT = 9
ADDRESS_MASK = 0xfe00
SUPPORTED_PARAMS = ADDRESS_MASK | STOP_STRETCH
SUPPORTED_ADDRESS_OPS = (1, 2, 3, 4)

# Public helper's accepted clear-all write is retained exactly, including the
# SE_IRQ_EN write. Its model readback is the declared supported low four bits.
CLEAR_ALL = 0xffffffff

# Public SE labels and layout, selected by the complete original DT status and
# ancestor status. No original child device is attached to this virtual bus.
# (id, base, SPI, wrapper, current GCC index, vote offset, SE vote mask)
INSTANCES = (
    (1, 0x984000, 602, 0, 89, 0x52008, 0x800),
    (3, 0x98c000, 604, 0, 93, 0x52008, 0x2000),
    (5, 0x994000, 606, 0, 97, 0x52008, 0x8000),
    (9, 0xa84000, 354, 1, 107, 0x52008, 0x800000),
    (11, 0xa8c000, 356, 1, 111, 0x52008, 0x2000000),
    (13, 0xa94000, 358, 1, 115, 0x52008, 0x8000000),
    (15, 0x884000, 583, 2, 121, 0x52010, 0x20),
    (16, 0x888000, 584, 2, 123, 0x52010, 0x40),
    (19, 0x894000, 587, 2, 129, 0x52010, 0x200),
)
WRAPPER_CLOCKS = {
    0: (0x52008, 0x40, 0x80, 0x17000, 24),
    1: (0x52008, 0x100000, 0x200000, 0x18000, 25),
    2: (0x52010, 0x4, 0x2, 0x1e000, 26),
}
GCC_BASE = 0x100000

# Compact storage and serialization is a declared model interface, independent
# of implementation layout and separate from the sparse architectural offsets.
REGISTER_ORDER = (
    FORCE_DEFAULT, OUTPUT_CTRL, CGC, M_CLK, S_CLK, CLK_SEL, BYTE_GRAN,
    DMA_MODE, TX_PACK0, TX_PACK1, TX_LEN, RX_LEN, SCL, RX_PACK0, RX_PACK1,
    M_CMD, M_STATUS, M_EN, S_EN, TX_WM, RX_WM, RX_RFR,
    DMA_TX_PTR_L, DMA_TX_PTR_H, DMA_TX_ATTR, DMA_TX_LEN, DMA_TX_STATUS, DMA_TX_EN,
    DMA_RX_PTR_L, DMA_RX_PTR_H, DMA_RX_ATTR, DMA_RX_LEN, DMA_RX_STATUS, DMA_RX_EN,
    GSI_EN, GLOBAL_EN, DMA_GENERAL,
)
REGISTER_MASKS = (
    1, 0x7f, 0x37f, 0xfff1, 0xfff1, 7, 0,
    1, 0xfffff, 0xfffff, 0xffffffff, 0xffffffff, 0x3fffffff, 0xfffff, 0xfffff,
    0xffffffff, NACK | ILLEGAL | CANCEL | ABORT, M_MASK, S_MASK, 0x3f, 0x3f, 0x3f,
    0xffffffff, 0xffffffff, 1, 0xffffffff, DMA_RESET_DONE, DMA_TX_MASK,
    0xffffffff, 0xffffffff, 0, 0xffffffff, DMA_RESET_DONE, DMA_RX_MASK,
    0, 0xf, 0x1ff,
)
INTEGER_FIELDS = (
    'reference-identity', 'instance-id-readonly', 'physical-base', 'window-bytes',
    'interrupt-spi', 'wrapper-index', 'main-fw-revision', 'secondary-fw-revision',
    'main-config-revision', 'secondary-config-revision', 'fifo-words', 'fifo-word-bits',
    'ios-idle', 'tx-count', 'se-clock-period', 'm-ahb-clock-period', 's-ahb-clock-period',
    'vmstate-bytes', 'profile-wire-bytes', 'attached-slaves', 'tx-payload-bytes',
    'rx-payload-bytes', 'dma-read-bytes', 'dma-write-bytes',
)
BOOLEAN_FIELDS = (
    'enabled-reference', 'irq-level', 'reset-held', 'bus-available', 'engine-available',
    'command-active', 'firmware-executed', 'physical-state-captured',
    'dma-data-modeled', 'address-phase-modeled',
)
STRING_FIELDS = ('profile', 'registers-hex', 'tx-fifo-hex')
CONFIG_INTEGER_FIELDS = ('instance-id',)
GETTERS = INTEGER_FIELDS + CONFIG_INTEGER_FIELDS + BOOLEAN_FIELDS + STRING_FIELDS
GUARD_BYTES, VMSTATE_BYTES = 64, 305
GUARD_FIELD_OFFSETS = (
    ('enabled', 0, 4), ('identity', 4, 4), ('instance', 8, 4), ('base', 12, 8),
    ('size', 20, 4), ('spi', 24, 4), ('wrapper', 28, 4),
    ('main-fw', 32, 4), ('secondary-fw', 36, 4), ('main-config', 40, 4),
    ('secondary-config', 44, 4), ('fifo-words', 48, 4), ('fifo-bits', 52, 4),
    ('ios', 56, 4), ('slave-count', 60, 4),
)


def device_path(instance):
    assert instance in [x[0] for x in INSTANCES]
    return '/machine/geni-i2c-se' + str(instance)


def instance_row(instance):
    return next(x for x in INSTANCES if x[0] == instance)


def guard_bytes(instance, enabled):
    number, base, spi, wrapper, _, _, _ = instance_row(instance)
    return struct.pack('>IIIQ11I', int(enabled), PROFILE_ID, number, base,
                       WINDOW_BYTES, spi, wrapper, FW_REVISION, FW_REVISION,
                       0, 0, FIFO_WORDS, FIFO_WORD_BITS, IOS_IDLE, 0)


def register_values(state):
    raw = bytes.fromhex(state['registers-hex'])
    assert len(raw) == 148
    return dict(zip(REGISTER_ORDER, struct.unpack('>37I', raw)))


def expected_wire(instance, enabled, state):
    """Complete 305-byte section from the independent published wire contract."""
    reg = bytes.fromhex(state['registers-hex'])
    fifo = bytes.fromhex(state['tx-fifo-hex'])
    assert len(reg) == 148 and len(fifo) == 64
    value = guard_bytes(instance, enabled) + reg + fifo
    value += struct.pack('>IB3Q', state['tx-count'], int(state['reset-held']),
                         state['se-clock-period'], state['m-ahb-clock-period'],
                         state['s-ahb-clock-period'])
    assert len(value) == VMSTATE_BYTES
    return value


@dataclass(frozen=True)
class Op:
    direction: str
    offset: int
    value: int
    source_step: str

    def __post_init__(self):
        assert self.direction in ('read', 'write')
        assert type(self.offset) is int and 0 <= self.offset < WINDOW_BYTES and self.offset % 4 == 0
        assert type(self.value) is int and 0 <= self.value < (1 << 32)

    def command(self, base):
        command = f'{self.direction}l {base + self.offset:#x}'
        if self.direction == 'write':
            command += f' {self.value:#x}'
        return command

    def as_dict(self, base):
        return {'operation': self.direction, 'offset': self.offset, 'value': self.value,
                'command': self.command(base), 'source_step': self.source_step}


def read(offset, value, label):
    return Op('read', offset, value, label)


def write(offset, value, label):
    return Op('write', offset, value, label)


def cold_operations(frequency=400000):
    """First runtime-resume SE operations after the three GCC clocks are on."""
    clock_fields = {100000: (7, 10, 11, 26), 400000: (2, 5, 12, 24),
                    1000000: (1, 3, 9, 18)}
    assert frequency in clock_fields
    divisor, high, low, cycle = clock_fields[frequency]
    return [
        read(FW_MAIN, FW_REVISION, 'runtime_resume/get_se_proto'),
        read(FIFO_DISABLE, 0, 'runtime_resume/FIFO or GSI branch'),
        read(HW_PARAM0, HW_PARAM, 'runtime_resume/get_tx_fifo_depth'),
        read(CGC, 0, 'se_io_init/read CGC'),
        read(DMA_GENERAL, 0, 'se_io_init/read DMA general'),
        write(CGC, 0x7f, 'se_io_init/CGC RMW'),
        write(DMA_GENERAL, 0xf, 'se_io_init/DMA general RMW'),
        write(OUTPUT_CTRL, 0x7f, 'se_io_init/output control'),
        write(FORCE_DEFAULT, 1, 'se_io_init/force default'),
        read(GLOBAL_EN, 0, 'se_io_set_mode/read global enables'),
        read(DMA_MODE, 0, 'se_io_set_mode/read DMA mode'),
        write(GLOBAL_EN, 0xf, 'se_io_set_mode/global enables'),
        write(DMA_MODE, 0, 'se_io_set_mode/FIFO mode'),
        write(GSI_EN, 0, 'se_io_set_mode/no GSI'),
        write(RX_WM, FIFO_WORDS - 1, 'se_set_rx_rfr_wm/RX watermark'),
        write(RX_RFR, FIFO_WORDS, 'se_set_rx_rfr_wm/RFR watermark'),
        read(M_EN, 0, 'se_geni_irq_en/read main'),
        read(S_EN, 0, 'se_geni_irq_en/read secondary'),
        write(M_EN, M_COMMON, 'se_geni_irq_en/main RMW'),
        write(S_EN, S_COMMON, 'se_geni_irq_en/secondary RMW'),
        write(TX_PACK0, PACK0, 'se_config_packing/TX0'),
        write(TX_PACK1, PACK1, 'se_config_packing/TX1'),
        write(RX_PACK0, PACK0, 'se_config_packing/RX0'),
        write(RX_PACK1, PACK1, 'se_config_packing/RX1'),
        write(BYTE_GRAN, 0, 'se_config_packing/8 bit bytes'),
        write(CLK_SEL, 0, 'qcom_geni_i2c_conf/DFS0'),
        write(M_CLK, (divisor << 4) | 1, 'qcom_geni_i2c_conf/serial divisor'),
        write(SCL, (high << 20) | (low << 10) | cycle, 'qcom_geni_i2c_conf/SCL counters'),
    ]


def selected_mode_values(dma, main=M_COMMON, secondary=S_COMMON, prior_dma=0):
    assert type(dma) is bool and prior_dma in (0, 1)
    if dma:
        # 987 helper additionally clears DONE for non-UART/non-I3C protocols.
        # The original kernel's complete select_mode slice confirms this mask.
        return main & ~((1 << 30) | (1 << 26) | DONE), secondary, prior_dma | 1
    return (main | DONE | (1 << 30) | (1 << 26) | (1 << 27), secondary | DONE, prior_dma & ~1)


def select_mode_operations(dma, main=M_COMMON, secondary=S_COMMON, prior_dma=0):
    """Exact public FIFO/SE_DMA helper order; GSI is outside this policy."""
    selected_m, selected_s, selected_dma = selected_mode_values(dma, main, secondary, prior_dma)
    ops = [read(FW_MAIN, FW_REVISION, 'select_mode/get_se_proto'),
           write(GSI_EN, 0, 'select_mode/no GSI'),
           write(M_CLEAR, CLEAR_ALL, 'select_mode/clear main'),
           write(S_CLEAR, CLEAR_ALL, 'select_mode/clear secondary'),
           write(DMA_TX_CLEAR, CLEAR_ALL, 'select_mode/clear TX DMA'),
           write(DMA_RX_CLEAR, CLEAR_ALL, 'select_mode/clear RX DMA'),
           write(GLOBAL_EN, CLEAR_ALL, 'select_mode/global clear-all-form enable')]
    if dma:
        ops += [read(M_EN, main, 'select_dma/read main'),
                write(M_EN, selected_m, 'select_dma/disable FIFO watermarks'),
                read(DMA_MODE, prior_dma, 'select_dma/read mode'),
                write(DMA_MODE, selected_dma, 'select_dma/set mode')]
    else:
        ops += [read(M_EN, main, 'select_fifo/read main'),
                read(S_EN, secondary, 'select_fifo/read secondary'),
                read(DMA_MODE, prior_dma, 'select_fifo/read mode'),
                write(M_EN, selected_m, 'select_fifo/main RMW'),
                write(S_EN, selected_s, 'select_fifo/secondary RMW'),
                write(DMA_MODE, selected_dma, 'select_fifo/clear DMA mode')]
    return ops


def command_value(opcode, address, stretch=False):
    assert opcode in SUPPORTED_ADDRESS_OPS and 0 <= address <= 0x7f and type(stretch) is bool
    return (opcode << 27) | (address << ADDRESS_SHIFT) | (STOP_STRETCH if stretch else 0)


def expected_command_status(value):
    assert type(value) is int and 0 <= value < (1 << 32)
    opcode, params = value >> 27, value & 0x07ffffff
    return NACK if opcode in SUPPORTED_ADDRESS_OPS and not (params & ~SUPPORTED_PARAMS) else UNSUPPORTED_COMMAND


def dma_preparation_operations(receive, pointer, length):
    assert type(receive) is bool and type(pointer) is int and 0 <= pointer < (1 << 64)
    assert type(length) is int and 0 < length <= 0xffff
    high, low = pointer >> 32, pointer & 0xffffffff
    if receive:
        return [write(DMA_RX_EN_SET, 7, 'rx_dma_start/IRQ enables'),
                write(DMA_RX_PTR_L, low, 'rx_dma_start/pointer low'),
                write(DMA_RX_PTR_H, high, 'rx_dma_start/pointer high'),
                write(DMA_RX_ATTR, 0, 'rx_dma_start/no EOT attribute'),
                write(DMA_RX_LEN, length, 'rx_dma_start/length after barrier')]
    return [write(DMA_TX_EN_SET, 7, 'tx_dma_prep/IRQ enables'),
            write(DMA_TX_PTR_L, low, 'tx_dma_prep/pointer low'),
            write(DMA_TX_PTR_H, high, 'tx_dma_prep/pointer high'),
            write(DMA_TX_ATTR, 1, 'tx_dma_prep/EOT attribute'),
            write(DMA_TX_LEN, length, 'tx_dma_prep/length')]


def transfer_setup_operations(receive, length, address, *, pointer=0x81020000,
                              stretch=False, main=M_COMMON, secondary=S_COMMON,
                              prior_dma=0):
    """Descriptor setup deliberately follows M_CMD0, including DMA mode."""
    assert type(receive) is bool and type(length) is int and 0 <= length <= 0xffff
    dma = length > 32
    ops = select_mode_operations(dma, main, secondary, prior_dma)
    ops += [write(RX_LEN if receive else TX_LEN, length, 'xfer/transfer length'),
            write(M_CMD, command_value(2 if receive else 1, address, stretch), 'xfer/address operation')]
    if dma:
        ops += dma_preparation_operations(receive, pointer, length)
    elif not receive:
        ops += [write(TX_WM, 1, 'xfer/request TX FIFO watermark after command')]
    return ops


def error_irq_operations(dma, status=NACK):
    """Consumer's error branch never drains RX or fills TX payload."""
    assert type(dma) is bool and status in (NACK, CANCEL, ABORT, UNSUPPORTED_COMMAND)
    ops = [read(M_STATUS, status, 'irq/main status'),
           read(RX_STATUS, 0, 'irq/empty RX FIFO'),
           read(DMA_TX_STATUS, 0, 'irq/no TX DMA success'),
           read(DMA_RX_STATUS, 0, 'irq/no RX DMA success'),
           read(DMA_MODE, int(dma), 'irq/current mode')]
    if not dma:
        ops.append(write(TX_WM, 0, 'irq/error branch disables TX watermark'))
    ops.append(write(M_CLEAR, status, 'irq/W1C observed main status'))
    return ops


def error_cleanup_operations(receive, dma):
    ops = [write(M_CTRL, 4, 'error_cleanup/cancel')]
    ops += error_irq_operations(dma, CANCEL)
    if dma:
        ops.append(write(DMA_RX_RESET if receive else DMA_TX_RESET, 1,
                         'error_cleanup/reset DMA FSM'))
    return ops


DEBUG_READ_OFFSETS = (M_CMD, M_STATUS, S_CMD, S_STATUS, STATUS, IOS,
                      DMA_TX_STATUS, DMA_RX_STATUS, RX_STATUS, TX_STATUS,
                      DMA_DEBUG, M_CTRL, DMA_RX_LEN, DMA_RX_LEN_IN,
                      DMA_TX_LEN, DMA_TX_LEN_IN)


def expected_irq(*, enabled, reset, se_period, m_ahb_period, s_ahb_period,
                 global_enable, main_status, main_enable,
                 tx_status, tx_enable, rx_status, rx_enable):
    available = enabled and not reset and se_period != 0 and m_ahb_period != 0 and s_ahb_period != 0
    return bool(available and (
        (global_enable & 4 and main_status & main_enable) or
        (global_enable & 2 and tx_status & tx_enable) or
        (global_enable & 1 and rx_status & rx_enable)))
