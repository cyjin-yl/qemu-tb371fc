#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real MMIO tests for the opt-in SDE RSC initialization reference.

The vendor initialization access sequence is exercised, not the guest driver.
The reference intentionally cannot start timers, a solver, sequence execution,
bandwidth transactions or power collapse. Migration streams remain unmodified.
"""
import argparse
import json
from pathlib import Path
import re
import traceback

from qce_bam_test_transport import Rig, file_channels, put_json, sha, verify_cleanup

TYPE = 'qcom-sm8250-sde-rsc'
DRV, WRAPPER = 0x0af20000, 0x0af30000
PROFILE = 0x52534301
WREGS = (0,4,8,0xc,0x10,0x18,0x1c,0x20,0x24,0x40,0x44,0x48,0x50,
         0x1040,0x1044,0x1fe0,0x1ff0,0x2020,0x2024,0x202c,0x3020,0x3024,0x302c)
DREGS = (0x20,0x24,0x28,0x2c,0x404,0x408,0x410,0x414,
         0xc00,0xc04,0xc08,0xc0c,0xc14,0xc18,0xc1c,0xc20,0xc24,0xc28,0xc2c,
         0xc30,0xc34,0xc38,0xc40,0xc4c,0xc50,0xc54,0xc5c,0xc68,0xc6c,0xc70,0xc78,
         0x1000,0x1004,0x1008,0x100c,0x1080,0x1100,0x110c,0x1180,0x118c)
SEQUENCE = (0xff399ebe,0x20209ebe,0x20202020,0xe0389ebe,0x9ebeff39,
            0x20202020,0xbdf9b9a0,0xa13899fe,0xe0ac81e1,0x3982e2a2,
            0x208cfd9d,0x20202020,0x20202020,0x01a6fcbc,0x20209ce6,
            0x01a7fcbc,0x00209ce7)
DRV_WINDOWS = ((0x20,0x10),(0x404,0x14),(0x600,0x44),(0xc00,0x7c),
               (0x1000,0x10),(0x1080,4),(0x1100,4),(0x110c,4),(0x1180,4),(0x118c,4))
PROTECTED = (0,4,0xc,0x38,0x40,0x1c00,0x1c04,0x1c08,0x1c10,0x1c14,
             0x1c18,0x1c1c,0x1c30,0x1c34,0x1c38,0x1c3c,0x1c40,
             0x1ea0,0x1eb0,0x1eb4,0x1eb8,0x1ebc,0x1ed0)
# TCS1's base is owned by RPMh but its offset-zero read is unsupported.
# Check that existing refusal explicitly instead of treating it as a register.
RPMH_READABLE = tuple(x for x in PROTECTED if x != 0x1ea0)
ALL_REGS = [(WRAPPER,x) for x in WREGS] + [(DRV,x) for x in DREGS]
ALL_REGS += [(DRV,0x600+4*i) for i in range(17)]
MACHINE = 'tb371fc,ufs-enabled=off,rpmh-profile=reference,rpmh-boot-configured=off'
ON_MACHINE = MACHINE + ',sde-rsc-init-reference=on'
REQUIRED = [
    'default-off-has-no-sde-apertures-or-profile-child',
    'opt-in-without-rpmh-fails-realize',
    'opt-in-exact-windows-immutable-profile-and-zero-reset',
    'actual-mmio-vendor-v3-initialization-write-sequence',
    'disabled-compare-storage-and-inert-enabled-timer-boundary',
    'active-solver-bandwidth-and-power-requests-reject-without-mutation',
    'strict-width-alignment-holes-and-address-space-boundaries',
    'shared-rpmh-identity-tcs-completion-and-irq-status-are-preserved',
    'virtual-time-does-not-create-solver-timer-vsync-or-power-completion',
    'system-reset-clears-configuration-and-all-sequence-words',
    'ordinary-checkpoint-preserves-configuration-and-continuation',
    'every-created-child-is-actually-waited-and-reaped',
]

if not __debug__:
    raise SystemExit('Acceptance requires assertions; do not use -O.')


class RSCRig(Rig):
    # Each fresh QTest process starts at zero; retain actual clock replies.
    clock_ns = 0

    def step(self, ns):
        assert ns > 0
        actual = int(self.qt(f'clock_step {ns}')[0], 0)
        assert actual == self.clock_ns + ns
        self.clock_ns = actual
        return actual

    def set_clock(self, ns):
        assert ns >= self.clock_ns, 'QTest time cannot move backwards'
        actual = int(self.qt(f'clock_set {ns}')[0], 0)
        assert actual == ns
        self.clock_ns = actual
        return actual

    def read_rsc(self, base, offset, width='l'):
        self.count['device_mmio'] += 1
        return int(self.qt(f'read{width} {base+offset:#x}')[0], 0)

    def write_rsc(self, base, offset, value, width='l'):
        self.count['device_mmio'] += 1
        self.qt(f'write{width} {base+offset:#x} {value:#x}')

    def state(self):
        return dict(config=[self.read_rsc(b,o) for b,o in ALL_REGS],
                    rpmh=[self.read_rsc(DRV,o) for o in RPMH_READABLE],
                    apps=[self.read_rsc(0x18200000+i*0x10000,0) for i in range(3)])

    def qget(self, path, prop):
        return self.monitor('qom-get', {'path':path,'property':prop})


def rejects(rig, base, offset, *, write=True, width='l', value=1):
    state = rig.state()
    before = rig.stderr()
    if write:
        rig.write_rsc(base,offset,value,width)
    else:
        rig.read_rsc(base,offset,width)
    added = rig.stderr()[len(before):]
    prefix = f"{TYPE}: rejected {'wrapper' if base==WRAPPER else 'drv'} {'write' if write else 'read'}"
    size = {'b':1,'w':2,'l':4,'q':8}[width]
    expected = f'{prefix} offset={offset:#x} size={size}'
    assert added.splitlines().count(expected) == 1, added
    assert len([x for x in added.splitlines() if TYPE+': rejected' in x]) == 1
    assert rig.state() == state
    return dict(base=hex(base),offset=hex(offset),write=write,width=width,
                value=value if write else None,diagnostic=added.strip(),state_unchanged=True)


def flatmap(rig,path):
    raw = rig.monitor('human-monitor-command',{'command-line':'info mtree -f'})
    path.write_text(raw)
    rows=[]
    for line in raw.splitlines():
        m=re.search(r'([0-9a-fA-F]+)-([0-9a-fA-F]+).*?: ([^:]+)$',line)
        if m: rows.append((int(m[1],16),int(m[2],16),m[3].strip()))
    return rows,dict(path=str(path),bytes=path.stat().st_size,sha256=sha(path))


def init_sequence():
    # Fixed primary sde_rsc_hw_v3.c order, with distinct test timer parameters.
    # The exact guest binary's ID read and first wrapper write were independently
    # decoded after native009; this fixture does not execute that binary.
    w=[(WRAPPER,o,v) for o,v in [
       (0x1fe0,0xffffffff),(0x1ff0,0xffffffff),(0x1040,1),(0x1044,1),
       (0x2020,0xffffffff),(0x2024,0xffffffff),(0x3020,0xffffffff),(0x3024,0xffffffff),
       (0x202c,1),(0x302c,1),(8,100),(0xc,200),(4,0x100),(0x44,300)]]
    w += [(DRV,0x600+4*i,v) for i,v in enumerate(SEQUENCE)]
    w += [(DRV,o,v) for o,v in [
       (0x410,0x34),(0x414,0x3c),(0xc14,0),(0xc30,0),(0xc4c,0xc),(0xc68,0x18),
       (0xc04,0xffffffff),(0xc08,0xffffffff),(0xc0c,0xefffffff),
       (0x1000,0),(0x1004,400),(0x1008,500),(0x100c,600),(0xc20,7),
       (0x1080,0),(0x1100,1),(0x110c,1),(0x1180,2),(0x118c,2),
       (0xc18,0),(0xc00,1),(0xc1c,0x01000010),
       (0xc34,0x80000000),(0xc38,700),(0xc40,800),
       (0xc50,0x80000000),(0xc54,1400),(0xc5c,800),
       (0xc6c,0x80000000),(0xc70,0),(0xc78,800),
       (0x20,0x4520),(0x24,0x4510),(0x28,0x4514),(0x2c,1)]]
    return w


def run_initializer(rig):
    before=rig.stderr()
    assert rig.read_rsc(DRV,0)==0x20700
    expected={(b,o):0 for b,o in ALL_REGS}
    writes=init_sequence()
    for b,o,v in writes:
        rig.write_rsc(b,o,v);expected[b,o]=v
    assert [rig.read_rsc(b,o) for b,o in ALL_REGS]==[expected[b,o] for b,o in ALL_REGS]
    assert rig.stderr()==before
    return [dict(base=hex(b),offset=hex(o),value=v) for b,o,v in writes]


def continuation(rig):
    rig.write_rsc(WRAPPER,8,0xabcdef01)
    rig.write_rsc(DRV,0x1008,0x0fedcba9)
    rig.write_rsc(DRV,0x640,0x76543210)
    rig.write_rsc(WRAPPER,0x202c,0)
    rig.write_rsc(WRAPPER,0x2020,0x76543210)
    return rig.state()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu',required=True)
    p.add_argument('--out',required=True,type=Path)
    args=p.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    (out/'streams').mkdir()
    report=dict(schema='tb371fc-sde-rsc-init-reference-v1',passed=False,
                qemu_sha256=sha(args.qemu),test_sha256=sha(__file__),checks=[],
                processes=[],migrations=[],access_rejections=[],profile_id=PROFILE,
                original_android_executed=False,guest_cpu_executed=False,
                native_fault_address='0xaf31fe0',configuration_registers=63,
                sequence_words=17,sequence_execution_implemented=False,
                timer_execution_implemented=False,solver_implemented=False,
                power_collapse_implemented=False,bandwidth_ack_implemented=False,
                physical_reset_values_measured=False,migration_payloads_modified=False,
                qtest_memtx_result_observed=False,
                rejection_evidence='exact model diagnostic and unchanged state; '
                                   'the source-bound rejection path returns MEMTX_ERROR')

    def check(name,**evidence):
        assert name==REQUIRED[len(report['checks'])]
        report['checks'].append(dict(name=name,passed=True,**evidence))

    try:
        args.machine=MACHINE
        off=RSCRig(args,out/'default-off')
        try:
            assert off.qget('/machine','sde-rsc-init-reference') is False
            assert 'error' in off.raw_monitor('qom-list',{'path':'/machine/sde-rsc'})
            rows,mp=flatmap(off,out/'default-off-memory-map.txt')
            assert not any(name.startswith(TYPE) for a,b,name in rows)
            absent=[]
            for base,offset in ((WRAPPER,0x1fe0),(DRV,0x600)):
                prior=off.stderr();off.read_rsc(base,offset);added=off.stderr()[len(prior):]
                expected=(f"Invalid read at addr 0x{base+offset:X}, size 4, region '(null)', reason: rejected"
                          if base==WRAPPER else
                          f'qcom-sm8250-rpmh: DRV3 unsupported read @{offset:#06x} value=0x00000000')
                assert added.strip()==expected,added
                absent.append(dict(address=hex(base+offset),diagnostic=added.strip(),
                                   owner='unassigned' if base==WRAPPER else 'rpmh-display-voter'))
            reply=off.raw_monitor('qom-set',{'path':'/machine','property':'sde-rsc-init-reference','value':True})
            assert 'error' in reply and off.qget('/machine','sde-rsc-init-reference') is False
            check(REQUIRED[0],machine=args.machine,flatmap=mp,rejected_reads=absent,
                  immutable_after_realize=reply)
        finally:off.close()

        args.machine='tb371fc,ufs-enabled=off,sde-rsc-init-reference=on'
        invalid=RSCRig(args,out/'requires-rpmh',expect_realize_failure=True)
        try:
            assert 'sde-rsc-init-reference requires RPMh enabled' in invalid.stderr()
            check(REQUIRED[1],machine=args.machine,realize_exit=invalid.process.returncode)
        finally:invalid.close()

        args.machine=ON_MACHINE
        source=RSCRig(args,out/'source')
        try:
            assert source.qget('/machine','sde-rsc-init-reference') is True
            assert source.qget('/machine/sde-rsc','profile-id')==PROFILE
            immutable=[]
            for path,prop,value in [('/machine','sde-rsc-init-reference',False),('/machine/sde-rsc','profile-id',0)]:
                r=source.raw_monitor('qom-set',{'path':path,'property':prop,'value':value})
                assert 'error' in r;immutable.append(r)
            assert source.qget('/machine','sde-rsc-init-reference') is True
            assert source.qget('/machine/sde-rsc','profile-id')==PROFILE
            rows,mp=flatmap(source,out/'memory-map.txt')
            expected={(WRAPPER,WRAPPER+0x3fd3)}|{(DRV+o,DRV+o+n-1) for o,n in DRV_WINDOWS}
            actual={(a,b) for a,b,n in rows if n.startswith(TYPE)}
            assert actual==expected,(actual,expected)
            for offset in PROTECTED:
                # A sliced FlatRange names its original MemoryRegion plus the
                # exact region-relative offset. Preserve both owner and offset.
                assert any(a<=DRV+offset<=b and n==('qcom-sm8250-rpmh.voter3' +
                    (f' @{a-DRV:016x}' if a!=DRV else '')) for a,b,n in rows)
            reset=source.state()
            assert reset['config']==[0]*80 and reset['apps']==[0x20700]*3
            assert source.read_rsc(DRV,0)==0x20700 and source.read_rsc(DRV,4)==0
            check(REQUIRED[2],machine=args.machine,flatmap=mp,
                  regions=[[hex(a),hex(b)] for a,b in sorted(actual)],
                  protected_rpmh_offsets=[hex(x) for x in PROTECTED],reset=reset,
                  unsupported_rpmh_offsets=['0x1ea0'],
                  immutable_qom_errors=immutable)

            writes=run_initializer(source)
            check(REQUIRED[3],id0=0x20700,first_write=writes[0],writes=writes,
                  write_count=len(writes),driver_executed=False,sequence_executed=False)

            timer_rejects=[]
            for lo,hi,ctl in ((0x2020,0x2024,0x202c),(0x3020,0x3024,0x302c)):
                timer_rejects.append(rejects(source,WRAPPER,lo,value=0))
                timer_rejects.append(rejects(source,WRAPPER,hi,value=0))
                timer_rejects.append(rejects(source,WRAPPER,ctl,value=3))
                source.write_rsc(WRAPPER,ctl,0)
                source.write_rsc(WRAPPER,lo,0x12345678)
                source.write_rsc(WRAPPER,hi,0x89abcdef)
                assert source.read_rsc(WRAPPER,lo)==0x12345678
                assert source.read_rsc(WRAPPER,hi)==0x89abcdef
                timer_rejects.append(rejects(source,WRAPPER,ctl,value=1))
                source.write_rsc(WRAPPER,lo,0xffffffff)
                source.write_rsc(WRAPPER,hi,0xffffffff)
                source.write_rsc(WRAPPER,ctl,1)
            report['access_rejections']+=timer_rejects
            check(REQUIRED[4],rejections=timer_rejects,disabled_storage=True,
                  finite_armed_compare_supported=False)

            active=[]
            for base,offset,value in [
                (WRAPPER,0,1),(WRAPPER,0,9),(WRAPPER,4,3),(WRAPPER,4,0x201),
                (WRAPPER,0x20,1),(WRAPPER,0x24,0x1000),(WRAPPER,0x40,0xe),
                (WRAPPER,0x48,1),(WRAPPER,0x50,0x4000),(DRV,0xc14,1),
                (DRV,0x404,1),(DRV,0x408,4),(DRV,0xc24,1),(DRV,0xc28,1),(DRV,0xc2c,2)]:
                active.append(rejects(source,base,offset,value=value))
            report['access_rejections']+=active
            check(REQUIRED[5],rejections=active,completion_or_ack_generated=False)

            malformed=[]
            for base,offset in ((WRAPPER,8),(DRV,0x600)):
                for width in ('b','w','q'):
                    for write in (False,True):
                        malformed.append(rejects(source,base,offset,write=write,width=width,value=0x12345678))
                for write in (False,True):
                    malformed.append(rejects(source,base,offset+1,write=write))
            for base,offset in ((WRAPPER,0x14),(WRAPPER,0x2028),(DRV,0x40c),(DRV,0xc10),(DRV,0xc3c)):
                for write in (False,True):malformed.append(rejects(source,base,offset,write=write))
            for base,offset,value in [(DRV,0x410,0x44),(DRV,0xc30,1),(DRV,0xc18,3),
                (DRV,0xc20,8),(DRV,0xc34,1),(DRV,0x20,1),(DRV,0x2c,2),
                (DRV,0x1100,3),(WRAPPER,0x1040,2),(WRAPPER,0x1fe0,1)]:
                malformed.append(rejects(source,base,offset,value=value))
            # This real AddressSpace request starts at the final four bytes.
            # Reject the whole wide transaction before any register mutation.
            malformed.append(rejects(source,WRAPPER,0x3fd0,width='q',value=0x123456789abcdef0))
            report['access_rejections']+=malformed
            neighbors=[]
            for address in (WRAPPER+0x3fd4,DRV+0x644):
                saved=source.state();prior=source.stderr();source.qt(f'readl {address:#x}')
                added=source.stderr()[len(prior):]
                expected=(f"Invalid read at addr 0x{address:X}, size 4, region '(null)', reason: rejected"
                          if address==WRAPPER+0x3fd4 else
                          'qcom-sm8250-rpmh: DRV3 unsupported read @0x0644 value=0x00000000')
                assert added.strip()==expected,added
                assert source.state()==saved
                neighbors.append(dict(address=hex(address),diagnostic=added.strip(),
                                      owner='unassigned' if address==WRAPPER+0x3fd4 else 'rpmh-display-voter'))
            check(REQUIRED[6],rejections=malformed,adjacent_reads=neighbors,
                  actual_address_space=True,whole_qword_rejected=True)

            # -S disables virtual timer callbacks. QTest uses dummy vCPUs:
            # cont enables device timers without executing guest instructions.
            timer_before=source.monitor('query-status')
            assert timer_before['running'] is False and timer_before['status'] in ('prelaunch','paused')
            source.monitor('cont')
            timer_running=source.monitor('query-status')
            assert timer_running['running'] is True and timer_running['status']=='running'
            r,w=source.read_rsc,source.write_rsc
            assert r(DRV,0xc)==0x80000002 and r(DRV,4)==0
            address=source.qget('/machine/rpmh','res.cx.lvl.address')
            assert type(address) is int and address>0
            assert source.qget('/machine/rpmh','res.cx.lvl.value')==0
            w(DRV,0x38,0x123456);w(DRV,0x40,0x89abcdef)
            assert r(DRV,0x38)==0x123456 and r(DRV,0x40)==0x89abcdef
            w(DRV,0x1c00,1);w(DRV,0x1c10,1);w(DRV,0x1c1c,1)
            w(DRV,0x1c30,0x10108);w(DRV,0x1c34,address);w(DRV,0x1c38,1)
            w(DRV,0x1c14,0x1010000)
            assert r(DRV,0x1c18)==0 and r(DRV,0x1c3c)==0x100
            w(WRAPPER,8,0x11223344)
            source.step(999)
            assert r(DRV,0x1c18)==0 and r(DRV,0x1c04)==0
            source.step(1)
            assert r(DRV,0x1c18)==1 and r(DRV,0x1c3c)==0x10100 and r(DRV,0x1c04)==1
            assert source.qget('/machine/rpmh','res.cx.lvl.value')==1
            assert r(WRAPPER,8)==0x11223344
            w(DRV,0x1c08,1);assert r(DRV,0x1c04)==0
            w(DRV,0x1c14,0)
            prior=source.stderr();r(DRV,0x1ea0);unsupported=source.stderr()[len(prior):]
            assert unsupported.strip()=='qcom-sm8250-rpmh: DRV3 unsupported read @0x1ea0 value=0x00000000',unsupported
            check(REQUIRED[7],resource_address=hex(address),command=0x10108,
                  complete_after_ns=1000,status=0x10100,irq_status_asserted_then_cleared=True,
                  sde_configuration_while_tcs_active=True,
                  qtest_timer_runstate=dict(before=timer_before,running=timer_running),
                  preserved_unsupported_read=dict(address=hex(DRV+0x1ea0),diagnostic=unsupported.strip()),
                  apps_ids=[r(0x18200000+i*0x10000,0) for i in range(3)])

            saved=source.state();source.step(1000000000)
            assert source.state()==saved
            status=[r(WRAPPER,o) for o in (0x10,0x18,0x1c,0x24,0x50)]
            status += [r(DRV,o) for o in (0x404,0x408,0xc24,0xc28,0xc2c)]
            assert status==[0]*10
            source.monitor('stop')
            timer_stopped=source.monitor('query-status')
            assert timer_stopped['running'] is False and timer_stopped['status']=='paused'
            check(REQUIRED[8],elapsed_virtual_ns=1000000000,zero_status=status,
                  configuration_unchanged=True,irq_line_emulation_claimed=False,
                  qtest_timer_stopped=timer_stopped)

            source.monitor('system_reset')
            assert source.state()==reset
            check(REQUIRED[9],state=reset,sequence_words_cleared=17)

            run_initializer(source)
            w(WRAPPER,0x202c,0);w(WRAPPER,0x2020,0x13579bdf);w(WRAPPER,0x2024,0x2468ace0)
            w(DRV,0x600,0xa5a5a5a5);w(DRV,0x640,0x5a5a5a5a)
            w(DRV,0x1004,0x10203040);w(WRAPPER,8,0x50607080)
            clock_ns=source.clock_ns
            saved=source.state();stream=out/'streams/sde-rsc.mig'
            source.monitor('migrate',file_channels(stream));assert source.migrate_wait()['status']=='completed'
            migration=dict(path=str(stream),bytes=stream.stat().st_size,sha256=sha(stream),
                           payload_modified=False,qtest_clock_ns=clock_ns)
            report['migrations'].append(migration);expected=continuation(source)
        finally:source.close()

        target=RSCRig(args,out/'target',incoming=True)
        try:
            # The synthetic QTest clock is process-local, not stream payload.
            # Align the fresh target before ordinary VMState loading.
            assert target.set_clock(0)==0
            assert target.set_clock(migration['qtest_clock_ns'])==clock_ns
            target.monitor('migrate-incoming',file_channels(stream));assert target.migrate_wait()['status']=='completed'
            assert target.state()==saved
            assert continuation(target)==expected
            assert sha(stream)==migration['sha256']
            check(REQUIRED[10],saved_state=saved,continued_state=expected,
                  ordinary_migration=True,malformed_migration_tested=False,
                  qtest_clocks_aligned=True)
        finally:target.close()

        report['processes']=verify_cleanup(out)
        assert len(report['processes'])==4
        assert {x['fixture']:x['counters']['qom_set'] for x in report['processes']}=={'default-off':1,'requires-rpmh':0,'source':2,'target':0}
        assert {x['fixture']:x['counters']['cont'] for x in report['processes']}=={'default-off':0,'requires-rpmh':0,'source':1,'target':0}
        assert sha(args.qemu)==report['qemu_sha256'] and sha(__file__)==report['test_sha256']
        check(REQUIRED[11],processes=4,forced_cleanup=False,
              qom_set_only_three_rejected_immutable_property_checks=True,
              qtest_source_cont_stop_only=True)
        assert [x['name'] for x in report['checks']]==REQUIRED
        report['passed']=True
    except BaseException as error:
        report['error']=f'{type(error).__name__}: {error}'
        report['traceback']=traceback.format_exc()
    finally:put_json(out/'result.json',report)
    if not report['passed']:print(report.get('traceback',report.get('error','Unknown failure')))
    print(json.dumps(dict(passed=report['passed'],checks=len(report['checks']),
                          error=report.get('error'),report=str(out/'result.json'))))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
