#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Derive the reviewed virtual QCE/BAM cold-control profile from full sources.

This generator has no network, device or QEMU interface. All twelve immutable
source identities are checked before any source text is decoded or parsed.
The seven words are derived from the selected source ABI plus explicitly named
virtual policy choices, then compared with the separately reviewed profile.
No command-line identity, source-pin override, fallback or physical reset claim
exists. Pure parser functions are exposed so the independent verifier can test
malformed, separately identified fixtures without changing production pins.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / 'tb371fc/sources/qce-bam-sm8250'
PROFILE = ROOT / 'tb371fc/docs/qce-bam-sm8250-reference-profile.json'
INCLUDE_PATH = 'hw/misc/qcom-sm8250-qce-bam-profile.inc'
MANIFEST_PATH = 'tb371fc/docs/qce-bam-sm8250-source-manifest.json'
PROFILE_DIGEST = 'f9809a8de0e6b91c57e7baf3d675dfd16c4ce1bc6e2797a0d866ec42bebf1c3b'
PROFILE_FILE_SHA256 = '6bb5bff5a445a73fab54261376ef73a820c6b2ac050048e5fca45715755f76be'
PROFILE_FILE_BYTES = 16599
VENDOR = 'OnePlusOSS/android_kernel_oneplus_sm8250'
VENDOR_REV = '98719830b0de5584f5588bbe9f4c8304d97a64c6'
UPSTREAM = 'torvalds/linux'
UPSTREAM_REV = 'adc218676eef25575469234709c2d87185ca223a'

# These are production identities, not values loaded from a mutable profile.
_PINS = (
    ('vendor_qce', VENDOR, VENDOR_REV, 'drivers/crypto/msm/qce50.c', 188196, 'a987df8a5ae02ffa34910cc96eb540a98111e76d6980fefbcd92277ae424c094', '1d9486ac0bd8c81b416a115eda5cd8e26164e0ec'),
    ('vendor_regs', VENDOR, VENDOR_REV, 'drivers/crypto/msm/qcryptohw_50.h', 18355, '300ba56b9c3c2c4f3adde3ef7b2bf8b2ec03e49eb41c9834a75bc882b6cf3732', 'ae72416e95e6502b63a4bf5533d71e1305e2237a'),
    ('vendor_bam', VENDOR, VENDOR_REV, 'drivers/platform/msm/sps/bam.c', 69776, '369b5b93a2d9d7c9244dc3f03d64db1d78450b977311e1eac619716b464ae859', 'f8f67ba4b789f0d1c204ea0f58c45b6a608df2bf'),
    ('vendor_spsi', VENDOR, VENDOR_REV, 'drivers/platform/msm/sps/spsi.h', 13415, '2517406309039ec1012774046810ca8a3fe493482ae798be48290c2678cce51d', '45ad64bd75553ee783e2902107585ce2902cefbf'),
    ('vendor_sps_bam', VENDOR, VENDOR_REV, 'drivers/platform/msm/sps/sps_bam.c', 65781, 'ea8099573db0bdbf48dfc9d3c31f70045a978343e8bc9da72f3fcd7f625a0ac6', 'c0c9ae60eea2df6714888d69b70cb26daa7532ff'),
    ('vendor_sps_api', VENDOR, VENDOR_REV, 'include/linux/msm-sps.h', 46545, '1b2300dca5426113b53446d5d581f20d83ac7fc1fe24957f4e335af91aea17f8', '98ed0e54535b9e18c3be68ef039ba9a64ebcf831'),
    ('vendor_dt', VENDOR, VENDOR_REV, 'arch/arm64/boot/dts/vendor/qcom/kona.dtsi', 132499, '12b4b191b0f517b0c9fdd42c23ad428193ef71e63831bade996f9a18b88ece55', 'e9651f09bd579616787894dc8514429063623b2d'),
    ('linux_bam', UPSTREAM, UPSTREAM_REV, 'drivers/dma/qcom/bam_dma.c', 40461, 'f58acbed38d782228fb9888a539f07caf849e6f3d60e06fb0621b940408064cd', 'd43a881e43b904a8e98faf582221dd05cede98e6'),
    ('linux_binding', UPSTREAM, UPSTREAM_REV, 'Documentation/devicetree/bindings/dma/qcom,bam-dma.yaml', 2550, '4a9b5810fdb7cb3ed2dbe3c19d7d9a47f047005185ac742ae7abfe9b18573d60', '3ad0d9b1fbc5e4f83dd316d1ad79773c288748ba'),
    ('linux_qce', UPSTREAM, UPSTREAM_REV, 'drivers/crypto/qce/core.c', 6952, '26781cdcae9c3d99b291428309455bb759ca8cb9de6ef3bedc29133a4d068001', '28b5fd82382775dd5cfffbda301058cfbd538ddd'),
    ('linux_regs', UPSTREAM, UPSTREAM_REV, 'drivers/crypto/qce/regs-v5.h', 9332, '79da04964ec251aff3874ac32bd8427cd40cb708e399c7c64381d52dc9e14676', 'd59ed279890621a8e2e6f4cdb20692dbf39f1461'),
    ('linux_dt', UPSTREAM, UPSTREAM_REV, 'arch/arm64/boot/dts/qcom/sm8250.dtsi', 176883, '8735b5d8bb61d6a3a579e5b088ff2a719132266cef57c91c319ec19e12770499', '630f4eff20bf810e347685066dce74ab92a23b56'),
)
SOURCE_PINS = tuple(dict(key=key, repository=repo, revision=rev,
                         source_path=path, bytes=size, sha256=sha, git_blob=blob,
                         path=('primary-vendor/' if repo == VENDOR else 'primary-upstream/') + path)
                    for key, repo, rev, path, size, sha, blob in _PINS)
MACROS = (('qce-version', 'QCE_VERSION'), ('qce-engines', 'QCE_ENGINES'),
          ('bam-config', 'BAM_CONFIG'), ('bam-revision', 'BAM_REVISION'),
          ('bam-num-pipes', 'BAM_NUM_PIPES'), ('bam-ee-attr', 'BAM_EE_ATTR'),
          ('bam-ee-initial-irq-mask', 'BAM_EE_MASK'))
FALSE_CAPABILITIES = ('physical_identity_measured', 'dma_execution_modeled',
                      'crypto_execution_modeled', 'secure_firmware_executed',
                      'guest_interrupt_handling_verified')
VIRTUAL_POLICY = {
    'abi_name': 'virtual-qce-bam-cold-control-v1',
    'select_named_vendor_qce_abi': True,
    'crypto_datapath_accepted': False,
    'command_descriptors_accepted': False,
    'bam_revision_marker_rule': 'source MTI upper bound + 1; new virtual marker, not silicon encoding',
    'descriptor_width_code': 1,
    'controller_admitted_when_enabled': True,
    'ee_initial_irq_mask': 0,
    'default_enabled': False,
    'physical_register_values_claimed': False,
}


class GenerationError(ValueError):
    """A source, scope, policy or output did not meet the frozen contract."""


def require(condition, message):
    if not condition:
        raise GenerationError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()


def canonical_json(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


def json_bytes(document):
    return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode('utf-8')


def strict_json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate JSON key: ' + key)
            result[key] = value
        return result
    def invalid(value):
        raise GenerationError('non-finite JSON constant: ' + value)
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)


def read_regular(path, limit):
    path = Path(path)
    require(not path.is_symlink(), 'symlink input: ' + str(path))
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode), 'non-regular input: ' + str(path))
        require(st.st_size <= limit, 'oversized input: ' + str(path))
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        require(len(raw) <= limit, 'oversized response: ' + str(path))
        return raw
    finally:
        os.close(fd)


def validate_profile_document(document):
    require(type(document) is dict and set(document) == {'schema', 'bound', 'name', 'identity', 'policy', 'source_inputs'}, 'profile top-level schema')
    require(document['schema'] == 'tb371fc-qce-bam-reference-profile-v1', 'profile schema version')
    require(document['bound'] is True, 'production requires the reviewed bound profile')
    require(type(document['name']) is str and re.fullmatch(r'[A-Za-z0-9._-]{1,96}', document['name']), 'profile name')
    require(document['name'] == VIRTUAL_POLICY['abi_name'], 'unreviewed profile name')
    identity = document['identity']
    require(type(identity) is dict and set(identity) == {f for f, _ in MACROS}, 'identity field set')
    require(all(type(v) is int and 0 <= v <= 0xffffffff for v in identity.values()), 'identity must be seven uint32 values')
    policy = document['policy']
    require(type(policy) is dict, 'policy must be an object')
    for field in FALSE_CAPABILITIES:
        require(policy.get(field) is False, 'unsupported capability: ' + field)
    for field in ('cold_reset_is_reference_policy', 'identity_selection_reviewed', 'initial_remote_state_reviewed'):
        require(policy.get(field) is True, 'unreviewed policy: ' + field)
    require(policy.get('default_enabled') is False, 'reference must remain default-off')
    require(type(policy.get('basis')) is str and policy['basis'].strip(), 'missing policy basis')
    require(type(document['source_inputs']) is list and document['source_inputs'] == list(SOURCE_PINS), 'complete immutable source pin set differs')


def read_profile(path):
    raw = read_regular(path, 128 * 1024)
    document = strict_json(raw)
    validate_profile_document(document)
    digest = sha256(canonical_json(document))
    require(digest == PROFILE_DIGEST, 'unreviewed canonical profile digest')
    require((len(raw), sha256(raw)) == (PROFILE_FILE_BYTES, PROFILE_FILE_SHA256), 'reviewed profile file identity differs')
    return dict(document=document, bound=True, name=document['name'],
                identity=document['identity'], digest=digest,
                file_sha256=sha256(raw), file_bytes=len(raw))


def verify_payloads(payloads):
    require(type(payloads) is dict and set(payloads) == {p['key'] for p in SOURCE_PINS}, 'source key set differs')
    # Verify every complete byte string before callers may decode any one.
    for pin in SOURCE_PINS:
        raw = payloads[pin['key']]
        require(type(raw) is bytes, 'source payload type: ' + pin['key'])
        require((len(raw), sha256(raw), git_blob(raw)) ==
                (pin['bytes'], pin['sha256'], pin['git_blob']),
                'complete source identity mismatch: ' + pin['key'])


def read_sources(source_root):
    source_root = Path(source_root)
    require(source_root.is_dir() and not source_root.is_symlink(), 'source-root must be a real directory')
    payloads = {}
    for pin in SOURCE_PINS:
        relative = Path(pin['path'])
        current = source_root
        for part in relative.parts[:-1]:
            current = current / part
            require(current.is_dir() and not current.is_symlink(), 'source ancestor is missing or symlink: ' + pin['path'])
        payloads[pin['key']] = read_regular(source_root / relative, pin['bytes'])
    verify_payloads(payloads)
    return payloads


def uncomment(text):
    # Preserve quoted strings and line positions; comments cannot supply anchors.
    token = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*', re.S)
    return token.sub(lambda m: ''.join('\n' if c == '\n' else ' ' for c in m.group())
                     if m.group().startswith(('/*', '//')) else m.group(), text)


def mask_strings(text):
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  lambda m: ' ' * len(m.group()), text, flags=re.S)


def unique_match(pattern, text, label, flags=0):
    found = list(re.finditer(pattern, text, flags | re.M))
    require(len(found) == 1, f'{label}: expected one scoped anchor, found {len(found)}')
    return found[0]


def brace_body(text, start, label):
    masked = mask_strings(text)
    require(start < len(text) and masked[start] == '{', label + ': missing opening brace')
    depth = 0
    for end in range(start, len(text)):
        if masked[end] == '{':
            depth += 1
        elif masked[end] == '}':
            depth -= 1
            if depth == 0:
                return text[start + 1:end], end + 1
    raise GenerationError(label + ': unterminated scoped block')


def block(text, pattern, label):
    match = unique_match(pattern, text, label)
    require(match.group().rstrip().endswith('{'), label + ': anchor must end in brace')
    start = match.end() - 1
    return brace_body(text, start, label)[0]


def direct_level(text):
    """Hide nested blocks so a parent DT property cannot come from a child."""
    masked = mask_strings(text)
    out, depth = [], 0
    for index, char in enumerate(masked):
        if char == '{':
            depth += 1
        out.append(text[index] if depth == 0 else ('\n' if char == '\n' else ' '))
        if char == '}':
            require(depth > 0, 'unbalanced child block')
            depth -= 1
    require(depth == 0, 'unbalanced child block')
    return ''.join(out)


def conditional(text, directive, discriminator, label):
    """Select one positive preprocessor arm, without consulting its #else."""
    lines = text.splitlines(keepends=True)
    candidates = []
    for i, line in enumerate(lines):
        if line.strip() != directive:
            continue
        depth, first_end, end = 1, None, None
        for j in range(i + 1, len(lines)):
            word = re.match(r'\s*#\s*(\w+)\b', lines[j])
            if word is None:
                continue
            op = word[1]
            if op in ('if', 'ifdef', 'ifndef'):
                depth += 1
            elif op == 'endif':
                depth -= 1
                if depth == 0:
                    end = j
                    break
            elif op in ('else', 'elif') and depth == 1 and first_end is None:
                first_end = j
        require(end is not None, label + ': unterminated preprocessor region')
        arm = ''.join(lines[i + 1:first_end if first_end is not None else end])
        if re.search(discriminator, arm, re.M):
            candidates.append(arm)
    require(len(candidates) == 1, f'{label}: expected one positive scoped region, found {len(candidates)}')
    return candidates[0]


class Defines:
    def __init__(self, text, label):
        self.label = label
        self.entries = {}
        for m in re.finditer(r'^\s*#\s*define[ \t]+([A-Za-z_]\w*)[ \t]+([^\n]+)',
                             text.replace('\\\n', ' '), re.M):
            self.entries.setdefault(m[1], []).append(m[2].strip())

    def expression(self, name):
        rows = self.entries.get(name, [])
        require(len(rows) == 1, f'{self.label}.{name}: expected one definition, found {len(rows)}')
        return rows[0]

    def value(self, name):
        return integer_expression(self.expression(name), self, (name,))


def integer_expression(expression, table=None, active=()):
    require(type(expression) is str and len(expression) <= 2048, 'integer expression length/type')
    # Strip C integer suffixes only after complete numeric tokens.
    expression = re.sub(r'\b(0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]+\b', r'\1', expression)
    try:
        tree = ast.parse(expression.strip(), mode='eval')
    except SyntaxError as exc:
        raise GenerationError('invalid integer expression syntax') from exc
    require(sum(1 for _ in ast.walk(tree)) <= 128, 'integer expression complexity')

    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            result = node.value
        elif isinstance(node, ast.Name) and table is not None:
            require(node.id not in active and len(active) < 32, 'integer macro cycle/depth')
            result = integer_expression(table.expression(node.id), table, active + (node.id,))
        elif isinstance(node, ast.BinOp):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, (ast.LShift, ast.RShift)):
                require(0 <= right < 64, 'integer shift outside 0..63')
            if isinstance(node.op, ast.BitOr): result = left | right
            elif isinstance(node.op, ast.BitAnd): result = left & right
            elif isinstance(node.op, ast.LShift): result = left << right
            elif isinstance(node.op, ast.RShift): result = left >> right
            elif isinstance(node.op, ast.Add): result = left + right
            elif isinstance(node.op, ast.Sub): result = left - right
            else: raise GenerationError('unsupported integer operator')
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            args = [evaluate(arg) for arg in node.args]
            if node.func.id == 'BIT' and len(args) == 1:
                require(args[0] < 64, 'BIT index outside 0..63')
                result = 1 << args[0]
            elif node.func.id == 'GENMASK' and len(args) == 2:
                high, low = args
                require(0 <= low <= high < 64, 'invalid GENMASK bounds')
                result = ((1 << (high + 1)) - 1) ^ ((1 << low) - 1)
            else:
                raise GenerationError('unsupported integer call')
        else:
            raise GenerationError('unsupported integer expression node')
        require(type(result) is int and 0 <= result <= 0xffffffffffffffff, 'integer result outside uint64')
        return result
    return evaluate(tree.body)


def shift(mask):
    require(type(mask) is int and 0 < mask <= 0xffffffff, 'invalid uint32 field mask')
    bottom = (mask & -mask).bit_length() - 1
    packed = mask >> bottom
    require(packed & (packed + 1) == 0, 'noncontiguous field mask')
    return bottom


def encode(value, mask):
    require(value >= 0 and value <= (mask >> shift(mask)), 'value does not fit source field')
    return value << shift(mask)


def dt_property(body, name):
    match = unique_match(r'^\s*' + re.escape(name) + r'\s*=\s*([^;]+);', direct_level(body), 'DT property ' + name, re.S)
    return match[1].strip()


def dt_cells(body, name):
    raw = dt_property(body, name)
    require(re.fullmatch(r'<\s*(?:0x[0-9a-fA-F]+|[0-9]+)(?:\s+(?:0x[0-9a-fA-F]+|[0-9]+))*\s*>', raw) is not None, 'DT numeric cells: ' + name)
    return [int(v, 0) for v in re.findall(r'0x[0-9a-fA-F]+|[0-9]+', raw)]


def dt_boolean(body, name):
    unique_match(r'^\s*' + re.escape(name) + r'\s*;', direct_level(body), 'DT boolean ' + name)


def parse_register_table(body, label, tuple_width=None):
    entries, cursor = {}, 0
    pattern = (r'\s*\[([A-Z][A-Z0-9_]*)\]\s*=\s*([^,]+),'
               if tuple_width is None else r'\s*\[([A-Z][A-Z0-9_]*)\]\s*=\s*\{([^}]+)\}\s*,')
    for m in re.finditer(pattern, body):
        require(not body[cursor:m.start()].strip(), label + ': unparsed table content')
        require(m[1] not in entries, label + ': duplicate register ' + m[1])
        if tuple_width is None:
            entries[m[1]] = integer_expression(m[2])
        else:
            cells = [integer_expression(x) for x in m[2].split(',')]
            require(len(cells) == tuple_width, label + ': tuple width')
            entries[m[1]] = cells
        cursor = m.end()
    require(entries and not body[cursor:].strip(), label + ': incomplete register table')
    return entries


def parse_sources(payloads):
    """Pure source parser. Production callers must first use read_sources()."""
    require(set(payloads) == {p['key'] for p in SOURCE_PINS}, 'parser source key set')
    source = {key: uncomment(raw.decode('utf-8')) for key, raw in payloads.items()}
    vm = Defines(source['vendor_regs'], 'vendor_regs')
    vq = Defines(source['vendor_qce'], 'vendor_qce')
    ub = Defines(source['linux_bam'], 'linux_bam')
    ur = Defines(source['linux_regs'], 'linux_regs')
    ndp = conditional(source['vendor_bam'], '#ifdef CONFIG_SPS_SUPPORT_NDP_BAM',
                      r'^\s*#define\s+BAM_MAX_EES\b', 'vendor NDP masks')
    vb = Defines(ndp, 'vendor_bam.NDP')
    # The complete enum fixes which positional table is the 4K layout.
    enum = block(source['vendor_spsi'], r'\benum\s+sps_bam_type\s*\{', 'BAM layout enum')
    labels = [v.strip() for v in enum.split(',') if v.strip()]
    require(labels == ['SPS_BAM_LEGACY', 'SPS_BAM_NDP', 'SPS_BAM_NDP_4K'], 'BAM layout enum differs')
    tables_body = block(source['vendor_bam'], r'\bstatic\s+u32\s+bam_regmap\[\]\[BAM_MAX_REGS\]\s*=\s*\{', 'vendor register map')
    tables, cursor = [], 0
    while cursor < len(tables_body):
        match = re.match(r'[\s,]*', tables_body[cursor:])
        cursor += match.end()
        if cursor == len(tables_body): break
        child, cursor = brace_body(tables_body, cursor, 'vendor layout child')
        tables.append(parse_register_table(child, 'vendor layout child'))
    require(len(tables) == len(labels), 'BAM layout table count differs')
    regs = tables[labels.index('SPS_BAM_NDP_4K')]
    upstream_table = block(source['linux_bam'], r'\bstatic\s+const\s+struct\s+reg_offset_data\s+bam_v1_7_reg_info\[\]\s*=\s*\{', 'upstream v1.7 table')
    upstream_regs = parse_register_table(upstream_table, 'upstream v1.7 table', 4)
    compared = ('REVISION', 'NUM_PIPES', 'CNFG_BITS', 'IRQ_SRCS_EE', 'IRQ_SRCS_MSK_EE',
                'P_CTRL', 'P_RST', 'P_IRQ_STTS', 'P_IRQ_CLR', 'P_IRQ_EN',
                'P_EVNT_REG', 'P_SW_OFSTS', 'P_DESC_FIFO_ADDR', 'P_EVNT_GEN_TRSHLD', 'P_FIFO_SIZES')
    for name in compared:
        require(name in regs and 'BAM_' + name in upstream_regs, 'missing 4K register: ' + name)
        require(regs[name] == upstream_regs['BAM_' + name][0], 'vendor/upstream 4K offset disagreement: ' + name)
    for name in ('IRQ_SRCS_UNMASKED_EE', 'PIPE_ATTR_EE', 'P_DESC_FIFO_ADDR_MSB'):
        require(name in regs, 'missing vendor 4K register: ' + name)
    offset_fn = block(source['vendor_bam'], r'\bbam_get_register_offset\s*\([^;{}]*\)\s*\{', 'vendor offset function')
    unique_match(r'index\s*=\s*\(bam_type\s*==\s*SPS_BAM_NDP_4K\)\s*\?\s*0x1000\s*:\s*0x80\s*;', offset_fn, '4K EE stride')
    require(upstream_regs['BAM_P_CTRL'][1] == upstream_regs['BAM_P_DESC_FIFO_ADDR'][2] == upstream_regs['BAM_IRQ_SRCS_EE'][3] == 0x1000, '4K pipe/event/EE stride')

    sps_dt = block(source['vendor_dt'], r'^\s*qcom,sps\s*\{', 'vendor SPS node')
    require(dt_property(sps_dt, 'compatible') == '"qcom,msm-sps-4k"', 'vendor SPS compatible')
    dt_boolean(sps_dt, 'qcom,pipe-attr-ee')
    bam_dt = block(source['linux_dt'], r'\bcryptobam:\s*dma-controller@1dc4000\s*\{', 'SM8250 BAM node')
    require(dt_property(bam_dt, 'compatible') == '"qcom,bam-v1.7.4", "qcom,bam-v1.7.0"', 'SM8250 BAM compatible order')
    bam_reg = dt_cells(bam_dt, 'reg')
    require(len(bam_reg) == 4 and bam_reg[0] == bam_reg[2] == 0, 'SM8250 BAM resource cell shape')
    channels, = dt_cells(bam_dt, 'num-channels')
    ees, = dt_cells(bam_dt, 'qcom,num-ees')
    ee, = dt_cells(bam_dt, 'qcom,ee')
    require((channels, ees, ee) == (8, 2, 0), 'SM8250 reference topology changed')
    dt_boolean(bam_dt, 'qcom,controlled-remotely')
    require(dt_property(bam_dt, 'interrupts') == '<GIC_SPI 272 IRQ_TYPE_LEVEL_HIGH>', 'BAM interrupt resource')
    qce_dt = block(source['linux_dt'], r'\bcrypto:\s*crypto@1dfa000\s*\{', 'SM8250 QCE node')
    qce_linux_reg = dt_cells(qce_dt, 'reg')
    require(dt_property(qce_dt, 'dmas') == '<&cryptobam 4>, <&cryptobam 5>', 'upstream QCE pipe pair')
    client_nodes = [('qcedev', r'\bqcom_cedev:\s*qcedev@1de0000\s*\{'), ('qcrypto', r'\bqcom_crypto:\s*qcrypto@1de0000\s*\{')]
    clients = []
    for name, pattern in client_nodes:
        node = block(source['vendor_dt'], pattern, 'vendor ' + name + ' node')
        require(dt_property(node, 'compatible') == '"qcom,' + name + '"', 'vendor client compatible')
        reg = dt_property(node, 'reg')
        resource_rows = re.findall(r'<([^>]+)>', reg)
        require(re.sub(r'<[^>]+>|[\s,]', '', reg) == '' and len(resource_rows) == 2, 'vendor client resource shape')
        resources = [[integer_expression(x) for x in row.split()] for row in resource_rows]
        require(resources == [[0x1de0000, 0x20000], [bam_reg[1], bam_reg[3]]], 'vendor client resources')
        pair, = dt_cells(node, 'qcom,bam-pipe-pair')
        client_ee, = dt_cells(node, 'qcom,bam-ee')
        require(client_ee == ee, 'vendor client EE differs')
        dt_boolean(node, 'qcom,ce-hw-shared')
        dt_boolean(node, 'qcom,no-clock-support')
        require(re.sub(r'\s+', '', dt_property(node, 'reg-names')) == '"crypto-base","crypto-bam-base"', 'vendor client resource names')
        require(dt_property(node, 'interrupts') == '<GIC_SPI 272 IRQ_TYPE_LEVEL_HIGH>', 'vendor client IRQ')
        clients.append(dict(name=name, pair=pair, producer=2 * pair + 1, consumer=2 * pair, ee=client_ee))
    require([c['pair'] for c in clients] == [3, 2], 'vendor client pairs changed')
    owned_pipes = sorted(p for c in clients for p in (c['producer'], c['consumer']))
    require(len(set(owned_pipes)) == 4 and max(owned_pipes) < channels, 'overlapping/out-of-range owned pipes')
    owned_mask = sum(1 << pipe for pipe in owned_pipes)
    require(qce_linux_reg == [0, 0x1de0000 + vm.value('CRYPTO_VERSION_REG'), 0, 0x6000], 'vendor/upstream QCE aperture relationship')
    bam_match = block(source['linux_bam'], r'\bbam_of_match\[\]\s*=\s*\{', 'upstream BAM compatibles')
    unique_match(r'"qcom,bam-v1\.7\.0"\s*,\s*\.data\s*=\s*&bam_v1_7_reg_info', bam_match, 'v1.7 fallback selection')
    require('qcom,bam-v1.7.4' not in bam_match, 'unexpected numeric v1.7.4 branch')
    qce_match = block(source['linux_qce'], r'\bqce_crypto_of_match\[\]\s*=\s*\{', 'upstream QCE compatibles')
    require('"qcom,qce"' in qce_match and '.data' not in qce_match, 'upstream QCE version mapping changed')
    unique_match(r'major\s*!=\s*QCE_MAJOR_VERSION5\s*\|\|\s*minor\s*==\s*0', source['linux_qce'], 'upstream QCE runtime version check')
    # Binding is a complete pinned YAML file; only its scoped compatible/schema facts are used.
    binding = payloads['linux_binding'].decode('utf-8')
    binding_props = unique_match(r'^properties:\n(.*?)^required:', binding, 'BAM YAML properties', re.S)[1]
    compatible = unique_match(r'^  compatible:\n(.*?)^  clocks:', binding_props, 'BAM YAML compatible', re.S)[1]
    unique_match(r'^\s+- qcom,bam-v1\.7\.4\s*$', compatible, 'binding v1.7.4')
    unique_match(r'^\s+- const: qcom,bam-v1\.7\.0\s*$', compatible, 'binding fallback')
    for prop, description in (('num-channels', 'DMA channels'), ('qcom,num-ees', 'Execution Environments')):
        body = unique_match(r'^  ' + re.escape(prop) + r':\n(.*?)(?=^  [^ ]|\Z)', binding_props, 'binding ' + prop, re.S)[1]
        require(description in body and 'remotely controlled bam' in body, 'binding topology meaning: ' + prop)

    version_fields = []
    version = 0
    for part in ('MAJOR', 'MINOR', 'STEP'):
        mask = vm.value(f'CRYPTO_CORE_{part}_REV_MASK')
        require(mask == ur.value(f'CORE_{part}_REV_MASK'), 'QCE version mask disagreement: ' + part)
        require(shift(mask) == vm.value(f'CRYPTO_CORE_{part}_REV') == ur.value(f'CORE_{part}_REV_SHIFT'), 'QCE version shift disagreement: ' + part)
        number = vq.value(f'CRYPTO_CORE_{part}_VER_NUM')
        version |= encode(number, mask)
        version_fields.append(dict(field=part.lower(), mask=mask, shift=shift(mask), source_abi_value=number))
    require([x['source_abi_value'] for x in version_fields] == [5, 3, 1], 'named vendor QCE ABI changed')
    mti = unique_match(r'^\s*#define\s+BAM_VERSION_MTI_SUPPORT\(ver\)\s+\(ver\s*<=\s*(\d+)\)', source['vendor_sps_bam'], 'BAM MTI boundary')
    mti_max = int(mti[1])
    global_bam = Defines(source['vendor_bam'].split('#ifdef CONFIG_SPS_SUPPORT_NDP_BAM', 1)[0], 'vendor BAM global')
    minimum, maximum = global_bam.value('BAM_MIN_VERSION'), global_bam.value('BAM_MAX_VERSION')
    marker = mti_max + 1
    require(minimum <= marker <= maximum and marker > mti_max, 'virtual non-MTI marker outside source range')
    require(vb.value('BAM_NUM_EES') == ub.value('NUM_EES_MASK') << ub.value('NUM_EES_SHIFT'), 'EE count mask disagreement')
    require(vb.value('BAM_REVISION') == ub.value('REVISION_MASK'), 'BAM revision mask disagreement')
    require(vb.value('BAM_NUM_PIPES') == ub.value('BAM_NUM_PIPES_MASK'), 'pipe count mask disagreement')
    width_mask = vb.value('BAM_DATA_ADDR_BUS_WIDTH')
    width_fn = block(source['vendor_bam'], r'\bprint_bam_selected_reg\s*\([^;{}]*\)\s*\{', 'BAM width decoder')
    unique_match(r'\(\(bam_data_addr_bus_width\s*==\s*0x0\)\s*\?\s*32\s*:\s*36\)', width_fn, 'zero/nonzero address width decoder')
    wide_scope = conditional(source['vendor_sps_api'], '#if defined(CONFIG_PHYS_ADDR_T_64BIT) || defined(CONFIG_ARM_LPAE)', r'^\s*#define\s+SPS_GET_UPPER_ADDR\(', '36-bit descriptor macro scope')
    high = unique_match(r'^\s*#define\s+SPS_GET_UPPER_ADDR\(addr\)\s+\(\(addr\s*&\s*(0x[0-9A-Fa-f]+[uUlL]*)\)\s*>>\s*(\d+)\)', wide_scope, 'descriptor upper-address macro')
    upper_mask, upper_shift = integer_expression(high[1]), int(high[2])
    require(upper_shift == 32 and upper_mask == 0xf00000000, 'descriptor upper address mask/shift')
    upper_field = upper_mask >> upper_shift
    require((1 << (upper_field.bit_length() + 32)) - 1 == 0xfffffffff, 'descriptor 36-bit geometry')
    lpae_scope = conditional(source['vendor_spsi'], '#if defined(CONFIG_PHYS_ADDR_T_64BIT) || defined(CONFIG_ARM_LPAE)', r'^\s*#define\s+SPS_LPAE\b', 'SPS LPAE scope')
    unique_match(r'^\s*#define\s+SPS_LPAE\s+\(true\)\s*$', lpae_scope, 'SPS LPAE enabled branch')
    require(max(owned_pipes) < Defines(source['vendor_spsi'], 'spsi').value('BAM_MAX_PIPES'), 'SPS max pipe bound')

    config_expression = ub.expression('BAM_CNFG_BITS_DEFAULT')
    terms = re.findall(r'\bBAM_[A-Z0-9_]+\b', config_expression)
    require(len(terms) == len(set(terms)) == 17, 'CNFG default must contain 17 distinct terms')
    default_config = ub.value('BAM_CNFG_BITS_DEFAULT')
    command_mask = ub.value('BAM_CMD_ENABLE')
    require(command_mask == vm.value('CRYPTO_BAM_CD_ENABLE_MASK') == vb.value('CNFG_BITS_BAM_CD_ENABLE'), 'command descriptor bit disagreement')
    config_rows, union = [], 0
    for name in terms:
        value = ub.value(name)
        require(value and value & (value - 1) == 0 and union & value == 0, 'CNFG term overlap or width')
        union |= value
        config_rows.append(dict(name=name, value=value, bit=shift(value), retained=name != 'BAM_CMD_ENABLE'))
    require(union == default_config and command_mask & default_config, 'CNFG expression does not equal distinct source terms')
    engine_fields = []
    engines_mask = 0
    for name in ('ENCR_AES_SEL', 'DES_SEL', 'ENCR_SNOW3G_SEL', 'ENCR_KASUMI_SEL', 'SHA_SEL', 'SHA512_SEL', 'AUTH_AES_SEL', 'AUTH_SNOW3G_SEL', 'AUTH_KASUMI_SEL', 'ENCR_ZUC_SEL', 'AUTH_ZUC_SEL', 'ZUC_ENABLE', 'BAM_PIPE_SETS', 'AXI_WR_BEATS', 'AXI_RD_BEATS'):
        position = ur.value(name + '_SHIFT')
        mask = ur.value(name + '_MASK') if name in ('BAM_PIPE_SETS', 'AXI_WR_BEATS', 'AXI_RD_BEATS') else 1 << position
        require(shift(mask) == position and engines_mask & mask == 0, 'engine field overlap/shift')
        engines_mask |= mask
        engine_fields.append(dict(name=name, mask=mask, shift=position, virtual_value=0))
    revision_fields = {n: vb.value(n) for n in ('BAM_REVISION', 'BAM_NUM_EES', 'BAM_CE_BUFFER_SIZE', 'BAM_AXI_ACTIVE', 'BAM_USE_VMIDMT', 'BAM_SECURED', 'BAM_HAS_NO_BYPASS', 'BAM_HIGH_FREQUENCY_BAM', 'BAM_INACTIV_TMRS_EXST', 'BAM_NUM_INACTIV_TMRS', 'BAM_DESC_CACHE_DEPTH', 'BAM_CMD_DESC_EN', 'BAM_INACTIV_TMR_BASE')}
    pipe_fields = {n: vb.value(n) for n in ('BAM_NUM_PIPES', 'BAM_DATA_ADDR_BUS_WIDTH', 'BAM_PERIPH_NON_PIPE_GRP', 'BAM_NON_PIPE_GRP')}
    irq_masks = {kind: sum(vb.value(f'P_IRQ_{kind}_P_{suffix}') for suffix in suffixes)
                 for kind, suffixes in {
                     'STTS': ('HRESP_ERR_IRQ', 'PIPE_RST_ERR_IRQ', 'TRNSFR_END_IRQ', 'ERR_IRQ', 'OUT_OF_DESC_IRQ', 'WAKE_IRQ', 'TIMER_IRQ', 'PRCSD_DESC_IRQ'),
                     'CLR': ('HRESP_ERR_CLR', 'PIPE_RST_ERR_CLR', 'TRNSFR_END_CLR', 'ERR_CLR', 'OUT_OF_DESC_CLR', 'WAKE_CLR', 'TIMER_CLR', 'PRCSD_DESC_CLR'),
                     'EN': ('HRESP_ERR_EN', 'PIPE_RST_ERR_EN', 'TRNSFR_END_EN', 'ERR_EN', 'OUT_OF_DESC_EN', 'WAKE_EN', 'TIMER_EN', 'PRCSD_DESC_EN'),
                 }.items()}
    require(set(irq_masks.values()) == {0xff}, 'pipe IRQ mask disagreement')
    identity = {
        'qce-version': version,
        'qce-engines': 0,
        'bam-config': default_config & ~command_mask,
        'bam-revision': encode(ees, vb.value('BAM_NUM_EES')) | encode(marker, vb.value('BAM_REVISION')),
        'bam-num-pipes': encode(channels, vb.value('BAM_NUM_PIPES')) | encode(VIRTUAL_POLICY['descriptor_width_code'], width_mask),
        'bam-ee-attr': vb.value('BAM_ENABLED') | owned_mask,
        'bam-ee-initial-irq-mask': VIRTUAL_POLICY['ee_initial_irq_mask'],
    }
    # First registration and cached clients are distinct source paths.
    get_bam = block(source['vendor_qce'], r'\bqce_sps_get_bam\s*\([^;{}]*\)\s*\{', 'qce_sps_get_bam')
    cached = block(get_bam, r'\bif\s*\(pbam\)\s*\{', 'cached BAM branch')
    require('pce_dev->support_cmd_dscr = pbam->support_cmd_dscr;' in cached and 'goto ret;' in cached, 'cached BAM support flag path')
    require('readl' not in cached and 'no_get_around' not in cached, 'cached BAM branch unexpectedly changes first-registration state')
    no_command = block(get_bam, r'\bif\s*\(!pbam->support_cmd_dscr\)\s*\{', 'first no-command branch')
    require('pce_dev->no_get_around = false;' in no_command, 'first no-command workaround flag')
    require(len(re.findall(r'\breadl_relaxed\s*\(', get_bam)) == 1, 'first registration CNFG read count')
    unique_match(r'if\s*\(!pce_dev->support_cmd_dscr\s*\|\|\s*pce_dev->is_shared\)\s*bam.manage\s*=\s*SPS_BAM_MGR_DEVICE_REMOTE', get_bam, 'remote BAM management branch')
    setup = block(source['vendor_qce'], r'\bqce_setup_ce_sps_data\s*\([^;{}]*\)\s*\{', 'QCE setup data')
    unique_match(r'if\s*\(pce_dev->support_cmd_dscr\)\s*\{\s*rc\s*=\s*qce_setup_cmdlistptrs\(pce_dev\)', setup, 'conditional command-list setup')
    require(re.search(r'\b(?:writel|writel_relaxed|readl|readl_relaxed|sps_transfer|sps_transfer_one)\s*\(', setup) is None, 'unexpected setup MMIO/submit')
    dummy = block(source['vendor_qce'], r'\bsetup_dummy_req\s*\([^;{}]*\)\s*\{', 'dummy request RAM preparation')
    require(re.search(r'\b(?:writel|writel_relaxed|readl|readl_relaxed|sps_transfer|sps_transfer_one)\s*\(', dummy) is None, 'unexpected dummy MMIO/submit')
    support = block(source['vendor_qce'], r'\bqce_hw_support\s*\([^;{}]*\)\s*\{', 'QCE software support')
    hardcoded = []
    for name in ('sha_hmac', 'cmac', 'aes_xts', 'bam', 'aes_ccm'):
        unique_match(r'support->' + name + r'\s*=\s*true\s*;', support, 'hardcoded software support ' + name)
        hardcoded.append(name)
    geometry = dict(bam_base=bam_reg[1], bam_bytes=bam_reg[3], qce_base=0x1de0000,
                    qce_bytes=0x20000, union_base=bam_reg[1],
                    union_bytes=0x1de0000 + 0x20000 - bam_reg[1],
                    overlap_bytes=bam_reg[1] + bam_reg[3] - 0x1de0000,
                    qce_version_address=0x1de0000 + vm.value('CRYPTO_VERSION_REG'),
                    qce_engines_address=0x1de0000 + vm.value('CRYPTO_ENGINES_AVAIL'),
                    spi=272, channels=channels, execution_environments=ees, guest_ee=ee,
                    owned_pipes=owned_pipes, owned_pipe_mask=owned_mask, clients=clients)
    selected_registers = {name: regs[name] for name in compared + ('IRQ_SRCS_UNMASKED_EE', 'PIPE_ATTR_EE', 'P_DESC_FIFO_ADDR_MSB')}
    return {
        'identity': identity,
        'geometry': geometry,
        'layout': {'selector': 'SPS_BAM_NDP_4K', 'positional_index': labels.index('SPS_BAM_NDP_4K'),
                   'ee_stride': 0x1000, 'pipe_stride': 0x1000,
                   'bam_register_offsets': selected_registers,
                   'qce_vendor_offsets': {name: vm.value(name) for name in ('CRYPTO_VERSION_REG', 'CRYPTO_ENGINES_AVAIL', 'CRYPTO_BAM_CNFG_BITS_REG')},
                   'vendor_upstream_offsets_compared': list(compared)},
        'fields': {'qce_version': version_fields, 'qce_engines': engine_fields,
                   'qce_engines_defined_mask': engines_mask,
                   'bam_revision': revision_fields, 'bam_num_pipes': pipe_fields,
                   'pipe_irq_masks': irq_masks,
                   'pipe_control_cold_mask': sum(vb.value(name) for name in ('P_LOCK_GROUP', 'P_SYS_MODE', 'P_SYS_STRM', 'P_DIRECTION', 'P_EN')),
                   'descriptor_upper_mask': upper_field, 'descriptor_address_bits': 36,
                   'descriptor_fifo_size_mask': vb.value('P_DESC_FIFO_SIZE')},
        'config': {'source_default': default_config, 'removed_command_mask': command_mask,
                   'derived_value': identity['bam-config'], 'terms': config_rows},
        'revision': {'vendor_minimum': minimum, 'vendor_maximum': maximum, 'mti_upper_bound': mti_max,
                     'virtual_marker': marker, 'compatible_to_silicon_revision_mapping_claimed': False,
                     'ce_buffer_size_vendor_mask': vb.value('BAM_CE_BUFFER_SIZE'),
                     'ce_buffer_size_upstream_mask': ub.value('CE_BUFFER_SIZE'),
                     'disagreeing_buffer_field_value_is_zero': True},
        'consumer': {'first_registration_reads_config': True,
                     'first_cd0_clears_no_get_around': True,
                     'cache_hit_reads_config': False, 'cache_hit_clears_no_get_around': False,
                     'cd0_omits_command_list_setup': True,
                     'setup_data_and_dummy_have_no_direct_mmio_or_submit': True,
                     'software_support_flags_independent_of_engines': hardcoded,
                     'software_flags_are_not_emulated_crypto_capabilities': True},
        'virtual_policy': dict(VIRTUAL_POLICY),
    }


def compare_profile(derived, profile):
    require(derived['identity'] == profile['identity'], 'source-derived seven words differ from reviewed profile')


def render_include(profile, derived):
    compare_profile(derived, profile)
    digest = ', '.join('0x' + profile['digest'][i:i + 2] for i in range(0, 64, 2))
    lines = [
        '/* SPDX-License-Identifier: GPL-2.0-only */',
        '/* Generated by gen_qce_bam_sm8250.py from twelve complete pinned sources.',
        ' * Seven independently derived words are checked against the reviewed profile.',
        ' * Explicit virtual cold-control ABI; no measured physical identity/reset.',
        ' * No crypto, DMA, secure firmware execution or completion is implemented.',
        ' * Digest: canonical UTF-8 JSON, sorted keys, compact separators, no newline.',
        ' */',
        '#define QCB_PROFILE_BOUND true',
        '#define QCB_PROFILE_NAME ' + json.dumps(profile['name'], ensure_ascii=True),
        '#define QCB_PROFILE_DIGEST { ' + digest + ' }',
    ]
    for field, suffix in MACROS:
        lines.append(f'#define QCB_PROFILE_{suffix} UINT32_C(0x{derived["identity"][field]:08x})')
    return ('\n'.join(lines) + '\n').encode('utf-8')


def generate(profile_path=PROFILE, source_root=BUNDLE):
    profile = read_profile(profile_path)
    payloads = read_sources(source_root)
    derived = parse_sources(payloads)
    compare_profile(derived, profile)
    include = render_include(profile, derived)
    manifest = {
        'schema': 'tb371fc-qce-bam-generation-v1',
        'profile': profile, 'inputs': [dict(pin) for pin in SOURCE_PINS],
        'input_count': len(SOURCE_PINS), 'input_bytes': sum(pin['bytes'] for pin in SOURCE_PINS),
        'reference_scope': 'cold-control-only', 'public_source_pins_verified': True,
        'all_source_identities_verified_before_source_parse': True,
        'profile_bound': True, 'derived': derived,
        'output_path': INCLUDE_PATH, 'output_bytes': len(include), 'output_sha256': sha256(include),
        'physical_identity_measured': False, 'qemu_device_network_used': False,
        'limitations': profile['document']['policy']['limitations'],
    }
    return include, manifest


def write_new_or_equal(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as stream:
            stream.write(raw)
    except FileExistsError:
        require(read_regular(path, len(raw)) == raw, 'refusing to replace different existing output: ' + str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=PROFILE)
    parser.add_argument('--source-root', type=Path, default=BUNDLE)
    parser.add_argument('--output-root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, help='Explicit include path; overrides output-root include location')
    parser.add_argument('--receipt', type=Path, help='Explicit manifest path; overrides output-root manifest location')
    args = parser.parse_args()
    include, manifest = generate(args.profile, args.source_root)
    paths = [(args.output or args.output_root / INCLUDE_PATH, include),
             (args.receipt or args.output_root / MANIFEST_PATH, json_bytes(manifest))]
    for path, raw in paths:
        write_new_or_equal(path, raw)
    print(json.dumps({'input_count': len(SOURCE_PINS), 'profile_bound': True,
                      'reference_scope': 'cold-control-only',
                      'outputs': [{'path': str(path), 'bytes': len(raw), 'sha256': sha256(raw)} for path, raw in paths]}, sort_keys=True))


if __name__ == '__main__':
    main()
