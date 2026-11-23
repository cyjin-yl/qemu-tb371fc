#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""QTest the conditional AOP-derived 75-entry partial file.

No private firmware or reconstructed binary is distributed. The optional file
contains 69 static records plus five clocks and one alias agreed by both
documented allocator variants. These checks do not establish live RAM capture,
native PMIC enumeration, Linux probe or complete board migration.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import traceback

from test_rpmh_sm8250 import RpmhRig, check, ISSUED, COMPLETE

RECEIPT = (Path(__file__).resolve().parents[1] /
           'evidence/expansion-20261123/aop-vrm-allocator-replay.json')
CLOCKS = [('lnbclka1', 'ln_bb_clk1', 19_200_000),
          ('lnbclka2', 'ln_bb_clk2', 19_200_000),
          ('lnbclka3', 'ln_bb_clk3', 19_200_000),
          ('rfclka1', 'rf_clk1', 38_400_000),
          ('rfclka3', 'rf_clk3', 38_400_000)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--cmd-db-file', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path)
    args = parser.parse_args()
    args.qemu = str(args.qemu.resolve())
    args.qom_path = '/machine/rpmh'
    args.base = 0x18200000
    args.cmd_db_base = 0x80860000
    args.cmd_db_file = args.cmd_db_file.resolve()
    args.machine = ('tb371fc,rpmh-profile=file,rpmh-boot-configured=off,'
                    'ufs-enabled=off,rpmh-cmd-db=' + str(args.cmd_db_file))
    directory = args.work_dir or Path(tempfile.mkdtemp(prefix='tb-rpmh-clk-'))
    directory.mkdir(parents=True, exist_ok=True)
    provenance = json.loads(RECEIPT.read_text())
    digest = hashlib.sha256(args.cmd_db_file.read_bytes()).hexdigest()
    check(digest == provenance['private_clock_partial']['sha256'],
          'Unexpected partial command-db file')
    table = {row['name']: row for row in provenance['clocks_equal_in_both_variants']}
    with open(args.qemu, 'rb') as stream:
        binary_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    checks = []
    report = dict(schema_version=1, suite='AOP conditional clock partial-file actual QTest',
                  qemu=args.qemu, qemu_sha256=binary_sha, machine=args.machine,
                  firmware_sha256=provenance['firmware_sha256'],
                  command_db_sha256=digest, live_command_db_captured=False,
                  reconstructed_table_complete=False,
                  native_enumeration_proven=False, linux_driver_probe_tested=False,
                  expected_checks=6, checks=checks)
    rig = None
    try:
        rig = RpmhRig(args, directory)
        check(rig.monitor('qom-get', {'path': args.qom_path,
                                     'property': 'cmd-db-sha256'}) == digest,
              'Effective command-db digest differs')
        check(hashlib.sha256(rig.db()).hexdigest() == digest, 'Guest command-db bytes differ')
        check(table['vrm.xob']['address'] == table['lnbclka1']['address'],
              'Derived alias mismatch')
        checks.append(dict(check='exact_partial_file_and_alias', status='pass'))

        for name, clock, hz in CLOCKS:
            check(rig.resource(name, 'present') == 1, name + ' absent')
            check(rig.resource(name, 'address') == table[name]['address'],
                  name + ' address differs from original-function output')
            rig.expect_hz(clock, 0)
            rig.run_one(name, 1, address=table[name]['address'] + 4)
            rig.expect_hz(clock, hz)
            check(rig.status() == ISSUED | COMPLETE, name + ' did not complete')
            rig.run_one(name, 0, address=table[name]['address'] + 4)
            rig.expect_hz(clock, 0)
        checks.append(dict(check='all_five_derived_clock_addresses_gate_correct_dividers',
                           status='pass'))

        name, clock, hz = CLOCKS[0]
        address = table[name]['address'] + 4
        rig.run_one(name, 1, address=address, drv=0)
        rig.run_one(name, 1, address=address, drv=2)
        rig.run_one(name, 0, address=address, drv=2)
        rig.expect_hz(clock, hz)
        rig.run_one(name, 0, address=address, drv=0)
        rig.expect_hz(clock, 0)
        checks.append(dict(check='derived_clock_votes_aggregate_across_drvs', status='pass'))

        rig.run_one(name, 1, field=1)  # Explicitly targets old synthetic reference address.
        rig.expect_stall(0, name)
        rig.expect_hz(clock, 0)
        checks.append(dict(check='reference_address_has_no_fallback_or_ack', status='pass'))

        rig.run_one(name, 1, address=address)
        before = rig.resource(name)
        rig.run_one(name, 2, address=address)
        rig.expect_stall(before, name)
        rig.expect_hz(clock, hz)
        rig.run_one(name, 0, address=table[name]['address'])
        rig.expect_stall(before, name)
        rig.expect_hz(clock, hz)
        rig.run_one(name, 0, address=address)
        rig.expect_hz(clock, 0)
        checks.append(dict(check='invalid_clock_value_and_field_do_not_change_vote',
                           status='pass'))

        check(rig.resource('ldoa12', 'present') == 0, 'Excluded regulator invented')
        rig.run_one('ldoa12', 1, address=0x42104)
        rig.expect_stall(0, 'ldoa12')
        checks.append(dict(check='excluded_conditional_regulator_has_no_ack', status='pass'))
        report['success'] = True
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    finally:
        if rig:
            rig.close()
    print(json.dumps(report, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
