#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate SM8250 GCC descriptors with one audited downstream supplement.

The parser deliberately accepts one audited source revision only. It extracts
register facts, not arbitrary C. Run in the development container:
  python3 tb371fc/scripts/generate_gcc_sm8250.py /path/to/linux-6.12.114

The fixed Kona supplement restores the documented SYS_NOC_CPUSS halt register
and parent omitted by upstream's probe-only enable. Its primary source and
hash are emitted in the manifest; other unknown parents remain unresolved.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

PIN = 'linux-6.12.114'
PRIMARY = 'https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/'
SYMBOLS = {'P_BI_TCXO': -1, 'P_SLEEP_CLK': -2, 'P_AUD_REF_CLK': -3,
           'P_GPLL0_OUT_MAIN': 0, 'P_GPLL0_OUT_EVEN': 1,
           'P_GPLL4_OUT_MAIN': 2, 'P_GPLL9_OUT_MAIN': 3}

def required(pattern, text):
    m = re.search(pattern, text)
    if not m:
        raise ValueError('unsupported source shape: ' + pattern)
    return m

def number(body, key, default=None):
    m = re.search(r'\.' + re.escape(key) + r'\s*=\s*(0x[0-9a-f]+|[0-9]+)\b', body)
    if m:
        return int(m.group(1), 0)
    if default is None:
        raise ValueError('missing integer ' + key)
    return default

def bit(body, key, default=None):
    m = re.search(r'\.' + re.escape(key) + r'\s*=\s*BIT\(([0-9]+)\)', body)
    if m:
        return 1 << int(m.group(1))
    if default is None:
        raise ValueError('missing bit ' + key)
    return default

def definitions(source, typ):
    pattern = r'static (?:const )?struct ' + typ + r'\s+(\w+)(?:\[\])?\s*=\s*\{([\s\S]*?)\n\};'
    return [(m.group(1), m.group(2), source.count('\n', 0, m.start()) + 1)
            for m in re.finditer(pattern, source)]

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('linux', type=Path)
    ap.add_argument('--output-root', type=Path, default=Path(__file__).resolve().parents[2])
    args = ap.parse_args()
    driver = args.linux / 'drivers/clk/qcom/gcc-sm8250.c'
    binding = args.linux / 'include/dt-bindings/clock/qcom,gcc-sm8250.h'
    source, header = driver.read_text(), binding.read_text()
    hashes = {str(p.relative_to(args.linux)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (driver, binding)}
    # These checks prevent a changed driver from silently producing partial data.
    if hashes['drivers/clk/qcom/gcc-sm8250.c'] != 'b3b3865de3a297f906d5562a720d8d304538cef3c8d3703dee026cfd6dba06c8':
        raise ValueError('gcc-sm8250.c differs from the audited Linux 6.12.114 source')
    if hashes['include/dt-bindings/clock/qcom,gcc-sm8250.h'] != '34682c01e5b21bb13db125afa3504c73606c294c23cfb3a96f6ea52cdffd2ff7':
        raise ValueError('binding differs from the audited Linux 6.12.114 source')
    ids = {name: int(value) for name, value in re.findall(r'^#define\s+(\w+)\s+(\d+)\s*$', header, re.M)}
    clock_array = required(r'static struct clk_regmap \*gcc_sm8250_clocks\[\] = \{([\s\S]*?)\n\};', source).group(1)
    clock_ids = {name: ids[symbol] for symbol, name in re.findall(r'\[(\w+)\]\s*=\s*&(\w+)\.clkr', clock_array)}
    parent_maps = {}
    for name, body, _ in definitions(source, 'parent_map'):
        parent_maps[name] = {int(sel): SYMBOLS[parent]
                            for parent, sel in re.findall(r'\{\s*(P_\w+),\s*(\d+)\s*\}', body)}
    clocks = []
    def common(name, body, line, kind):
        parent = re.search(r'&(\w+)\.clkr\.hw', body)
        return dict(id=clock_ids[name], name=name, kind=kind, line=line,
                    reg=0, enable_reg=0, enable_mask=0, parent=clock_ids[parent.group(1)] if parent else -4,
                    mnd_width=0, hid_width=0, hwcg_reg=0, hwcg_mask=0,
                    parent_map=[parent_maps['gcc_parent_map_3'].get(i, -127) for i in range(8)],
                    external=-1)
    for typ, kind in [('clk_alpha_pll', 'PLL'), ('clk_alpha_pll_postdiv', 'POSTDIV'),
                      ('clk_rcg2', 'RCG'), ('clk_regmap_div', 'DIV'), ('clk_branch', 'BRANCH')]:
        for name, body, line in definitions(source, typ):
            d = common(name, body, line, kind)
            if kind == 'PLL':
                d.update(reg=number(body, 'offset'), enable_reg=number(body, 'enable_reg'),
                         enable_mask=bit(body, 'enable_mask'), parent=-1)
            elif kind == 'POSTDIV':
                d.update(reg=number(body, 'offset') + 0xc, hid_width=number(body, 'width'),
                         mnd_width=number(body, 'post_div_shift'))
            elif kind == 'RCG':
                mapname = required(r'\.parent_map\s*=\s*(\w+)', body).group(1)
                d.update(reg=number(body, 'cmd_rcgr'), mnd_width=number(body, 'mnd_width'),
                         hid_width=number(body, 'hid_width'),
                         parent_map=[parent_maps[mapname].get(i, -127) for i in range(8)])
                d['frequency_table'] = required(r'\.freq_tbl\s*=\s*(\w+)', body).group(1)
            elif kind == 'DIV':
                d.update(reg=number(body, 'reg'), mnd_width=number(body, 'shift'), hid_width=number(body, 'width'))
            else:
                d.update(reg=number(body, 'halt_reg', 0xffffffff), enable_reg=number(body, 'enable_reg'),
                         enable_mask=bit(body, 'enable_mask'), hwcg_reg=number(body, 'hwcg_reg', 0),
                         hwcg_mask=1 << number(body, 'hwcg_bit', 0) if '.hwcg_reg' in body else 0)
                d['halt_check'] = required(r'\.halt_check\s*=\s*(\w+)', body).group(1)
                if required(r'\.ops\s*=\s*&(\w+)', body).group(1) != 'clk_branch2_ops':
                    raise ValueError('unsupported branch operation')
            clocks.append(d)
    if len(clocks) != 192:
        raise ValueError('expected 192 explicit clock descriptions')
    # These six CBCRs are deliberately enabled in gcc_sm8250_probe(), without
    # corresponding clk_branch declarations. Keep that provenance explicit.
    for symbol, reg in [('GCC_VIDEO_AHB_CLK', 0xb004), ('GCC_CAMERA_AHB_CLK', 0xb008),
                        ('GCC_DISP_AHB_CLK', 0xb00c), ('GCC_CPUSS_DVM_BUS_CLK', 0x4818c),
                        ('GCC_GPU_CFG_AHB_CLK', 0x71004), ('GCC_SYS_NOC_CPUSS_AHB_CLK', 0x52000)]:
        name = symbol.lower()
        pos = required(r'qcom_branch_set_clk_en\(regmap, 0x0*' + format(reg, 'x') + r'\)', source).start()
        clocks.append(dict(id=ids[symbol], name=name, kind='BRANCH', line=source.count('\n', 0, pos) + 1,
                           reg=0xffffffff, enable_reg=reg, enable_mask=1, parent=-4, mnd_width=0, hid_width=0,
                           hwcg_reg=0, hwcg_mask=0, parent_map=[-127]*8, external=-1,
                           halt_check='probe-only CBCR'))
    clocks.sort(key=lambda d: d['id'])
    ext = 0
    for d in clocks:
        if d['kind'] == 'BRANCH' and d['parent'] == -4:
            d['external'] = ext
            ext += 1
    if ext != 84 or len({d['id'] for d in clocks}) != 198:
        raise ValueError('unexpected external clock or duplicate binding')
    # Assign the original external slots first: VMState v1 serializes all 84
    # Clock inputs in this order. Slot 63 and its old QOM name remain present,
    # but this branch's recovered internal parent determines its actual rate.
    cpuss = next(d for d in clocks if d['id'] == 143)
    if (cpuss['name'], cpuss['reg'], cpuss['enable_reg'],
            cpuss['enable_mask'], cpuss['parent'], cpuss['external']) != (
            'gcc_sys_noc_cpuss_ahb_clk', 0xffffffff, 0x52000, 1, -4, 63):
        raise ValueError('unexpected upstream SYS_NOC_CPUSS description')
    cpuss_parent = next(d for d in clocks if d['id'] == 18)
    if cpuss_parent['name'] != 'gcc_cpuss_ahb_postdiv_clk_src':
        raise ValueError('unexpected CPUSS parent binding')
    cpuss.update(reg=0x48198, parent=18, halt_check='BRANCH_HALT_VOTED')
    cpuss['source_supplement'] = {
        'source_url': 'https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/'
                      '94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gcc-kona.c',
        'sha256': 'ed208678508c983d0989f1f135fac898c5a9a91c14af1fa42cd7ea2f24cd14cd',
        'line': 3265,
        'fields': ['reg', 'parent', 'halt_check'],
        'driver_flags': ['CLK_IS_CRITICAL', 'CLK_SET_RATE_PARENT'],
        'legacy_external_slot': 63,
    }
    resets = []
    body = required(r'static const struct qcom_reset_map gcc_sm8250_resets\[\] = \{([\s\S]*?)\n\};', source).group(1)
    for name, reg, more in re.findall(r'\[(\w+)\]\s*=\s*\{\s*(0x[0-9a-f]+)([^}]*)\}', body):
        resets.append(dict(id=ids[name], name=name, reg=int(reg, 16), mask=1 << number(more, 'bit', 0)))
    gdsc_ids = {name: ids[symbol] for symbol, name in re.findall(r'\[(\w+)\]\s*=\s*&(\w+),', required(r'static struct gdsc \*gcc_sm8250_gdscs\[\] = \{([\s\S]*?)\n\};', source).group(1))}
    gdscs = [dict(id=gdsc_ids[name], name=name, reg=number(body, 'gdscr'), line=line,
                  votable='VOTABLE' in body, retention='PWRSTS_RET_ON' in body)
             for name, body, line in definitions(source, 'gdsc')]
    if len(resets) != 45 or len(gdscs) != 11:
        raise ValueError('unexpected reset/power binding count')
    # Known addresses and write/RO masks: no catch-all MMIO backing store.
    regs = {}
    def add(reg, rw=0, ro=0):
        d = regs.setdefault(reg, [0, 0])
        d[0] |= rw
        d[1] |= ro
    for d in clocks:
        r = d['reg']; kind = d['kind']
        if kind == 'PLL':
            add(r, 0x01ffff87, 0xf0000000)
            for off in (4, 8, 0x40): add(r + off, 0xffff)
            add(r + 0xc, 0xf07)
            for off in (0x10, 0x14, 0x18, 0x1c, 0x20, 0x24, 0x28, 0x2c): add(r + off)
            add(r + 0x30, ro=1 << 27)
            add(r + 0x38, 1)
            add(d['enable_reg'], d['enable_mask'])
        elif kind == 'RCG':
            add(r, 3, 0x800000f0)
            add(r + 4, 0x103700 | ((1 << d['hid_width']) - 1))
            if d['mnd_width']:
                for off in (8, 12, 16): add(r + off, (1 << d['mnd_width']) - 1)
            if 'qupv3' in d['name']: add(r + 0x14)
        elif kind == 'DIV':
            # Linux declares these read-only. POR is a model default divide1.
            add(r, ro=((1 << d['hid_width']) - 1) << d['mnd_width'])
        elif kind == 'BRANCH':
            if r != 0xffffffff: add(r, ro=0xf0000000)
            add(d['enable_reg'], d['enable_mask'])
            if d['hwcg_reg']: add(d['hwcg_reg'], d['hwcg_mask'])
    for d in resets: add(d['reg'], d['mask'])
    for d in gdscs: add(d['reg'], 0xfff007, 0x80000000)
    add(0x4d110, 3); add(0x71028, 3)
    for reg, (rw, ro) in regs.items():
        if rw & ro: raise ValueError('overlapping rw/ro bits at ' + hex(reg))
    manifest = dict(schema=1, source_revision=PIN, source_url=PRIMARY + 'drivers/clk/qcom/gcc-sm8250.c?h=v6.12.114',
                    sha256=hashes, clocks=clocks, resets=resets, power_domains=gdscs,
                    registers=[dict(offset=reg, write_mask=rw, status_mask=ro) for reg, (rw, ro) in sorted(regs.items())])
    (args.output_root / 'tb371fc/docs/gcc-sm8250-registers.json').write_text(json.dumps(manifest, indent=2) + '\n')
    out = ['/* SPDX-License-Identifier: GPL-2.0-only */',
           '/* Generated Linux v6.12.114 GCC facts and a pinned Kona supplement.',
           ' * Source copyright (c) 2020, The Linux Foundation. All rights reserved.',
           ' * Regenerate with tb371fc/scripts/generate_gcc_sm8250.py; see manifest.',
           ' */', 'static const GccClockDesc gcc_clocks[GCC_CLOCKS] = {']
    for d in clocks:
        pm = ', '.join(str(v) for v in d['parent_map'])
        if 'source_supplement' in d:
            out.append('    /* Kona line 3265: halt/parent; legacy external slot 63 retained. */')
        out += [f'    /* upstream line {d["line"]} */',
                f'    [{d["id"]}] = {{ "{d["name"]}", GCC_{d["kind"]},',
                f'        0x{d["reg"]:x}, 0x{d["enable_reg"]:x}, 0x{d["enable_mask"]:x}, {d["parent"]},',
                f'        {d["mnd_width"]}, {d["hid_width"]}, 0x{d["hwcg_reg"]:x}, 0x{d["hwcg_mask"]:x},',
                f'        {{ {pm} }}, {d["external"]} }},']
    out += ['};', 'static const GccResetDesc gcc_resets[GCC_RESETS] = {']
    for d in sorted(resets, key=lambda d:d['id']):
        out.append(f'    [{d["id"]}] = {{ 0x{d["reg"]:x}, 0x{d["mask"]:x} }}, /* {d["name"]} */')
    out += ['};', 'static const GccPowerDesc gcc_power[GCC_POWER_DOMAINS] = {']
    for d in sorted(gdscs, key=lambda d:d['id']):
        out.append(f'    [{d["id"]}] = {{ 0x{d["reg"]:x} }}, /* {d["name"]} */')
    out += ['};', 'static const GccRegisterDesc gcc_registers[] = {']
    for reg, (rw, ro) in sorted(regs.items()):
        out.append(f'    {{ 0x{reg:05x}, 0x{rw:08x}, 0x{ro:08x} }},')
    out += ['};', '']
    (args.output_root / 'hw/misc/qcom-sm8250-gcc-data.inc').write_text('\n'.join(out))
    print(json.dumps({'clock_descriptors':len(clocks), 'external_parents':ext,
                      'registers':len(regs), 'resets':len(resets), 'power_domains':len(gdscs)}))

if __name__ == '__main__':
    main()
