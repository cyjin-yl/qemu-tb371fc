#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise a private, partial AOP-derived command-db with actual QTest.

Requires the explicitly reconstructed static table, not a native live RAM
capture. Does not claim dynamic VRM, AOP firmware execution, or Linux probe.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import traceback

from test_rpmh_sm8250 import RpmhRig, check, ISSUED, COMPLETE

RECEIPT = Path(__file__).resolve().parents[1] / 'evidence/expansion-20261123/aop-command-db-static.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--cmd-db-file', required=True, type=Path)
    parser.add_argument('--work-dir', type=Path)
    args = parser.parse_args()
    args.qom_path = '/machine/rpmh'
    args.base = 0x18200000
    args.cmd_db_base = 0x80860000
    args.cmd_db_file = args.cmd_db_file.resolve()
    args.machine = ('tb371fc,rpmh-profile=file,rpmh-boot-configured=off,'
                    'ufs-enabled=off,rpmh-cmd-db=' + str(args.cmd_db_file))
    directory = args.work_dir or Path(tempfile.mkdtemp(prefix='tb371fc-rpmh-aop-'))
    directory.mkdir(parents=True, exist_ok=True)
    provenance = json.loads(RECEIPT.read_text())
    digest = hashlib.sha256(args.cmd_db_file.read_bytes()).hexdigest()
    check(digest == provenance['reconstructed_sha256'], 'Unexpected partial database')
    table = {r['name']: r for g in provenance['groups'] for r in g['entries']}
    with open(args.qemu, 'rb') as stream:
        qemu_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
    checks = []
    report = dict(schema_version=1, suite='AOP static partial-table actual QTest',
                  qemu=args.qemu, qemu_sha256=qemu_sha256, machine=args.machine,
                  firmware_sha256=provenance['firmware_sha256'],
                  reconstructed_command_db_sha256=digest,
                  live_command_db_captured=False, reconstructed_table_complete=False,
                  dynamic_vrm_available=False, aop_firmware_executed=False,
                  linux_driver_probe_tested=False, expected_checks=6, checks=checks)
    rig = None
    try:
        rig = RpmhRig(args, directory)
        check(rig.monitor('qom-get', {'path': args.qom_path, 'property': 'cmd-db-sha256'}) == digest,
              'effective database differs')
        check(rig.resource('xo.lvl', 'address') == table['xo.lvl']['address'], 'XO address')
        checks.append(dict(check='exact_loaded_partial_database_and_source_address', status='pass'))
        rig.run_one('xo.lvl', 3, address=table['xo.lvl']['address'])
        rig.expect_hz('bi_tcxo', 19_200_000)
        check(rig.status() == ISSUED | COMPLETE, 'native-address XO request not completed')
        checks.append(dict(check='firmware_static_xo_address_controls_clock', status='pass'))
        levels = table['cx.lvl']['arc_levels_or_mol']
        count = next(i for i in range(1, len(levels)) if levels[i] == 0)
        check(rig.resource('cx.lvl', 'level-count') == count, 'firmware ARC aux map ignored')
        rig.run_one('cx.lvl', count - 1, address=table['cx.lvl']['address'])
        check(rig.resource('cx.lvl') == count - 1, 'last valid hardware-level index rejected')
        rig.run_one('cx.lvl', count, address=table['cx.lvl']['address'])
        rig.expect_stall(count - 1, 'cx.lvl')
        checks.append(dict(check='firmware_arc_aux_bounds_enforced', status='pass'))
        check(table['IP0']['bcm']['vcd'] != table['CE0']['bcm']['vcd'], 'expected independent firmware VCDs')
        rig.run_one('IP0', 0x20000000 | 123, address=table['IP0']['address'])
        rig.expect_hz('ipa', 0)
        rig.run_one('CE0', 0x60000001, address=table['CE0']['address'])
        rig.expect_hz('ipa', 0)  # Commit in VCD6 must not commit staged VCD7.
        rig.run_one('IP0', 0x60000000 | 123, address=table['IP0']['address'])
        rig.expect_hz('ipa', 123 * table['IP0']['bcm']['unit'] * 1000)
        checks.append(dict(check='firmware_bcm_address_unit_and_vcd_isolation_applied', status='pass'))
        check(rig.resource('ACV', 'present') == 1, 'zero-width BCM not loaded')
        rig.run_one('ACV', 0x60000008, address=table['ACV']['address'])
        check(rig.resource('ACV', 'enabled') == 1, 'enable-mask BCM request not applied')
        checks.append(dict(check='zero_width_firmware_bcm_serviced_as_digital_vote', status='pass'))
        check(rig.resource('lnbclka1', 'present') == 0, 'missing VRM secretly invented')
        rig.run_one('lnbclka1', 1, field=1)
        rig.expect_stall(0, 'lnbclka1')
        rig.expect_hz('ln_bb_clk1', 0)
        checks.append(dict(check='unresolved_vrm_has_no_reference_fallback_or_ack', status='pass'))
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
