#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate an explicitly synthetic RPMh command-db and typed resource catalogue.

Linux provides resource names, command encodings and regulator voltage ranges.
It does NOT provide the SM8250 firmware resource addresses, ARC level maps or
BCM auxiliary values. Those fields below are a reproducible test profile.
No native firmware is redistributed by this generator.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

PINNED = {
    'drivers/regulator/qcom-rpmh-regulator.c': '282260691c1ff1e8ea1e059ffcf4a6c3938bcd14e0723055f01145a534ae12fb',
    'drivers/interconnect/qcom/sm8250.c': 'a3b64fc0c4755ce916b8f57193b7bbb9d124d5e623a21a6b3c0a50bb1f2caed1',
    'drivers/clk/qcom/clk-rpmh.c': '67a1aec12bd6e56e9964aa699b53618e41ba2cbdc58e91c4239d7d0217ca1554',
}
ARC_LEVELS = [0, 16, 48, 64, 128, 192, 256, 320, 384, 416, 448, 480]
SIZE = 0x20000


def generate(source, output):
    sources = {}
    for name, digest in PINNED.items():
        data = (source / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f'Unexpected Linux 6.12.114 source: {name}')
        sources[name] = data.decode()
    rows = []

    def add(name, kind, **kw):
        if not 1 <= len(name.encode()) <= 8 or any(r['name'] == name for r in rows):
            raise ValueError(f'Duplicate/invalid resource ID {name}')
        ordinal = sum(r['kind'] == kind for r in rows)
        slave = {'ARC': 3, 'VRM_CLOCK': 4, 'VRM_REGULATOR': 4, 'BCM': 5}[kind]
        if slave == 4:
            ordinal = sum(r['slave'] == 4 for r in rows)
        address = (slave << 16) + 0xe000 + ordinal * (4 if slave == 5 else 16)
        row = dict(index=len(rows), name=name, kind=kind, slave=slave,
                   address=address, aux_hex='', clock='', divider=0,
                   max_mv=0, modes=0, **kw)
        rows.append(row)
        return row

    row = add('xo.lvl', 'ARC')
    row.update(clock='bi_tcxo', divider=2, max_level=3)
    for name in ('cx.lvl', 'mx.lvl', 'mmcx.lvl', 'gfx.lvl', 'lcx.lvl', 'lmx.lvl', 'ebi.lvl'):
        row = add(name, 'ARC')
        row.update(aux_hex=struct.pack('<12H', *ARC_LEVELS).hex(), max_level=11)
    for name, clock, div in (('lnbclka1', 'ln_bb_clk1', 2), ('lnbclka2', 'ln_bb_clk2', 2),
                             ('lnbclka3', 'ln_bb_clk3', 2), ('rfclka1', 'rf_clk1', 1),
                             ('rfclka3', 'rf_clk3', 1)):
        row = add(name, 'VRM_CLOCK')
        row.update(clock=clock, divider=div)
    reg = sources['drivers/regulator/qcom-rpmh-regulator.c']
    for pmic, suffix in (('pm8150', 'a'), ('pm8150l', 'c'), ('pm8009', 'f')):
        block = re.search(r'static const struct rpmh_vreg_init_data ' + pmic +
                          r'_vreg_data\[\] = \{(.*?)\n\};', reg, re.S).group(1)
        for _, pattern, hardware in re.findall(
                r'RPMH_VREG\("([^"]+)",\s*"([^"]+)",\s*&(\w+),', block):
            body = re.search(r'static const struct rpmh_vreg_hw_data ' + hardware +
                             r' = \{(.*?)\n\};', reg, re.S).group(1)
            ranges = [tuple(map(int, m)) for m in re.findall(
                r'REGULATOR_LINEAR_RANGE\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)', body)]
            if not ranges:
                raise ValueError(f'Missing range for {hardware}')
            mode_family = re.search(r'\.pmic_mode_map = pmic_mode_map_pmic5_(\w+)', body).group(1)
            modes = {'ldo': [3, 4, 7], 'smps': [3, 4, 6, 7], 'bob': [2, 4, 6, 7]}[mode_family]
            row = add(pattern % suffix, 'VRM_REGULATOR')
            row.update(max_mv=max((v + (hi - lo) * step + 999) // 1000
                                  for v, lo, hi, step in ranges),
                       modes=sum(1 << m for m in modes), reference_pmic=pmic,
                       linux_hardware=hardware, linux_voltage_ranges_uv=ranges)
    bcm = re.findall(r'static struct qcom_icc_bcm\s+\w+\s*=\s*\{\s*\.name\s*=\s*"([^"]+)"',
                     sources['drivers/interconnect/qcom/sm8250.c'])
    for name in ['IP0'] + bcm:
        row = add(name, 'BCM')
        # Test profile: one shared VCD, unit 1000, width 1. Not captured values.
        row.update(aux_hex=struct.pack('<IHBB', 1000, 1, 0, 0).hex())
        if name == 'IP0':
            row.update(clock='ipa', divider=1)
    if len(rows) > 128:
        raise ValueError('Resource capacity exceeded')

    image = bytearray(SIZE)
    struct.pack_into('<II', image, 0, 1, 0x0c0330db)
    cursor = 144
    for group, slave in enumerate((3, 4, 5)):
        entries = [r for r in rows if r['slave'] == slave]
        header_start = cursor
        cursor += len(entries) * 24
        data_start = cursor
        for j, row in enumerate(entries):
            aux = bytes.fromhex(row['aux_hex'])
            struct.pack_into('<8sIIIHH', image, header_start + j * 24,
                             row['name'].encode(), 0, 0, row['address'],
                             len(aux), cursor - data_start)
            image[cursor:cursor + len(aux)] = aux
            cursor += len(aux)
        struct.pack_into('<8H', image, 8 + group * 16, slave,
                         header_start - 144, data_start - 144, len(entries),
                         0x100, 0, 0, 0)
    if cursor >= 65536:
        raise ValueError('Command-db 16-bit offset overflow')
    out_inc = output / 'hw/misc/qcom-sm8250-rpmh-data.inc'
    lines = ['/* SPDX-License-Identifier: GPL-2.0-only */',
             '/* Generated by tb371fc/scripts/generate_rpmh_reference.py.',
             ' * Linux-derived names and regulator contracts; addresses and aux',
             ' * are an explicitly synthetic reference profile, NOT tablet data.',
             ' */', 'static const RpmhResourceDesc rpmh_resources[] = {']
    for row in rows:
        levels = list(struct.unpack('<' + 'H' * (len(bytes.fromhex(row['aux_hex'])) // 2),
                                    bytes.fromhex(row['aux_hex']))) if row['kind'] == 'ARC' else []
        lines += [f'    {{ "{row["name"]}", RPMH_{row["kind"]}, 0x{row["address"]:x},',
                  f'      {row["max_mv"]}, 0x{row["modes"]:x}, {row.get("max_level", 0)},',
                  f'      "{row["clock"]}", {row["divider"]},',
                  '      { ' + ', '.join(map(str, levels or [0])) + ' } },']
    lines += ['};', '']
    out_inc.write_text('\n'.join(lines))
    manifest = dict(schema_version=1, profile='synthetic-sm8250-reference-v1',
                    native_table=False, native_voltage_calibration=False,
                    source_release='Linux 6.12.114', source_sha256=PINNED,
                    command_db_sha256=hashlib.sha256(image).hexdigest(),
                    command_db_size=SIZE, used_bytes=cursor,
                    synthetic_fields=['resource addresses', 'ARC aux level maps',
                                      'BCM unit=1000,width=1,VCD=0', 'reset votes=0'],
                    resources=rows)
    fixture_dir = output / 'tb371fc/fixtures'
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / 'rpmh-reference.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (fixture_dir / 'rpmh-reference-cmd-db.bin').write_bytes(image)
    print(json.dumps(dict(resources=len(rows), sha256=manifest['command_db_sha256'],
                          used_bytes=cursor)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    generate(args.linux_source, args.output)
