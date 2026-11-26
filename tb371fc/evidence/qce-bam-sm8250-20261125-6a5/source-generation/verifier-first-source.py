#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independently check both generated artifacts and fail-closed source parsing.

No network, QEMU or device operation is performed. Whole-file integrity tests
use the production entry point. Parser tests explicitly identify newly altered
fixtures and call only the pure parser; they never replace production pins or
emit a bound include. All test files and first failures are retained under --out.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import traceback

SCRIPT = Path(__file__).with_name('gen_qce_bam_sm8250.py')
spec = importlib.util.spec_from_file_location('qce_bam_source_generator', SCRIPT)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

# Independent literals from the reviewed source/policy contract. Expected words
# are not read from the generated include, C model, or generator return value.
EXPECTED_WORDS = {
    'qce-version': 0x05030001, 'qce-engines': 0,
    'bam-config': 0x07fff004, 'bam-revision': 0x00000203,
    'bam-num-pipes': 0x00004008, 'bam-ee-attr': 0x800000f0,
    'bam-ee-initial-irq-mask': 0,
}
EXPECTED_DIGEST = 'f9809a8de0e6b91c57e7baf3d675dfd16c4ce1bc6e2797a0d866ec42bebf1c3b'
EXPECTED_PROFILE_FILE_SHA256 = '6bb5bff5a445a73fab54261376ef73a820c6b2ac050048e5fca45715755f76be'
EXPECTED_FIELDS = {
    'QCE_VERSION': 'qce-version', 'QCE_ENGINES': 'qce-engines',
    'BAM_CONFIG': 'bam-config', 'BAM_REVISION': 'bam-revision',
    'BAM_NUM_PIPES': 'bam-num-pipes', 'BAM_EE_ATTR': 'bam-ee-attr',
    'BAM_EE_MASK': 'bam-ee-initial-irq-mask',
}


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def identities(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'git_blob': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}


def save(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def save_json(path, document):
    save(path, (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + '\n').encode())


def expect_rejection(name, category, operation, records, **details):
    try:
        operation()
    except (g.GenerationError, ValueError, TypeError, KeyError, OSError, UnicodeError) as exc:
        records.append({'name': name, 'category': category, 'rejected': True,
                        'rejection_reason': type(exc).__name__ + ': ' + str(exc), **details})
        return
    raise AssertionError('Invalid case accepted: ' + name)


def verify_output_semantics(include, manifest, profile):
    text = include.decode('utf-8')
    check('\r' not in text and text.endswith('\n') and not include.startswith(b'\xef\xbb\xbf'), 'include encoding')
    defines = re.findall(r'^#define\s+(QCB_PROFILE_[A-Z_]+)\s+([^\n]+)$', text, re.M)
    check(len(defines) == 10 and len({name for name, _ in defines}) == 10, 'exact ten unique include macros')
    rows = dict(defines)
    check(rows.pop('QCB_PROFILE_BOUND') == 'true', 'include bound bit')
    check(rows.pop('QCB_PROFILE_NAME') == '"virtual-qce-bam-cold-control-v1"', 'include profile name')
    digest = rows.pop('QCB_PROFILE_DIGEST')
    check(re.fullmatch(r'\{ 0x[0-9a-f]{2}(?:, 0x[0-9a-f]{2}){31} \}', digest) is not None, 'digest C initializer shape')
    check(bytes(int(x, 16) for x in re.findall(r'0x([0-9a-f]{2})', digest)).hex() == EXPECTED_DIGEST, 'canonical digest bytes')
    check(set(rows) == {'QCB_PROFILE_' + suffix for suffix in EXPECTED_FIELDS}, 'seven include fields')
    for suffix, field in EXPECTED_FIELDS.items():
        match = re.fullmatch(r'UINT32_C\(0x([0-9a-f]{8})\)', rows['QCB_PROFILE_' + suffix])
        check(match is not None and int(match[1], 16) == EXPECTED_WORDS[field], 'independent expected word: ' + field)
    check(manifest['derived']['identity'] == EXPECTED_WORDS == profile['identity'], 'derived identities match independent policy words')
    facts = manifest['derived']
    check(facts['geometry']['owned_pipes'] == [4, 5, 6, 7], 'owned pipe topology')
    check((facts['geometry']['bam_base'], facts['geometry']['bam_bytes'], facts['geometry']['qce_base'], facts['geometry']['qce_bytes']) == (0x1dc4000, 0x24000, 0x1de0000, 0x20000), 'resource geometry')
    check((facts['geometry']['union_bytes'], facts['geometry']['overlap_bytes']) == (0x3c000, 0x8000), 'union overlap relationship')
    check((facts['geometry']['qce_version_address'], facts['geometry']['qce_engines_address']) == (0x1dfa000, 0x1dfa108), 'QCE identity addresses')
    check((facts['geometry']['channels'], facts['geometry']['execution_environments'], facts['geometry']['guest_ee']) == (8, 2, 0), 'upstream topology')
    expected_registers = {'REVISION': 0x1000, 'NUM_PIPES': 0x1008, 'CNFG_BITS': 0x7c,
        'IRQ_SRCS_EE': 0x3000, 'IRQ_SRCS_MSK_EE': 0x3004, 'IRQ_SRCS_UNMASKED_EE': 0x3008, 'PIPE_ATTR_EE': 0x300c,
        'P_CTRL': 0x13000, 'P_RST': 0x13004, 'P_IRQ_STTS': 0x13010, 'P_IRQ_CLR': 0x13014, 'P_IRQ_EN': 0x13018,
        'P_EVNT_REG': 0x13818, 'P_SW_OFSTS': 0x13800, 'P_DESC_FIFO_ADDR': 0x1381c,
        'P_EVNT_GEN_TRSHLD': 0x13828, 'P_FIFO_SIZES': 0x13820, 'P_DESC_FIFO_ADDR_MSB': 0x13914}
    check(facts['layout']['bam_register_offsets'] == expected_registers, 'independent 4K register geometry')
    check(facts['fields']['qce_engines_defined_mask'] == 0x1dffffff, 'complete engines bitfields')
    check(facts['fields']['bam_revision']['BAM_NUM_EES'] == 0xf00, 'EE field')
    check(facts['fields']['bam_num_pipes']['BAM_DATA_ADDR_BUS_WIDTH'] == 0xc000, 'width field')
    check(facts['fields']['pipe_control_cold_mask'] == 0x1f003a, 'cold control mask')
    check(facts['fields']['descriptor_upper_mask'] == 15 and facts['fields']['descriptor_address_bits'] == 36, 'descriptor high word bounds')
    check(facts['fields']['pipe_irq_masks'] == {'STTS': 255, 'CLR': 255, 'EN': 255}, 'complete IRQ masks')
    terms = facts['config']['terms']
    check(len(terms) == 17 and sorted(row['bit'] for row in terms) == [2] + list(range(12, 28)), 'all CNFG source terms')
    check([row['name'] for row in terms if not row['retained']] == ['BAM_CMD_ENABLE'], 'only CMD_ENABLE removed')
    check(facts['config']['source_default'] == 0x0ffff004 and facts['config']['removed_command_mask'] == 1 << 27, 'source default/removed bit')
    check(facts['consumer']['first_cd0_clears_no_get_around'] is True and facts['consumer']['cache_hit_clears_no_get_around'] is False, 'first/cached consumer distinction')
    check(facts['consumer']['software_flags_are_not_emulated_crypto_capabilities'] is True, 'software capability limitation')
    check(manifest['profile']['digest'] == EXPECTED_DIGEST and manifest['profile']['file_sha256'] == EXPECTED_PROFILE_FILE_SHA256, 'reviewed profile identities')
    check(manifest['input_count'] == 12 and manifest['input_bytes'] == 770745, 'twelve complete inputs')
    check(manifest['all_source_identities_verified_before_source_parse'] is True, 'verify-before-parse contract')


def profile_cases(document):
    cases = []
    def add(name, change):
        item = copy.deepcopy(document)
        change(item)
        cases.append((name, item))
    add('profile-unknown-top-key', lambda x: x.update(unreviewed_override=True))
    add('profile-missing-top-key', lambda x: x.pop('identity'))
    add('profile-integer-bound', lambda x: x.update(bound=1))
    add('profile-unbound', lambda x: x.update(bound=False))
    add('profile-name-injection', lambda x: x.update(name='bad"name'))
    add('profile-unreviewed-name', lambda x: x.update(name='different-policy'))
    add('profile-missing-identity-field', lambda x: x['identity'].pop('qce-version'))
    add('profile-extra-identity-field', lambda x: x['identity'].update(ready=1))
    add('profile-boolean-word', lambda x: x['identity'].update({'qce-version': True}))
    add('profile-negative-word', lambda x: x['identity'].update({'qce-version': -1}))
    add('profile-wide-word', lambda x: x['identity'].update({'qce-version': 1 << 32}))
    for field in g.FALSE_CAPABILITIES:
        add('profile-unsupported-' + field, lambda x, f=field: x['policy'].update({f: True}))
    for field in ('cold_reset_is_reference_policy', 'identity_selection_reviewed', 'initial_remote_state_reviewed'):
        add('profile-unreviewed-' + field, lambda x, f=field: x['policy'].update({f: False}))
    add('profile-default-enabled', lambda x: x['policy'].update(default_enabled=True))
    add('profile-empty-basis', lambda x: x['policy'].update(basis=''))
    add('profile-missing-input', lambda x: x['source_inputs'].pop())
    add('profile-duplicate-input', lambda x: x['source_inputs'].append(copy.deepcopy(x['source_inputs'][0])))
    add('profile-source-outside-root', lambda x: x['source_inputs'][0].update(path='../other.c'))
    for field, value in (('bytes', 1), ('sha256', '0' * 64), ('git_blob', '0' * 40), ('revision', '0' * 40), ('repository', 'unreviewed/repository')):
        add('profile-source-' + field, lambda x, f=field, v=value: x['source_inputs'][0].update({f: v}))
    for field in EXPECTED_WORDS:
        add('profile-one-bit-' + field, lambda x, f=field: x['identity'].update({f: x['identity'][f] ^ 1}))
    return cases


def source_integrity_cases(out, profile_path, payloads, records):
    for index, pin in enumerate(g.SOURCE_PINS):
        for kind in ('one-bit', 'truncated'):
            name = f'complete-input-{index:02d}-{kind}'
            root = out / 'integrity' / name
            root.mkdir(parents=True, exist_ok=False)
            changed = bytearray(payloads[pin['key']])
            if kind == 'one-bit': changed[len(changed) // 2] ^= 1
            else: changed = changed[:-1]
            for row in g.SOURCE_PINS:
                save(root / row['path'], bytes(changed) if row['key'] == pin['key'] else payloads[row['key']])
            calls = []
            original_parser = g.parse_sources
            def parser_spy(value):
                calls.append(True)
                return original_parser(value)
            g.parse_sources = parser_spy
            try:
                expect_rejection(name, 'complete-input-identity', lambda: g.generate(profile_path, root), records,
                                 input=pin['key'], altered=identities(bytes(changed)))
            finally:
                g.parse_sources = original_parser
            check(not calls, name + ': parser ran before all identities passed')
            records[-1]['source_parser_calls'] = len(calls)
    # A whole-file input must not silently accept an extra byte, a missing file,
    # or a symlink to an otherwise correctly pinned source.
    for kind in ('oversized', 'missing', 'symlink'):
        name, root = 'complete-input-' + kind, out / 'integrity' / kind
        root.mkdir(parents=True, exist_ok=False)
        for index, pin in enumerate(g.SOURCE_PINS):
            path = root / pin['path']
            if index == 0 and kind == 'missing': continue
            if index == 0 and kind == 'symlink':
                path.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(out / 'original-inputs' / pin['path'], path)
            else:
                save(path, payloads[pin['key']] + (b'x' if index == 0 and kind == 'oversized' else b''))
        expect_rejection(name, 'complete-input-container', lambda: g.generate(profile_path, root), records)


def change_once(text, old, new):
    check(text.count(old) == 1, 'fixture replacement must have exactly one source match: ' + old)
    return text.replace(old, new, 1)


def change_macro(text, name, expression, *, occurrence=0, remove=False, duplicate=False):
    found = list(re.finditer(r'^([ \t]*#define[ \t]+' + re.escape(name) + r'[ \t]+)([^\n]+)$', text, re.M))
    check(0 <= occurrence < len(found), 'fixture macro not found: ' + name)
    m = found[occurrence]
    replacement = '' if remove else m[1] + expression
    if duplicate: replacement = m.group() + '\n' + m.group()
    return text[:m.start()] + replacement + text[m.end():]


def change_4k(text, old, new):
    marker = '{ /* 4K OFFSETs*/'
    check(text.count(marker) == 1, 'fixture 4K anchor')
    head, tail = text.split(marker, 1)
    return head + marker + change_once(tail, old, new)


def parser_cases(texts):
    cases = []
    def add(name, key, text):
        check(text != texts[key], 'unchanged parser fixture: ' + name)
        cases.append((name, key, text.encode('utf-8')))
    bam, qce, dtsi = texts['vendor_bam'], texts['vendor_qce'], texts['linux_dt']
    add('parser-duplicate-NDP-macro', 'vendor_bam', change_macro(bam, 'BAM_REVISION', '', duplicate=True))
    add('parser-mask-only-in-legacy-scope', 'vendor_bam', change_macro(bam, 'BAM_REVISION', '', remove=True))
    add('parser-NDP-macro-cycle', 'vendor_bam', change_macro(bam, 'BAM_NUM_EES', 'BAM_NUM_EES'))
    add('parser-NDP-illegal-call', 'vendor_bam', change_macro(bam, 'BAM_NUM_EES', "__import__('os').system('false')"))
    add('parser-NDP-mask-mismatch', 'vendor_bam', change_macro(bam, 'BAM_NUM_EES', '0xe00'))
    add('parser-missing-NDP-branch', 'vendor_bam', change_once(bam, '#ifdef CONFIG_SPS_SUPPORT_NDP_BAM\n\n/* Maximum number of execution environment */', '#ifdef DIFFERENT_BAM\n\n/* Maximum number of execution environment */'))
    add('parser-missing-4K-register-with-other-layout-present', 'vendor_bam', change_4k(bam, '[REVISION] = 0x1000,', ''))
    add('parser-duplicate-4K-register', 'vendor_bam', change_4k(bam, '[REVISION] = 0x1000,', '[REVISION] = 0x1000,\n[REVISION] = 0x1000,'))
    add('parser-wrong-4K-register-not-legacy-fallback', 'vendor_bam', change_4k(bam, '[REVISION] = 0x1000,', '[REVISION] = 0x4,'))
    add('parser-missing-4K-only-register', 'vendor_bam', change_4k(bam, '[P_DESC_FIFO_ADDR_MSB] = 0x13914,', ''))
    add('parser-4K-illegal-expression', 'vendor_bam', change_4k(bam, '[P_CTRL] = 0x13000,', '[P_CTRL] = open("untrusted"),'))
    add('parser-layout-enum-reordered', 'vendor_spsi', change_once(texts['vendor_spsi'], 'SPS_BAM_LEGACY,\n\tSPS_BAM_NDP,\n\tSPS_BAM_NDP_4K', 'SPS_BAM_LEGACY,\n\tSPS_BAM_NDP_4K,\n\tSPS_BAM_NDP'))
    add('parser-missing-upstream-v17-table', 'linux_bam', change_once(texts['linux_bam'], 'bam_v1_7_reg_info[] = {', 'bam_other_reg_info[] = {'))
    add('parser-upstream-register-tuple-width', 'linux_bam', change_once(texts['linux_bam'], '[BAM_REVISION]\t\t= { 0x01000, 0x00, 0x00, 0x00 },', '[BAM_REVISION]\t\t= { 0x01000, 0x00, 0x00 },'))
    add('parser-source-ABI-value-mismatch', 'vendor_qce', change_macro(qce, 'CRYPTO_CORE_MINOR_VER_NUM', '0x04'))
    add('parser-source-ABI-missing', 'vendor_qce', change_macro(qce, 'CRYPTO_CORE_STEP_VER_NUM', '', remove=True))
    add('parser-source-ABI-duplicate', 'vendor_qce', change_macro(qce, 'CRYPTO_CORE_MAJOR_VER_NUM', '', duplicate=True))
    add('parser-version-mask-disagreement', 'linux_regs', change_macro(texts['linux_regs'], 'CORE_MAJOR_REV_MASK', 'GENMASK(30, 24)'))
    add('parser-version-mask-invalid-GENMASK', 'linux_regs', change_macro(texts['linux_regs'], 'CORE_MAJOR_REV_MASK', 'GENMASK(24, 31)'))
    add('parser-source-expression-float', 'vendor_regs', change_macro(texts['vendor_regs'], 'CRYPTO_CORE_MINOR_REV', '16.0'))
    add('parser-source-expression-large-shift', 'linux_bam', change_macro(texts['linux_bam'], 'BAM_CMD_ENABLE', 'BIT(64)'))
    add('parser-command-bit-mismatch', 'vendor_regs', change_macro(texts['vendor_regs'], 'CRYPTO_BAM_CD_ENABLE', '26'))
    add('parser-CNFG-not-source-term-expression', 'linux_bam', change_once(texts['linux_bam'], '#define BAM_CNFG_BITS_DEFAULT\t(BAM_PIPE_CNFG |\t\\', '#define BAM_CNFG_BITS_DEFAULT\t(0x4 |\t\\'))
    add('parser-CNFG-duplicate-term', 'linux_bam', change_once(texts['linux_bam'], '#define BAM_CNFG_BITS_DEFAULT\t(BAM_PIPE_CNFG |\t\\', '#define BAM_CNFG_BITS_DEFAULT\t(BAM_PIPE_CNFG | BAM_PIPE_CNFG |\t\\'))
    add('parser-MTI-boundary-duplicate', 'vendor_sps_bam', change_once(texts['vendor_sps_bam'], '#define BAM_VERSION_MTI_SUPPORT(ver) (ver <= 2)', '#define BAM_VERSION_MTI_SUPPORT(ver) (ver <= 2)\n#define BAM_VERSION_MTI_SUPPORT(ver) (ver <= 2)'))
    add('parser-high-address-only-in-other-scope', 'vendor_sps_api', change_once(texts['vendor_sps_api'], '#define SPS_GET_UPPER_ADDR(addr) ((addr & 0xF00000000ULL) >> 32)', '#define SPS_OTHER_UPPER_ADDR(addr) ((addr & 0xF00000000ULL) >> 32)'))
    add('parser-high-address-widened', 'vendor_sps_api', change_once(texts['vendor_sps_api'], '#define SPS_GET_UPPER_ADDR(addr) ((addr & 0xF00000000ULL) >> 32)', '#define SPS_GET_UPPER_ADDR(addr) ((addr & 0xFF00000000ULL) >> 32)'))
    add('parser-BAM-node-missing', 'linux_dt', change_once(dtsi, 'cryptobam: dma-controller@1dc4000 {', 'otherbam: dma-controller@1dc4000 {'))
    add('parser-BAM-node-duplicate', 'linux_dt', dtsi + '\ncryptobam: dma-controller@1dc4000 { };\n')
    marker = 'cryptobam: dma-controller@1dc4000 {'
    prefix, scope = dtsi.split(marker, 1)
    add('parser-channel-only-in-child-scope', 'linux_dt', prefix + marker + change_once(scope, 'num-channels = <8>;', 'unrelated-child { num-channels = <8>; };'))
    add('parser-channel-missing-but-other-nodes-present', 'linux_dt', prefix + marker + change_once(scope, 'num-channels = <8>;', ''))
    add('parser-channel-duplicate', 'linux_dt', prefix + marker + change_once(scope, 'num-channels = <8>;', 'num-channels = <8>;\nnum-channels = <8>;'))
    add('parser-channel-value-mismatch', 'linux_dt', prefix + marker + change_once(scope, 'num-channels = <8>;', 'num-channels = <9>;'))
    add('parser-DT-cell-expression-unsupported', 'linux_dt', prefix + marker + change_once(scope, 'num-channels = <8>;', 'num-channels = <(4 * 2)>;'))
    # Scope the boolean removal to the BAM node; later remotely managed devices
    # must not accidentally satisfy this required property.
    bam_end = scope.index('\n\t\t};')
    bam_part, remainder = scope[:bam_end], scope[bam_end:]
    add('parser-remote-property-missing-in-node', 'linux_dt', prefix + marker + change_once(bam_part, 'qcom,controlled-remotely;', '') + remainder)
    add('parser-vendor-client-pair-mismatch', 'vendor_dt', change_once(texts['vendor_dt'], 'qcom,bam-pipe-pair = <3>;', 'qcom,bam-pipe-pair = <2>;'))
    add('parser-vendor-SPS-wrong-layout', 'vendor_dt', change_once(texts['vendor_dt'], 'compatible = "qcom,msm-sps-4k";', 'compatible = "qcom,msm-sps";'))
    add('parser-binding-fallback-missing', 'linux_binding', change_once(texts['linux_binding'], '- const: qcom,bam-v1.7.0', '- const: qcom,bam-v1.4.0'))
    add('parser-cached-client-incorrectly-clears-flag', 'vendor_qce', change_once(qce, 'pce_dev->support_cmd_dscr = pbam->support_cmd_dscr;\n\t\tgoto ret;', 'pce_dev->support_cmd_dscr = pbam->support_cmd_dscr;\n\t\tpce_dev->no_get_around = false;\n\t\tgoto ret;'))
    add('parser-first-client-does-not-clear-flag', 'vendor_qce', change_once(qce, 'pce_dev->no_get_around = false;', 'pce_dev->no_get_around = true;'))
    add('parser-command-setup-unconditional', 'vendor_qce', change_once(qce, 'if (pce_dev->support_cmd_dscr)\n\t\t\tqce_setup_cmdlistptrs(pce_dev, i, &vaddr);', 'qce_setup_cmdlistptrs(pce_dev, i, &vaddr);'))
    add('parser-hardcoded-support-fact-changed', 'vendor_qce', change_once(qce, 'ce_support->sha_hmac = true;', 'ce_support->sha_hmac = false;'))
    return cases


def parser_fixture_tests(out, payloads, profile, records):
    texts = {key: data.decode() for key, data in payloads.items()}
    for name, key, altered in parser_cases(texts):
        path = out / 'parser-fixtures' / (name + '.txt')
        save(path, altered)
        fixture_pin = identities(altered)
        check(identities(path.read_bytes()) == fixture_pin, 'fixture independent identity verification')
        fixture = dict(payloads)
        fixture[key] = altered
        def evaluate():
            derived = g.parse_sources(fixture)
            g.compare_profile(derived, profile)
        expect_rejection(name, 'parser-fixture', evaluate, records,
                         fixture_source=key, fixture_identity=fixture_pin,
                         fixture_only_repin=True, production_pins_modified=False,
                         production_generation_invoked=False)
    # Positive fixtures show that the parser actually interprets expressions
    # and selected scopes instead of rejecting every changed byte by a hash.
    positives = []
    equivalent = change_macro(texts['vendor_bam'], 'BAM_NUM_EES', '(0xf << 8)')
    changed_legacy = change_macro(texts['vendor_bam'], 'BAM_REVISION', '0x7f', occurrence=1)
    original = g.parse_sources(payloads)
    for name, text in (('equivalent-safe-integer-expression', equivalent), ('unselected-legacy-mask-does-not-supply-NDP-value', changed_legacy)):
        altered = text.encode()
        save(out / 'parser-fixtures' / (name + '.txt'), altered)
        fixture = dict(payloads)
        fixture['vendor_bam'] = altered
        result = g.parse_sources(fixture)
        check(result == original, name + ': unexpected changed derivation')
        g.compare_profile(result, profile)
        positives.append({'name': name, 'passed': True, 'fixture_identity': identities(altered),
                          'fixture_only_repin': True, 'production_pins_modified': False})
    return positives


def expression_tests(records):
    cases = {
        'expression-arbitrary-call': '__import__("os")',
        'expression-attribute': 'object.value',
        'expression-comprehension': '[x for x in range(2)]',
        'expression-float': '1.0',
        'expression-boolean': 'True',
        'expression-negative': '-1',
        'expression-shift-64': '1 << 64',
        'expression-negative-shift': '1 << -1',
        'expression-reversed-mask': 'GENMASK(3, 7)',
        'expression-BIT-keyword': 'BIT(index=3)',
        'expression-BIT-arity': 'BIT(1, 2)',
        'expression-multiplication': '2 * 2',
    }
    for name, expression in cases.items():
        expect_rejection(name, 'integer-expression', lambda x=expression: g.integer_expression(x), records)
    check(g.integer_expression('BIT(27) | (0xf << 8) | GENMASK(3, 0)') == 0x08000f0f, 'safe independent expression oracle')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=g.PROFILE)
    parser.add_argument('--source-root', type=Path, default=g.BUNDLE)
    parser.add_argument('--generated', type=Path, default=g.ROOT / g.INCLUDE_PATH)
    parser.add_argument('--manifest', type=Path, default=g.ROOT / g.MANIFEST_PATH)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'tb371fc-qce-bam-source-generation-v1', 'passed': False,
              'checks': [], 'negative_checks': [], 'qemu_device_network_used': False,
              'physical_identity_measured': False, 'automatic_test_retries': 0}
    original_pins = json.dumps(g.SOURCE_PINS, sort_keys=True)
    code = 1
    try:
        expected, manifest = g.generate(args.profile, args.source_root)
        actual = g.read_regular(args.generated, len(expected))
        manifest_raw = g.read_regular(args.manifest, 128 * 1024)
        check(actual == expected, 'generated include differs byte-for-byte')
        check(manifest_raw == g.json_bytes(manifest), 'generated manifest differs byte-for-byte')
        profile = g.read_profile(args.profile)
        verify_output_semantics(actual, manifest, profile)
        report.update(manifest)
        report['schema'] = 'tb371fc-qce-bam-source-generation-v1'
        report['profile_bound'] = True
        report['reference_profile_sha256'] = EXPECTED_DIGEST
        report['reference_profile_file_sha256'] = EXPECTED_PROFILE_FILE_SHA256
        report['generated_bytes'] = len(actual)
        report['generated_sha256'] = hashlib.sha256(actual).hexdigest()
        report['outputs'] = [{'path': g.INCLUDE_PATH, **identities(actual)},
                             {'path': g.MANIFEST_PATH, **identities(manifest_raw)}]
        report['checks'].append({'name': 'complete-source-pins-and-two-exact-outputs', 'passed': True})
        payloads = g.read_sources(args.source_root)
        for pin in g.SOURCE_PINS:
            save(args.out / 'original-inputs' / pin['path'], payloads[pin['key']])
        records = report['negative_checks']
        for name, document in profile_cases(profile['document']):
            path = args.out / 'profile-fixtures' / (name + '.json')
            save_json(path, document)
            expect_rejection(name, 'profile-policy', lambda p=path: g.read_profile(p), records)
        for name, raw in (
                ('profile-duplicate-JSON-key', b'{"schema":1,"schema":2}'),
                ('profile-nonfinite-JSON', b'{"x":NaN}'),
                ('profile-truncated-JSON', args.profile.read_bytes()[:-3])):
            save(args.out / 'profile-fixtures' / (name + '.json'), raw)
            expect_rejection(name, 'profile-json', lambda b=raw: g.strict_json(b), records)
        source_integrity_cases(args.out, args.profile, payloads, records)
        report['parser_positive_checks'] = parser_fixture_tests(args.out, payloads, profile, records)
        expression_tests(records)
        # Exercise the seven-value comparison itself, separately from profile
        # digest validation and parsing. No output is emitted for these values.
        for field in EXPECTED_WORDS:
            altered = copy.deepcopy(manifest['derived'])
            altered['identity'][field] ^= 1
            expect_rejection('derived-mismatch-' + field, 'derived-profile-comparison',
                             lambda value=altered: g.compare_profile(value, profile), records)
        check(json.dumps(g.SOURCE_PINS, sort_keys=True) == original_pins, 'production pins changed during verifier')
        check(len({x['name'] for x in records}) == len(records), 'duplicate negative test name')
        report['negative_count'] = len(records)
        report['negative_category_counts'] = {category: sum(x['category'] == category for x in records)
                                              for category in sorted({x['category'] for x in records})}
        report['production_pins_modified_by_tests'] = False
        report['checks'].append({'name': 'identity-policy-and-scoped-parser-negatives-rejected', 'passed': True})
        report['passed'] = True
        code = 0
    except Exception as exc:
        report['failure'] = {'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()}
    save_json(args.out / 'result.json', report)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']),
                      'negative_count': report.get('negative_count'),
                      'result': str(args.out / 'result.json')}, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
