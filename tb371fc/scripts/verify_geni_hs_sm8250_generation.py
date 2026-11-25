#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify complete GENI HS source pins, regeneration and preserved negatives."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


generator = module('geni_hs_generation', 'gen_geni_hs_sm8250.py')
source_download = module('geni_hs_source_download', 'source_download.py')

# Mutations target independent contracts, not just whitespace in a generated
# file. Production entry points never bypass complete input identity checks.
# The semantic parser negatives deliberately bypass those checks only after a
# separate full-input mutation has proved each identity boundary.
PARSER_MUTATIONS = (
    ('current_gcc', r'"hw-request"', '"hw-request-removed"'),
    ('current_gcc_data', r'"gcc_qupv3_wrap0_s6_clk"', '"removed_wrap0_s6_clock"'),
    ('current_gcc_h', r'(#define GCC_CLOCKS 200)', r'\1\n#define GCC_CLOCKS 201'),
    ('current_uart', r'(GENI_FW_REV\s*=\s*)0x068', r'\g<1>0x06c'),
    ('current_uart_h', r'(#define QCOM_GENI_UART_FIFO_SIZE\s+)64', r'\g<1>65'),
    ('qdev_clock', r'(void qdev_connect_clock_in\([^{}]*\)\s*\{\s*)assert\(!dev->realized\);',
     r'\1(void)dev;'),
    ('qdev_core', r'qdev_prop_set_globals\(DEVICE\(obj\)\);', '(void)obj;'),
    ('upstream_header', r'(\bGENI_SE_UART,)', r'\1\n GENI_SE_UART,'),
    ('upstream_se', r'(#define SE_GENI_FW_REVISION\s+)0x1000', r'\g<1>0x1008'),
    ('upstream_serial', r'proto\s*!=\s*GENI_SE_UART', 'proto != GENI_SE_SPI'),
    ('vendor-serial', r'(msm_port->ver_info.s_fw_ver\s*=\s*)get_se_s_fw', r'\1get_se_m_fw'),
    ('vendor_gcc', r'(struct clk_branch gcc_qupv3_wrap0_s6_clk\s*=\s*\{\s*\.halt_reg\s*=\s*)0x1772c',
     r'\g<1>0x17730'),
    ('vendor_gcc_h', r'(#define GCC_QUPV3_WRAP0_S6_CLK[ \t]+100\b)',
     r'\1\n#define GCC_QUPV3_WRAP0_S6_CLK 101'),
    ('vendor_header', r'(#define FW_REV_PROTOCOL_MSK\s*\()GENMASK\(15, 8\)', r'\1GENMASK(14, 8)'),
    ('vendor_kona', r'(clock_gcc:\s*qcom,gcc@100000\s*\{[^{}]*?reg\s*=\s*<0x100000\s+)0x1f0000',
     r'\g<1>0x1e0000'),
    ('vendor_platform_se', r'(fw_ver_s\s*=\s*\(\(geni_read_reg\(base,\s*GENI_FW_S_REVISION_RO\)\s*&\s*)FW_REV_VERSION_MSK',
     r'\1FW_REV_PROTOCOL_MSK'),
    ('vendor_qupv3', r'(qupv3_se6_4uart:\s*qcom,qup_uart@998000\s*\{\s*compatible\s*=\s*)"qcom,msm-geni-serial-hs"',
     r'\1"qcom,msm-geni-console"'),
    ('vendor_se', r'(#define QUP_HW_VER_REG\s+)0x4\b', r'\g<1>0x8'),
    ('vmstate_core', r'if\s*\(!vmstate_subsection_load\(f, vmsd, opaque, errp\)\)',
     'if (!removed_subsection_load(f, vmsd, opaque, errp))'),
)

EXPECTED_CONSTANTS = {
    'GENI_HS_SE_BASE': 0x998000, 'GENI_HS_SE_WINDOW_BYTES': 0x4000,
    'GENI_HS_WRAPPER_VERSION_ADDRESS': 0x9c0004, 'GENI_HS_WRAPPER_WINDOW_BYTES': 4,
    'GENI_HS_MAIN_FW_REVISION': 0x201, 'GENI_HS_SECONDARY_FW_REVISION': 0x201,
    'GENI_HS_WRAPPER_VERSION': 0x20050000, 'GENI_HS_FIFO_BYTES': 64,
    'GENI_HS_PROFILE_ID': 0x47485331, 'GENI_HS_DMA_TX_IRQ_MASK': 0x400f,
    'GENI_HS_DMA_RX_IRQ_MASK': 0x77ff, 'GENI_HS_CFG_REVISION': 8,
}


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def rejected(out, report, category, name, raw, callback, required_reason=None):
    sequence = len(report['negative_checks']) + 1
    relative = f'negative/{category}/{sequence:03d}-{name}.input'
    target = out / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    item = {'category': category, 'name': name, 'mutant_path': relative,
            'mutant_bytes': len(raw), 'mutant_sha256': generator.digest(raw),
            'mutant_git_blob_sha1': generator.git_blob(raw), 'rejected': False}
    report['negative_checks'].append(item)
    try:
        callback()
    except ValueError as error:
        item['rejection_type'] = type(error).__name__
        item['rejection_reason'] = str(error)
        generator.need(required_reason is None or str(error).startswith(required_reason),
                       'mutation rejected at wrong boundary: ' + name)
        item['rejected'] = True
        return
    raise ValueError('mutation was accepted: ' + category + '/' + name)


def run_negatives(out, report, sources, raw_inputs, policy, facts, pins, expected):
    for pin in generator.SOURCE_PINS:
        original = raw_inputs[pin['id']]
        mutant = bytearray(original)
        index = len(mutant) // 2
        mutant[index] ^= 1
        raw = bytes(mutant)
        rejected(out, report, 'complete-input-identity', pin['id'], raw,
                 lambda p=pin, b=raw: generator.verify_source_bytes(p, b),
                 'complete public source identity mismatch:')
        report['negative_checks'][-1].update({'original_sha256': pin['sha256'], 'mutation_offset': index,
                                             'same_full_byte_length': len(raw) == len(original)})
    with tempfile.TemporaryDirectory(prefix='reference-negative-', dir=out) as directory:
        root = Path(directory)
        for key, relative, _, _ in generator.REFERENCE_INPUTS:
            for _, other, _, _ in generator.REFERENCE_INPUTS:
                p = root / other
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes((ROOT / other).read_bytes())
            p = root / relative
            original = p.read_bytes()
            mutant = bytearray(original)
            index = len(mutant) // 2
            mutant[index] ^= 1
            p.write_bytes(bytes(mutant))
            rejected(out, report, 'reference-input-identity', key, bytes(mutant),
                     lambda: generator.load_reference_inputs(root), 'reference input identity mismatch:')
            report['negative_checks'][-1].update({'original_sha256': generator.digest(original),
                                                 'mutation_offset': index, 'same_full_byte_length': True})
    generator.need({case[0] for case in PARSER_MUTATIONS} == set(sources) and len(PARSER_MUTATIONS) == 19,
                   'parser negative coverage must include all source roles')
    for source_id, pattern, replacement in PARSER_MUTATIONS:
        changed, count = re.subn(pattern, replacement, sources[source_id])
        generator.need(count == 1 and changed != sources[source_id], 'parser mutation target count: ' + source_id)
        mutated = dict(sources)
        mutated[source_id] = changed
        rejected(out, report, 'parser-contract', source_id, changed.encode(),
                 lambda s=mutated: generator.derive(s, policy, facts, pins))
        report['negative_checks'][-1].update({'complete_identity_bypassed_only_for_semantic_test': True,
                                             'original_sha256': generator.digest(sources[source_id].encode())})
    semantic = (
        ('default-enabled', 'policy', ('default_enabled',), True),
        ('claimed-se6-selection', 'facts', ('selection', 'se6_selection_established'), True),
        ('claimed-live-wrapper', 'policy', ('physical_wrapper_version_known',), True),
        ('claimed-revision-alias-readback', 'facts', ('producer', 'write_to_readonly_alias_proved'), True),
        ('wrong-firmware-low-byte', 'facts', ('header_scalars', 'firmware_version'), 0x302),
        ('wrong-fifo-policy', 'policy', ('fifo_words',), 32),
        ('claimed-dma-completion', 'policy', ('dma_status_or_completion_implemented',), True),
        ('wrong-protocol-scalar', 'facts', ('header_scalars', 'protocol'), 3),
        ('wrong-static-selected-base', 'facts', ('selection', 'observed_static_configuration_bases'), [0x998000, 0xa90000]),
    )
    for name, owner, path, value in semantic:
        p, f = copy.deepcopy(policy), copy.deepcopy(facts)
        target = p if owner == 'policy' else f
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        rejected(out, report, 'reference-scope', name, json_bytes({'policy': p, 'producer_facts': f}),
                 lambda p=p, f=f: generator.derive(sources, p, f, pins))
    changed = dict(expected)
    inc = generator.OUTPUTS[0]
    changed[inc], count = re.subn(rb'(GENI_HS_MAIN_FW_REVISION UINT32_C\()0x00000201', rb'\g<1>0x00000301', expected[inc])
    generator.need(count == 1, 'generated revision mutation target')
    rejected(out, report, 'generated-output', 'wrong-main-revision', changed[inc],
             lambda: generator.compare_outputs(expected, changed), 'generated output mismatch:')
    changed = dict(expected)
    manifest_path = generator.OUTPUTS[1]
    manifest = json.loads(expected[manifest_path])
    manifest['scope']['physical_se6_firmware_state_known'] = True
    changed[manifest_path] = json_bytes(manifest)
    rejected(out, report, 'generated-output', 'false-physical-state-claim', changed[manifest_path],
             lambda: generator.compare_outputs(expected, changed), 'generated output mismatch:')


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'tb371fc-geni-hs-sm8250-source-generation-v1', 'passed': False,
              'public_source_pins_verified': False, 'reference_input_pins_verified': False,
              'inputs': [], 'checks': [], 'negative_checks': [],
              'private_binary_review_publicly_reproduced': False,
              'devices_ssh_qemu_or_native_used': False}
    try:
        source_root = out / 'inputs'
        source_root.mkdir()
        raw_inputs = {}
        for pin in generator.SOURCE_PINS:
            raw = (source_download.read_source(pin['url'], max_bytes=generator.MAX_INPUT_BYTES)
                   if args.source_root is None else (args.source_root / pin['local_name']).read_bytes())
            generator.verify_source_bytes(pin, raw)
            (source_root / pin['local_name']).write_bytes(raw)
            raw_inputs[pin['id']] = raw
            report['inputs'].append({**pin, 'transport': 'shared-source-download' if args.source_root is None else 'local',
                                     'verified_bytes': len(raw), 'verified_sha256': generator.digest(raw),
                                     'verified_git_blob_sha1': generator.git_blob(raw)})
        report['public_source_pins_verified'] = True
        sources = generator.load_sources(source_root)
        policy, facts, reference_pins = generator.load_reference_inputs()
        report['reference_input_pins_verified'] = True
        report['reference_inputs'] = reference_pins
        manifest = generator.derive(sources, policy, facts, reference_pins)
        generator.need(manifest['constants'] == EXPECTED_CONSTANTS, 'independent reviewed constants differ')
        generated = out / 'generated'
        command = [sys.executable, '-I', '-S', '-B', str(Path(generator.__file__).resolve()),
                   '--source-root', str(source_root), '--output-root', str(generated)]
        process = subprocess.run(command, capture_output=True, timeout=60)
        (out / 'generation.stdout').write_bytes(process.stdout)
        (out / 'generation.stderr').write_bytes(process.stderr)
        report['generator_exit_code'] = process.returncode
        report['generator_stdout_sha256'] = generator.digest(process.stdout)
        report['generator_stderr_bytes'] = len(process.stderr)
        generator.need(process.returncode == 0, 'subprocess regeneration failed')
        expected = generator.render_outputs(manifest)
        regenerated = {path: (generated / path).read_bytes() for path in generator.OUTPUTS}
        committed = {path: (ROOT / path).read_bytes() for path in generator.OUTPUTS}
        generator.compare_outputs(expected, regenerated)
        generator.compare_outputs(expected, committed)
        for path in generator.OUTPUTS:
            report['checks'].append({'name': path, 'passed': True, 'bytes': len(expected[path]),
                                     'sha256': generator.digest(expected[path]),
                                     'git_blob_sha1': generator.git_blob(expected[path])})
        run_negatives(out, report, sources, raw_inputs, policy, facts, reference_pins, expected)
        counts = {name: sum(item['category'] == name for item in report['negative_checks']) for name in (
            'complete-input-identity', 'reference-input-identity', 'parser-contract', 'reference-scope', 'generated-output')}
        generator.need(counts == {'complete-input-identity': 19, 'reference-input-identity': 2,
                                  'parser-contract': 19, 'reference-scope': 9, 'generated-output': 2},
                       'negative gate cardinality differs')
        report['negative_counts'] = counts
        report['generated_contract'] = {'source_input_count': 19, 'source_input_bytes': 810000,
                                       'reference_input_count': 2, 'output_count': 2, 'constant_count': 12,
                                       'window_count': 2, 'clock_binding_count': 3, 'fifo_bytes': 64,
                                       'negative_check_count': 51, 'constants': manifest['constants']}
        generator.need(all(item['rejected'] is True for item in report['negative_checks']), 'negative accepted')
        report['passed'] = True
    except Exception as error:
        report['error'] = {'type': type(error).__name__, 'message': str(error)}
    (out / 'result.json').write_bytes(json_bytes(report))
    print(json.dumps({'passed': report['passed'], 'inputs': len(report['inputs']),
                      'outputs': len(report['checks']), 'negatives': len(report['negative_checks'])}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--source-root', type=Path,
                        help='Optional local cache; complete length/SHA256/Git blob validation remains mandatory')
    raise SystemExit(run(parser.parse_args()))
