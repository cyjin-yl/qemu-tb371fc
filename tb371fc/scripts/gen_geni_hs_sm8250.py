#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Derive the bounded GENI HS reference contract from complete pinned inputs."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PINS = ({'id': 'current_gcc',
  'local_name': 'current_gcc.txt',
  'repository': 'cyjin-yl/qemu-tb371fc',
  'revision': '7d01e5e294b788f97a8120c2d34122ddb25e6343',
  'source_path': 'hw/misc/qcom-sm8250-gcc.c',
  'bytes': 29024,
  'sha256': 'f9f0dc291e6a183298a0114082df1f5ea65a369dc4f1685dfc330996121da95a',
  'git_blob_sha1': 'facf75a5aa64aa8638515a99a762d0319bd5bb5b',
  'url': 'https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/hw/misc/qcom-sm8250-gcc.c'},
 {'id': 'current_gcc_data',
  'local_name': 'current_gcc_data.txt',
  'repository': 'cyjin-yl/qemu-tb371fc',
  'revision': '7d01e5e294b788f97a8120c2d34122ddb25e6343',
  'source_path': 'hw/misc/qcom-sm8250-gcc-data.inc',
  'bytes': 63127,
  'sha256': '98e779ab6a57a72c88ee62ba22a5a52289a7873aac457fa0c378f86c6048f004',
  'git_blob_sha1': 'd0a9a8c7a44168309f22489a1c45074dbcec2de9',
  'url': 'https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/hw/misc/qcom-sm8250-gcc-data.inc'},
 {'id': 'current_gcc_h',
  'local_name': 'current_gcc_h.txt',
  'repository': 'cyjin-yl/qemu-tb371fc',
  'revision': '7d01e5e294b788f97a8120c2d34122ddb25e6343',
  'source_path': 'include/hw/misc/qcom-sm8250-gcc.h',
  'bytes': 1340,
  'sha256': 'd1c18e0f20bbd792fa27de71ea34a13c31f3c023ef4952b8adce8d4c099f1667',
  'git_blob_sha1': '6b5c796a60429b96b5e3e393c745df90ca511706',
  'url': 'https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/include/hw/misc/qcom-sm8250-gcc.h'},
 {'id': 'current_uart',
  'local_name': 'current_uart.txt',
  'repository': 'cyjin-yl/qemu-tb371fc',
  'revision': '7d01e5e294b788f97a8120c2d34122ddb25e6343',
  'source_path': 'hw/char/qcom-geni-uart.c',
  'bytes': 22547,
  'sha256': 'ef1c5e6e9400f907f227327e995fbe25d4b7ca09bd88429100e146202e32aad9',
  'git_blob_sha1': 'a1b805d30c7bcfe59bdd9a3390654f623a6ca1d4',
  'url': 'https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/hw/char/qcom-geni-uart.c'},
 {'id': 'current_uart_h',
  'local_name': 'current_uart_h.txt',
  'repository': 'cyjin-yl/qemu-tb371fc',
  'revision': '7d01e5e294b788f97a8120c2d34122ddb25e6343',
  'source_path': 'include/hw/char/qcom-geni-uart.h',
  'bytes': 1109,
  'sha256': '3cdd539724bf4bf72052fb91d6e29f553aeeebfdcb31d9b4a443b8243aac1dc7',
  'git_blob_sha1': '7ab464a234026dbf90c4f050faaa76609c83154f',
  'url': 'https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/include/hw/char/qcom-geni-uart.h'},
 {'id': 'qdev_clock',
  'local_name': 'qdev_clock.txt',
  'repository': 'cyjin-yl/qemu-tb371fc',
  'revision': '7d01e5e294b788f97a8120c2d34122ddb25e6343',
  'source_path': 'hw/core/qdev-clock.c',
  'bytes': 5316,
  'sha256': 'a34d5f6b8902937e320e5dc3bda654d4d5c898c98ff939ec75a28a38484f9a91',
  'git_blob_sha1': '861f78f94c64e5ef10825b7d561d474a3f7d04e1',
  'url': 'https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/hw/core/qdev-clock.c'},
 {'id': 'qdev_core',
  'local_name': 'qdev_core.txt',
  'repository': 'cyjin-yl/qemu-tb371fc',
  'revision': '7d01e5e294b788f97a8120c2d34122ddb25e6343',
  'source_path': 'hw/core/qdev.c',
  'bytes': 25849,
  'sha256': 'fd64dedac988aebd53b11fa97ffa8ddc086e5ed116d1611b728667d90faa5195',
  'git_blob_sha1': 'e2aab3d1fc61246ba2f1e147c0ff71c7c7712c55',
  'url': 'https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/hw/core/qdev.c'},
 {'id': 'upstream_header',
  'local_name': 'upstream_header.txt',
  'repository': 'torvalds/linux',
  'revision': '75f2c0b3690702c90863c2e138cb5520670845ea',
  'source_path': 'include/linux/soc/qcom/geni-se.h',
  'bytes': 16853,
  'sha256': 'd4bba63f2ecc744804be983b81f6e703bd3ddc37135d2edf34fc80397c83c411',
  'git_blob_sha1': 'c5e6ab85df0999b1b6c1fd8975d074161c84c2b9',
  'url': 'https://raw.githubusercontent.com/torvalds/linux/75f2c0b3690702c90863c2e138cb5520670845ea/include/linux/soc/qcom/geni-se.h'},
 {'id': 'upstream_se',
  'local_name': 'upstream_se.txt',
  'repository': 'torvalds/linux',
  'revision': '75f2c0b3690702c90863c2e138cb5520670845ea',
  'source_path': 'drivers/soc/qcom/qcom-geni-se.c',
  'bytes': 51564,
  'sha256': '95bd702fcf277b58075810089c2bf307aa21e72bd7006dc8b83b9f669affb90a',
  'git_blob_sha1': '15636a8dc907a875d4c1bf9af6e60c99505d61cb',
  'url': 'https://raw.githubusercontent.com/torvalds/linux/75f2c0b3690702c90863c2e138cb5520670845ea/drivers/soc/qcom/qcom-geni-se.c'},
 {'id': 'upstream_serial',
  'local_name': 'upstream_serial.txt',
  'repository': 'torvalds/linux',
  'revision': '75f2c0b3690702c90863c2e138cb5520670845ea',
  'source_path': 'drivers/tty/serial/qcom_geni_serial.c',
  'bytes': 58179,
  'sha256': 'b49bd3b3a1f72a5fe6588ca4084100c454edc43272693f0be4457eaff333ae64',
  'git_blob_sha1': '1e39122ec09f4dc80b9495a5dd69d3b084147d47',
  'url': 'https://raw.githubusercontent.com/torvalds/linux/75f2c0b3690702c90863c2e138cb5520670845ea/drivers/tty/serial/qcom_geni_serial.c'},
 {'id': 'vendor-serial',
  'local_name': 'vendor-msm-geni-serial.c',
  'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250',
  'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6',
  'source_path': 'drivers/tty/serial/msm_geni_serial.c',
  'bytes': 103712,
  'sha256': 'ce5476a19e9dcfe0824b5eba6d9f73e39ea76cd738441a67a40ac8a0ebd2026e',
  'git_blob_sha1': '87c58161b0bb7790acb561897628818fa834988c',
  'url': 'https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/tty/serial/msm_geni_serial.c'},
 {'id': 'vendor_gcc',
  'local_name': 'vendor_gcc.txt',
  'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250',
  'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6',
  'source_path': 'drivers/clk/qcom/gcc-kona.c',
  'bytes': 115577,
  'sha256': 'ed208678508c983d0989f1f135fac898c5a9a91c14af1fa42cd7ea2f24cd14cd',
  'git_blob_sha1': 'f78629254926e67dc7646da3e5e69f9bc2472ef6',
  'url': 'https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/clk/qcom/gcc-kona.c'},
 {'id': 'vendor_gcc_h',
  'local_name': 'vendor_gcc_h.txt',
  'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250',
  'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6',
  'source_path': 'include/dt-bindings/clock/qcom,gcc-kona.h',
  'bytes': 10232,
  'sha256': 'ec7b2a775a7f96a543c064c7fc733fb83ffa7e3dae3c4bb517200627ae5f7fed',
  'git_blob_sha1': '2e74c2800db35b88b585e2e74256bf4b1e08078e',
  'url': 'https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/include/dt-bindings/clock/qcom,gcc-kona.h'},
 {'id': 'vendor_header',
  'local_name': 'vendor_header.txt',
  'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250',
  'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6',
  'source_path': 'include/linux/qcom-geni-se.h',
  'bytes': 30916,
  'sha256': 'ff0313eaa597c87be8dc83fc7f9befc89ec0bdf26889f98aa1a5652ce980dff7',
  'git_blob_sha1': '3e2888af87c3c64ee0cafe082df9980aea53956d',
  'url': 'https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/include/linux/qcom-geni-se.h'},
 {'id': 'vendor_kona',
  'local_name': 'vendor_kona.txt',
  'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250',
  'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6',
  'source_path': 'arch/arm64/boot/dts/vendor/qcom/kona.dtsi',
  'bytes': 132499,
  'sha256': '12b4b191b0f517b0c9fdd42c23ad428193ef71e63831bade996f9a18b88ece55',
  'git_blob_sha1': 'e9651f09bd579616787894dc8514429063623b2d',
  'url': 'https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/arch/arm64/boot/dts/vendor/qcom/kona.dtsi'},
 {'id': 'vendor_platform_se',
  'local_name': 'vendor_platform_se.txt',
  'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250',
  'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6',
  'source_path': 'drivers/platform/msm/qcom-geni-se.c',
  'bytes': 55444,
  'sha256': '02c0accf53cb09f7f92261abb6532b8610539554f96bd3b81d753f4e4ae5c3bf',
  'git_blob_sha1': 'd31e9bd4a781ecd42dca6efb1ca7873437f8b590',
  'url': 'https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/platform/msm/qcom-geni-se.c'},
 {'id': 'vendor_qupv3',
  'local_name': 'vendor_qupv3.txt',
  'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250',
  'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6',
  'source_path': 'arch/arm64/boot/dts/vendor/qcom/kona-qupv3.dtsi',
  'bytes': 32816,
  'sha256': '6277002e4dc831d7d6744bb966723dc674793ae48f3f63488d6accba4f7f006d',
  'git_blob_sha1': '8c1f34eba8be07c949b90e46ae31156555236f0c',
  'url': 'https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/arch/arm64/boot/dts/vendor/qcom/kona-qupv3.dtsi'},
 {'id': 'vendor_se',
  'local_name': 'vendor_se.txt',
  'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250',
  'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6',
  'source_path': 'drivers/soc/qcom/qcom-geni-se.c',
  'bytes': 23952,
  'sha256': '3c76494b56eac865eab70206b0103f76ee6f19bd870abb98f3a9d2db16cc4dd0',
  'git_blob_sha1': 'ee89ffb6dde84cacc0f37a82918461b8b20eb206',
  'url': 'https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/soc/qcom/qcom-geni-se.c'},
 {'id': 'vmstate_core',
  'local_name': 'vmstate_core.txt',
  'repository': 'cyjin-yl/qemu-tb371fc',
  'revision': '7d01e5e294b788f97a8120c2d34122ddb25e6343',
  'source_path': 'migration/vmstate.c',
  'bytes': 29944,
  'sha256': 'dc5aca380cef7f94867f700c87c9b46ddd25ef9fbed19bd6a1ae79279a1cd4a4',
  'git_blob_sha1': '9f49abda245eaf9053d7c1241f4a36e943cd54db',
  'url': 'https://raw.githubusercontent.com/cyjin-yl/qemu-tb371fc/7d01e5e294b788f97a8120c2d34122ddb25e6343/migration/vmstate.c'})

OUTPUTS = ('hw/char/qcom-sm8250-geni-hs-data.inc',
           'tb371fc/docs/geni-hs-sm8250-registers.json')
MAX_INPUT_BYTES = 256 * 1024
REFERENCE_INPUTS = (
    ('policy', 'tb371fc/docs/geni-hs-sm8250-reference-policy.json', 1923,
     '6d1ed6d87381f2d6e7cbc8c11804e150ad6c3be9b5e8db84654dcb9accb0d85f'),
    ('producer_facts', 'tb371fc/docs/geni-hs-sm8250-private-producer-facts.json', 3246,
     '1df0eb4cfd41cf8a42918407dba8e391a985164ecc9a1e513ca6b471c0bdfc80'),
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git_blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def need(condition, message):
    if not condition:
        raise ValueError(message)


def one(pattern, text, label, flags=0):
    matches = list(re.finditer(pattern, text, flags))
    need(len(matches) == 1, f'{label}: expected one match, found {len(matches)}')
    return matches[0]


def without_comments(text):
    token = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*', re.S)
    return token.sub(lambda m: re.sub(r'[^\n]', ' ', m[0])
                     if m[0].startswith(('/',)) else m[0], text)


def block(text, pattern, label):
    text = without_comments(text)
    match = one(pattern, text, label, re.M)
    start = match.end() - 1
    while start >= match.start() and text[start].isspace():
        start -= 1
    need(text[start] == '{', label + ': pattern must end at opening brace')
    level, quote, escaped = 1, None, False
    for pos in range(start + 1, len(text)):
        char = text[pos]
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == '{':
            level += 1
        elif char == '}':
            level -= 1
            if not level:
                return text[start + 1:pos]
    raise ValueError(label + ': unterminated block')


def function(text, name):
    # Require a declaration at line start: a call in an if-condition must not
    # become a second function definition merely because it precedes a brace.
    declaration = r'^[ \t]*(?:[A-Za-z_]\w*[ \t\r\n]+)+(?:\*[ \t]*)?'
    return block(text, declaration + re.escape(name) + r'\s*\([^;{}]*\)\s*\{', 'function ' + name)


def split_fields(text):
    fields, start, depth, quote, escaped = [], 0, [], None, False
    pairs = {')': '(', ']': '[', '}': '{'}
    for pos, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char in '([{':
            depth.append(char)
        elif char in ')]}':
            need(depth and depth.pop() == pairs[char], 'unbalanced initializer')
        elif char == ',' and not depth:
            fields.append(text[start:pos].strip())
            start = pos + 1
    need(not depth and quote is None, 'unterminated initializer')
    fields.append(text[start:].strip())
    return [item for item in fields if item]


class CIntegers:
    """Small, non-executing C integer-expression reader for pinned definitions."""

    def __init__(self, text='', values=None):
        self.definitions, self.values, self.active = {}, dict(values or {}), set()
        text = without_comments(text).replace('\\\n', '')
        for match in re.finditer(r'^\s*#define\s+([A-Za-z_]\w*)[ \t]+([^\n]+)', text, re.M):
            self.definitions.setdefault(match[1], []).append(match[2].strip())

    def name(self, name):
        if name in self.values:
            return self.values[name]
        expressions = self.definitions.get(name, [])
        need(len(expressions) == 1, f'define {name}: missing or ambiguous')
        need(name not in self.active, 'cyclic integer definition: ' + name)
        self.active.add(name)
        try:
            value = self.expression(expressions[0])
        finally:
            self.active.remove(name)
        self.values[name] = value
        return value

    def expression(self, expression):
        expression = re.sub(r'\b(0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]+\b', r'\1', expression)
        try:
            tree = ast.parse(expression.strip(), mode='eval')
        except SyntaxError as error:
            raise ValueError('unsupported C integer expression') from error

        def number(node):
            if isinstance(node, ast.Constant) and type(node.value) is int:
                return node.value
            if isinstance(node, ast.Name):
                return self.name(node.id)
            if isinstance(node, ast.UnaryOp):
                value = number(node.operand)
                if isinstance(node.op, ast.USub):
                    return -value
                if isinstance(node.op, ast.UAdd):
                    return value
                if isinstance(node.op, ast.Invert):
                    return ~value
            if isinstance(node, ast.BinOp):
                left, right = number(node.left), number(node.right)
                if isinstance(node.op, (ast.LShift, ast.RShift)):
                    need(0 <= right < 64, 'invalid integer shift')
                    return left << right if isinstance(node.op, ast.LShift) else left >> right
                if isinstance(node.op, ast.BitOr):
                    return left | right
                if isinstance(node.op, ast.BitAnd):
                    return left & right
                if isinstance(node.op, ast.BitXor):
                    return left ^ right
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, (ast.Div, ast.FloorDiv)):
                    need(right != 0 and left % right == 0, 'non-integral C quotient')
                    return left // right
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
                args = [number(arg) for arg in node.args]
                if node.func.id in ('BIT', 'GENI_BIT') and len(args) == 1:
                    need(0 <= args[0] < 64, 'invalid BIT index')
                    return 1 << args[0]
                if node.func.id == 'GENMASK' and len(args) == 2:
                    high, low = args
                    need(0 <= low <= high < 64, 'invalid GENMASK range')
                    return ((1 << (high - low + 1)) - 1) << low
            raise ValueError('unsupported integer expression node: ' + type(node).__name__)

        value = number(tree.body)
        need(-(1 << 63) <= value < (1 << 64), 'integer expression out of range')
        return value


def enum_values(text, name=None):
    pattern = r'\benum\s+' + re.escape(name) + r'\s*\{' if name else r'\benum\s*\{'
    body = block(text, pattern, 'enum ' + str(name))
    values, current = {}, -1
    for item in split_fields(body):
        match = one(r'^([A-Za-z_]\w*)(?:\s*=\s*(.+))?$', item, 'enum item', re.S)
        need(match[1] not in values, 'duplicate enum member ' + match[1])
        current = CIntegers(values=values).expression(match[2]) if match[2] else current + 1
        values[match[1]] = current
    return values


def require_patterns(text, patterns, label):
    for pattern in patterns:
        need(re.search(pattern, text, re.S) is not None, label + ': missing source contract ' + pattern)


def ordered(text, tokens, label):
    offset = 0
    for token in tokens:
        found = text.find(token, offset)
        need(found >= 0, label + ': missing/out-of-order token ' + token)
        offset = found + len(token)


def dt_node(text, label):
    return block(text, r'\b' + re.escape(label) + r'\s*:\s*[^;{}]+\{', 'DT node ' + label)


def dt_property(body, name):
    # The preceding semicolon is a zero-width boundary. Consuming it would
    # hide an immediately adjacent duplicate property from finditer().
    return one(r'(?:^|(?<=;))\s*' + re.escape(name) + r'\s*=\s*([^;]+);', body,
               'DT property ' + name, re.M)[1]


def dt_cells(body, name):
    value = dt_property(body, name)
    cells = re.findall(r'<([^<>]+)>', value)
    need(cells and re.sub(r'<[^<>]+>|,|\s+', '', value) == '', 'invalid DT cells: ' + name)
    return [cell.split() for cell in cells]


def dt_reg(body):
    cells = dt_cells(body, 'reg')
    need(len(cells) == 1 and len(cells[0]) == 2, 'expected one two-cell DT region')
    values = [CIntegers().expression(value) for value in cells[0]]
    need(values[0] >= 0 and values[1] > 0, 'invalid DT geometry')
    return values


def verify_source_bytes(pin, raw):
    actual = (len(raw), digest(raw), git_blob(raw))
    expected = (pin['bytes'], pin['sha256'], pin['git_blob_sha1'])
    need(len(raw) <= MAX_INPUT_BYTES and actual == expected,
         'complete public source identity mismatch: ' + pin['id'])
    return raw.decode('utf-8', 'strict')


def load_sources(source_root):
    result = {}
    for pin in SOURCE_PINS:
        result[pin['id']] = verify_source_bytes(pin, (source_root / pin['local_name']).read_bytes())
    need(len(result) == len(SOURCE_PINS) == 19, 'public source input cardinality')
    return result


def load_reference_inputs(root=ROOT):
    values, pins = {}, []
    for key, relative, size, sha in REFERENCE_INPUTS:
        raw = (root / relative).read_bytes()
        need((len(raw), digest(raw)) == (size, sha), 'reference input identity mismatch: ' + key)
        values[key] = json.loads(raw)
        pins.append({'role': key, 'path': relative, 'bytes': size, 'sha256': sha,
                     'git_blob_sha1': git_blob(raw)})
    return values['policy'], values['producer_facts'], pins


def validate_reference_inputs(policy, facts):
    need(policy['schema'] == 'tb371fc-geni-hs-sm8250-reference-policy-v1', 'reference policy schema')
    need(facts['schema'] == 'tb371fc-geni-hs-sanitized-private-producer-facts-v1', 'producer facts schema')
    for key in ('default_enabled', 'dma_data_path_implemented', 'dma_status_or_completion_implemented',
                'firmware_ram_execution_implemented', 'baud_timing_implemented',
                'electrical_flow_control_implemented', 'radio_peer_implemented',
                'physical_se6_assignment_known', 'physical_se6_revision_known',
                'physical_wrapper_version_known', 'physical_reset_retention_known',
                'private_raw_inputs_required_by_public_generator'):
        need(policy[key] is False, 'reference policy must not claim ' + key)
    need(policy['device_type'] == 'qcom-sm8250-geni-hs' and
         policy['enabled_property'] == 'enabled-reference', 'profile selection policy')
    need(policy['profile_id_ascii'] == 'GHS1' and
         policy['profile_id'] == int.from_bytes(b'GHS1', 'big'), 'reference profile identifier')
    need(policy['aligned_access_bytes'] == policy['wrapper_mapping_bytes'] == 4,
         'narrow aligned reference mapping')
    need(policy['unknown_register_policy'] == 'transaction-error', 'unknown register must fail')
    need(facts['raw_binary_disassembly_paths_serials_or_bootargs_included'] is False,
         'private raw contents must be absent')
    for key in ('write_to_readonly_alias_proved', 'execution_or_se6_selection_proved', 'high_firmware_byte_used'):
        need(facts['producer'][key] is False, 'unsupported private producer claim: ' + key)
    need(facts['producer']['both_use_byte_loads_and_bitfield_insert'] is True,
         'reviewed producer byte semantics missing')
    for key in ('runtime_condition_captured', 'se6_selection_established',
                'exhaustive_whole_image_execution_claimed', 'saved_binding_proves_revision_or_reset'):
        need(facts['selection'][key] is False, 'unsupported private selection claim: ' + key)
    need(all(value is False for value in facts['geometry_and_wrapper'].values()),
         'unsupported captured geometry or wrapper state')
    copies = facts['uart_image_copies']
    need(len(copies) == 2 and facts['uart_image_copies_identical'] is True and
         copies[0]['sha256'] == copies[1]['sha256'] and copies[0]['bytes'] == copies[1]['bytes'],
         'reviewed UART image copies disagree')
    header = facts['header_scalars']
    for key in ('protocol', 'firmware_version', 'configuration_version'):
        need(type(header[key]) is int and 0 <= header[key] <= 0xffff, 'invalid header scalar: ' + key)
    need(type(header['core_version']) is int and 0 <= header['core_version'] <= 0xffffffff,
         'invalid header core version scalar')
    need(header['core_version'] == policy['wrapper_version_choice'], 'wrapper profile/header choice mismatch')


def parse_dt(sources):
    source = sources['vendor_qupv3']
    uart = dt_node(source, 'qupv3_se6_4uart')
    need(dt_property(uart, 'compatible').strip() == '"qcom,msm-geni-serial-hs"', 'DT HS UART compatible')
    se_base, se_size = dt_reg(uart)
    need(dt_property(uart, 'reg-names').strip() == '"se_phys"', 'DT UART register name')
    names = re.findall(r'"([^"\n]+)"', dt_property(uart, 'clock-names'))
    need(names == ['se-clk', 'm-ahb', 's-ahb'], 'DT clock names/order')
    clocks = dt_cells(uart, 'clocks')
    need(len(clocks) == 3 and all(len(c) == 2 and c[0] == '&clock_gcc' for c in clocks), 'DT clock phandles')
    expected = ['GCC_QUPV3_WRAP0_S6_CLK', 'GCC_QUPV3_WRAP_0_M_AHB_CLK', 'GCC_QUPV3_WRAP_0_S_AHB_CLK']
    need([c[1] for c in clocks] == expected, 'DT wrap0 clock identities')
    irq = dt_cells(uart, 'interrupts-extended')
    need(len(irq) == 2 and len(irq[0]) == 4 and irq[0][:2] == ['&intc', 'GIC_SPI'] and
         irq[0][3] == 'IRQ_TYPE_LEVEL_HIGH' and len(irq[1]) == 3 and irq[1][0] == '&tlmm',
         'DT interrupt topology')
    wrapper_ref = dt_cells(uart, 'qcom,wrapper-core')
    need(wrapper_ref == [['&qupv3_0']], 'DT wrapper identity')
    wrapper = dt_node(source, 'qupv3_0')
    need(dt_property(wrapper, 'compatible').strip() == '"qcom,qupv3-geni-se"', 'DT wrapper compatible')
    wrapper_base, wrapper_size = dt_reg(wrapper)
    iommu = dt_cells(wrapper, 'iommus')
    need(len(iommu) == 1 and len(iommu[0]) == 3 and iommu[0][0] == '&apps_smmu', 'DT IOMMU topology')
    gcc = dt_node(sources['vendor_kona'], 'clock_gcc')
    need('"qcom,gcc-kona"' in dt_property(gcc, 'compatible'), 'DT GCC compatible')
    gcc_base, gcc_size = dt_reg(gcc)
    alternatives = [dt_reg(dt_node(source, label))[0]
                    for label in ('qupv3_se2_2uart', 'qupv3_se12_2uart')]
    return {'se_base': se_base, 'se_window_bytes': se_size, 'wrapper_base': wrapper_base,
            'wrapper_dt_window_bytes': wrapper_size, 'gic_spi': int(irq[0][2], 0),
            'wake_gpio': int(irq[1][1], 0), 'wake_irq_flags': int(irq[1][2], 0),
            'wake_byte': int(dt_cells(uart, 'qcom,wakeup-byte')[0][0], 0),
            'iommu_sid': int(iommu[0][1], 0), 'iommu_flags': int(iommu[0][2], 0),
            'clock_names': names, 'clock_macros': expected, 'gcc_base': gcc_base,
            'gcc_window_bytes': gcc_size, 'other_public_uart_bases': alternatives,
            'vendor_node_status': dt_property(uart, 'status').strip(' \t\r\n"'),
            'wake_gpio_or_dma_behavior_implemented_by_this_manifest': False}


def parse_clocks(sources, dt):
    vendor = sources['vendor_gcc']
    vendor_ids = CIntegers(sources['vendor_gcc_h'])
    current_ids = CIntegers(sources['current_gcc_h'])
    need(current_ids.name('SM8250_GCC_MMIO_SIZE') == dt['gcc_window_bytes'], 'GCC aperture disagreement')
    rows = []
    for macro in dt['clock_macros']:
        name = macro.lower()
        body = block(vendor, r'\bstruct\s+clk_branch\s+' + name + r'\s*=\s*\{', 'vendor clock ' + name)
        read = lambda field: CIntegers().expression(one(r'\.' + field + r'\s*=\s*([^,\n]+)', body, name + '.' + field)[1])
        halt, vote, mask = read('halt_reg'), read('enable_reg'), read('enable_mask')
        hardware = (read('hwcg_reg'), 1 << read('hwcg_bit')) if '.hwcg_reg' in body else (0, 0)
        data = sources['current_gcc_data']
        match = one(r'\[(\d+)\]\s*=\s*\{\s*"' + name + r'"\s*,', data, 'current clock index ' + name)
        index = int(match[1])
        current = block(data, r'\[' + str(index) + r'\]\s*=\s*\{\s*(?="' + name + '")', 'current clock ' + name)
        fields = split_fields(current)
        need(len(fields) == 12 and fields[1] == 'GCC_BRANCH', 'current GCC branch field layout')
        numbers = {i: CIntegers().expression(fields[i]) for i in (2, 3, 4, 5, 8, 9, 11)}
        need((numbers[2], numbers[3], numbers[4], numbers[8], numbers[9]) == (halt, vote, mask, *hardware),
             'vendor/current physical clock register mismatch: ' + name)
        need(0 <= index < current_ids.name('GCC_CLOCKS'), 'current GCC index out of range')
        rows.append({'name': name, 'vendor_dt_id': vendor_ids.name(macro), 'qemu_internal_index': index,
                     'halt_register': halt, 'vote_register': vote, 'vote_mask': mask,
                     'hwcg_register': hardware[0], 'hwcg_mask': hardware[1],
                     'parent_index': numbers[5], 'external_parent_index': numbers[11],
                     'external_parent_name': name + '_parent' if numbers[11] >= 0 else None})
    reset_id = vendor_ids.name('GCC_QUPV3_WRAPPER_0_BCR')
    vendor_reset = int(one(r'\[GCC_QUPV3_WRAPPER_0_BCR\]\s*=\s*\{\s*(0x[0-9a-fA-F]+)\s*\}',
                           vendor, 'vendor wrapper reset')[1], 0)
    current_reset = one(r'\[' + str(reset_id) + r'\]\s*=\s*\{\s*(0x[0-9a-fA-F]+)\s*,\s*(0x[0-9a-fA-F]+)\s*\}',
                        sources['current_gcc_data'], 'current wrapper reset')
    need(vendor_reset == int(current_reset[1], 0) and int(current_reset[2], 0) == 1 and
         reset_id < current_ids.name('GCC_RESETS'), 'wrapper reset source disagreement')
    require_patterns(sources['current_gcc'], [
        r'qdev_init_gpio_in_named\(dev,\s*gcc_hw_request,\s*"hw-request",\s*GCC_CLOCKS\)',
        r'qdev_init_gpio_out_named\(dev,\s*s->reset_out,\s*"reset",\s*GCC_RESETS\)',
        r'qdev_init_clock_out\(dev,\s*d->name\)', r'g_strdup_printf\("%s_parent",\s*d->name\)'], 'current GCC wiring')
    return {'bindings': rows, 'reset_index': reset_id, 'reset_register': vendor_reset,
            'reset_mask': 1, 'clock_array_slots': current_ids.name('GCC_CLOCKS'),
            'reset_array_slots': current_ids.name('GCC_RESETS'),
            'vendor_ids_are_not_qemu_internal_indices': True}


def parse_registers(sources):
    vendor = CIntegers(sources['vendor_header'])
    upstream = CIntegers(sources['upstream_header'])
    names = ('GENI_FW_REVISION_RO', 'GENI_FW_S_REVISION_RO', 'SE_GENI_DMA_MODE_EN',
             'SE_GENI_M_CMD0', 'SE_GENI_M_CMD_CTRL_REG', 'SE_GENI_M_IRQ_STATUS',
             'SE_GENI_M_IRQ_EN', 'SE_GENI_M_IRQ_CLEAR', 'SE_GENI_S_CMD0',
             'SE_GENI_S_CMD_CTRL_REG', 'SE_GENI_S_IRQ_STATUS', 'SE_GENI_S_IRQ_EN',
             'SE_GENI_S_IRQ_CLEAR', 'SE_GENI_TX_FIFOn', 'SE_GENI_RX_FIFOn',
             'SE_GENI_TX_FIFO_STATUS', 'SE_GENI_RX_FIFO_STATUS', 'SE_GENI_TX_WATERMARK_REG',
             'SE_GENI_RX_WATERMARK_REG', 'SE_GENI_RX_RFR_WATERMARK_REG', 'SE_HW_PARAM_0',
             'SE_HW_PARAM_1', 'SE_DMA_TX_IRQ_EN', 'SE_DMA_TX_IRQ_EN_SET', 'SE_DMA_TX_IRQ_EN_CLR',
             'SE_DMA_RX_IRQ_EN', 'SE_DMA_RX_IRQ_EN_SET', 'SE_DMA_RX_IRQ_EN_CLR')
    registers = {name: vendor.name(name) for name in names}
    for name in names:
        if name in upstream.definitions:
            need(registers[name] == upstream.name(name), 'public register disagreement: ' + name)
    extra = ('SE_GENI_M_IRQ_EN_SET', 'SE_GENI_M_IRQ_EN_CLEAR',
             'SE_GENI_S_IRQ_EN_SET', 'SE_GENI_S_IRQ_EN_CLEAR')
    registers.update({name: upstream.name(name) for name in extra})
    platform = CIntegers(sources['vendor_platform_se'])
    masks = {name: vendor.name(name) for name in (
        'FW_REV_PROTOCOL_MSK', 'FW_REV_PROTOCOL_SHFT', 'FW_REV_VERSION_MSK',
        'TX_FIFO_WIDTH_MSK', 'TX_FIFO_WIDTH_SHFT', 'TX_FIFO_DEPTH_MSK', 'TX_FIFO_DEPTH_SHFT',
        'RX_FIFO_WIDTH_MSK', 'RX_FIFO_WIDTH_SHFT', 'RX_FIFO_DEPTH_MSK', 'RX_FIFO_DEPTH_SHFT')}
    for name in ('FW_REV_PROTOCOL_MSK', 'FW_REV_PROTOCOL_SHFT'):
        need(masks[name] == upstream.name(name), 'public protocol field disagreement')
    tx_fields = ('TX_DMA_DONE', 'TX_EOT', 'TX_SBE', 'TX_RESET_DONE', 'TX_GENI_CANCEL_IRQ')
    rx_fields = ('RX_DMA_DONE', 'RX_EOT', 'RX_SBE', 'RX_RESET_DONE', 'RX_FLUSH_DONE',
                 'RX_GENI_GP_IRQ', 'RX_GENI_CANCEL_IRQ', 'RX_GENI_GP_IRQ_EXT')
    def union(names):
        value = 0
        for name in names:
            value |= vendor.name(name)
        return value
    wrapper_offset = vendor.name('QUPV3_HW_VER')
    need(wrapper_offset == CIntegers(sources['vendor_se']).name('QUP_HW_VER_REG'), 'wrapper register disagreement')
    need(registers['GENI_FW_S_REVISION_RO'] == CIntegers(sources['vendor_se']).name('GENI_FW_S_REVISION_RO'),
         'secondary revision register disagreement')
    version = {name: platform.name(name) for name in ('HW_VER_MAJOR_MASK', 'HW_VER_MAJOR_SHFT',
                'HW_VER_MINOR_MASK', 'HW_VER_MINOR_SHFT', 'HW_VER_STEP_MASK')}
    return {'offsets': registers, 'fields': masks, 'wrapper_version_offset': wrapper_offset,
            'wrapper_version_fields': version, 'dma_tx_irq_defined_mask': union(tx_fields),
            'dma_rx_irq_defined_mask': union(rx_fields), 'dma_tx_irq_fields': {n: vendor.name(n) for n in tx_fields},
            'dma_rx_irq_fields': {n: vendor.name(n) for n in rx_fields},
            'public_definitions_do_not_assert_all_registers_implemented': True}


def parse_producer_and_consumers(sources, registers, facts):
    vendor_protocols = enum_values(sources['vendor_header'], 'se_protocol_types')
    upstream_protocols = enum_values(sources['upstream_header'], 'geni_se_protocol_type')
    vendor_modes = enum_values(sources['vendor_header'], 'se_xfer_mode')
    upstream_modes = enum_values(sources['upstream_header'], 'geni_se_xfer_mode')
    protocol = vendor_protocols['UART']
    need(protocol == upstream_protocols['GENI_SE_UART'] == facts['header_scalars']['protocol'],
         'UART protocol sources/header scalar disagree')
    need(vendor_modes['FIFO_MODE'] == upstream_modes['GENI_SE_FIFO'] and vendor_modes['SE_DMA'] == 3,
         'vendor transfer mode contract')
    for name, reg, mask in [('get_se_proto', 'GENI_FW_REVISION_RO', 'FW_REV_PROTOCOL_MSK'),
                            ('get_se_m_fw', 'GENI_FW_REVISION_RO', 'FW_REV_VERSION_MSK'),
                            ('get_se_s_fw', 'GENI_FW_S_REVISION_RO', 'FW_REV_VERSION_MSK')]:
        body = function(sources['vendor_platform_se'], name)
        require_patterns(body, [r'geni_read_reg\(base,\s*' + reg + r'\)', r'&\s*' + mask], name)
        if name == 'get_se_proto':
            require_patterns(body, [r'>>\s*FW_REV_PROTOCOL_SHFT'], name)
    clocks = function(sources['vendor_platform_se'], 'se_geni_clks_on')
    ordered(clocks, ['clk_prepare_enable(rsc->m_ahb_clk)', 'clk_prepare_enable(rsc->s_ahb_clk)',
                     'clk_prepare_enable(rsc->se_clk)'], 'vendor clock enable sequence')
    vendor_info = function(sources['vendor-serial'], 'msm_geni_serial_get_ver_info')
    require_patterns(vendor_info, [r'get_se_proto\(uport->membase\)\s*!=\s*UART'], 'vendor UART consumer')
    ordered(vendor_info, ['get_se_proto(', 'get_se_m_fw(', 'get_se_s_fw(', 'geni_se_qupv3_hw_version('],
            'vendor version consumer')
    require_patterns(function(sources['vendor-serial'], 'msm_geni_serial_port_setup'),
                     [r'msm_port->xfer_mode\s*=\s*SE_DMA\s*;'], 'vendor later startup')
    require_patterns(function(sources['upstream_serial'], 'qcom_geni_serial_port_setup'),
                     [r'geni_se_read_proto\(', r'proto\s*!=\s*GENI_SE_UART'], 'upstream UART consumer')
    require_patterns(function(sources['vendor_se'], 'geni_se_get_qup_hw_version'),
                     [r'readl_relaxed\(wrapper->base\s*\+\s*QUP_HW_VER_REG\)'], 'vendor wrapper consumer')
    require_patterns(function(sources['vendor_se'], 'geni_se_select_mode'),
                     [r'case\s+GENI_SE_FIFO:', r'case\s+GENI_SE_DMA:'], 'vendor generic mode consumer')
    header_body = block(sources['upstream_se'], r'\bstruct\s+se_fw_hdr\s*\{', 'public SEFW header')
    fields, offset = {}, 0
    for declaration in header_body.split(';'):
        if not declaration.strip():
            continue
        match = one(r'^\s*__le(16|32)\s+([A-Za-z_]\w*)\s*$', declaration, 'SEFW field')
        need(match[2] not in fields, 'duplicate SEFW field')
        size = int(match[1]) // 8
        need(offset % size == 0, 'SEFW field requires unmodeled padding')
        fields[match[2]] = {'offset': offset, 'bytes': size}
        offset += size
    private_header = facts['header_scalars']
    need(fields['serial_protocol']['offset'] == private_header['protocol_byte_offset'] and
         fields['fw_version']['offset'] == private_header['firmware_low_byte_offset'],
         'public SEFW layout/private byte producer offset disagreement')
    producer = function(sources['upstream_se'], 'geni_write_fw_revision')
    ordered(producer, ['FIELD_PREP(FW_REV_PROTOCOL_MSK, serial_protocol)',
                       'FIELD_PREP(FW_REV_VERSION_MSK, fw_version)',
                       'writel(reg, se->base + SE_GENI_FW_REVISION)',
                       'writel(reg, se->base + SE_GENI_S_FW_REVISION)'], 'public revision producer')
    need(len(re.findall(r'\bwritel\s*\(', producer)) == 2, 'public producer has additional writes')
    defs = CIntegers(sources['upstream_se'])
    need(defs.name('FW_REV_VERSION_MSK') == registers['fields']['FW_REV_VERSION_MSK'], 'public version mask agreement')
    main, secondary = defs.name('SE_GENI_FW_REVISION'), defs.name('SE_GENI_S_FW_REVISION')
    need(main == facts['producer']['main_programming_offset'] and secondary == facts['producer']['secondary_programming_offset'],
         'public/private revision programming offset disagreement')
    low = private_header['firmware_version'] & registers['fields']['FW_REV_VERSION_MSK']
    word = ((protocol << registers['fields']['FW_REV_PROTOCOL_SHFT']) &
            registers['fields']['FW_REV_PROTOCOL_MSK']) | low
    need(word == facts['producer']['main_word'] == facts['producer']['secondary_word'], 'private producer result disagreement')
    return {'uart_protocol': protocol, 'vendor_fifo_mode': vendor_modes['FIFO_MODE'],
            'vendor_se_dma_mode': vendor_modes['SE_DMA'], 'upstream_fifo_mode': upstream_modes['GENI_SE_FIFO'],
            'upstream_se_dma_mode': upstream_modes['GENI_SE_DMA'],
            'transfer_mode_enums_are_source_specific': True,
            'sefw_header_fields': fields, 'sefw_header_bytes': offset,
            'sefw_magic': defs.name('SE_MAGIC_NUM'), 'main_programming_offset': main,
            'secondary_programming_offset': secondary, 'revision_word': word,
            'revision_low_byte': low, 'ignored_firmware_high_byte': private_header['firmware_version'] >> 8,
            'configuration_revision_byte': private_header['configuration_version'] & 0xff,
            'revision_formula': '(UART_protocol << protocol_shift) | (reviewed_header_firmware_version & version_mask)',
            'public_producer_consumer_interface_checked': True,
            'withheld_header_values_publicly_reproduced': False,
            'public_vendor_source_is_whole_original_kernel_identity': False,
            'original_irq_helper_is_not_claimed_identical_to_vendor_helper': True,
            'later_vendor_nonconsole_startup_requires_unimplemented_dma': True}


def parse_baseline_and_lifecycle(sources, registers, policy):
    uart_enum = enum_values(sources['current_uart'])
    baseline_map = {'GENI_FW_REV': 'GENI_FW_REVISION_RO', 'GENI_FW_S_REV': 'GENI_FW_S_REVISION_RO',
                    'GENI_M_EN': 'SE_GENI_M_IRQ_EN', 'GENI_S_EN': 'SE_GENI_S_IRQ_EN',
                    'GENI_M_EN_SET': 'SE_GENI_M_IRQ_EN_SET', 'GENI_S_EN_SET': 'SE_GENI_S_IRQ_EN_SET'}
    for old, public in baseline_map.items():
        need(uart_enum[old] == registers['offsets'][public], 'legacy/public register disagreement')
    uart = without_comments(sources['current_uart'])
    expression = one(r'case\s+GENI_HW_PARAM0:\s*case\s+GENI_HW_PARAM1:\s*return\s+([^;]+);',
                     uart, 'legacy FIFO geometry view')[1]
    view = CIntegers().expression(expression)
    masks = registers['fields']
    words = (view & masks['TX_FIFO_DEPTH_MSK']) >> masks['TX_FIFO_DEPTH_SHFT']
    bits = (view & masks['TX_FIFO_WIDTH_MSK']) >> masks['TX_FIFO_WIDTH_SHFT']
    header = CIntegers(sources['current_uart_h'])
    need(bits % 8 == 0 and words * (bits // 8) == header.name('QCOM_GENI_UART_FIFO_SIZE'), 'legacy FIFO geometry/capacity disagreement')
    need(words == policy['fifo_words'] and bits == policy['fifo_word_bits'], 'declared/legacy FIFO geometry disagreement')
    require_patterns(function(sources['qdev_core'], 'device_post_init'),
                     [r'qdev_prop_set_globals\(DEVICE\(obj\)\)'], 'QOM global property timing')
    ordered(function(sources['qdev_clock'], 'qdev_connect_clock_in'),
            ['assert(!dev->realized)', 'clock_set_source('], 'pre-realize clock connection')
    ordered(function(sources['vmstate_core'], 'vmstate_load_vmsd'),
            ['vmstate_subsection_load(', 'vmstate_post_load('], 'VMState subsection/load ordering')
    require_patterns(function(sources['vmstate_core'], 'vmstate_post_load'),
                     [r'vmsd->post_load\(opaque,\s*version_id\)'], 'VMState parent post-load callback')
    return {'legacy_mmio_bytes': header.name('QCOM_GENI_UART_MMIO_SIZE'),
            'legacy_fifo_bytes': header.name('QCOM_GENI_UART_FIFO_SIZE'),
            'legacy_fifo_words': words, 'legacy_fifo_word_bits': bits,
            'fifo_geometry_is_model_policy': True,
            'globals_applied_in_device_post_init': True,
            'clock_connection_requires_unrealized_device': True,
            'migration_subsections_loaded_before_parent_post_load': True,
            'source_contracts_are_not_compiled_or_runtime_acceptance': True}


def derive(sources, policy, facts, reference_pins=None):
    need(set(sources) == {pin['id'] for pin in SOURCE_PINS}, 'public source set differs')
    validate_reference_inputs(policy, facts)
    dt = parse_dt(sources)
    clocks = parse_clocks(sources, dt)
    registers = parse_registers(sources)
    producer = parse_producer_and_consumers(sources, registers, facts)
    lifecycle = parse_baseline_and_lifecycle(sources, registers, policy)
    need(dt['se_window_bytes'] == lifecycle['legacy_mmio_bytes'], 'SE window/legacy geometry disagreement')
    need(sorted(dt['other_public_uart_bases']) == sorted(facts['selection']['observed_static_configuration_bases']) and
         dt['se_base'] not in facts['selection']['observed_static_configuration_bases'], 'private static selection boundary')
    wrapper = policy['wrapper_version_choice']
    vf = registers['wrapper_version_fields']
    constants = {
        'GENI_HS_SE_BASE': dt['se_base'], 'GENI_HS_SE_WINDOW_BYTES': dt['se_window_bytes'],
        'GENI_HS_WRAPPER_VERSION_ADDRESS': dt['wrapper_base'] + registers['wrapper_version_offset'],
        'GENI_HS_WRAPPER_WINDOW_BYTES': policy['wrapper_mapping_bytes'],
        'GENI_HS_MAIN_FW_REVISION': producer['revision_word'],
        'GENI_HS_SECONDARY_FW_REVISION': producer['revision_word'],
        'GENI_HS_WRAPPER_VERSION': wrapper, 'GENI_HS_FIFO_BYTES': lifecycle['legacy_fifo_bytes'],
        'GENI_HS_PROFILE_ID': policy['profile_id'], 'GENI_HS_DMA_TX_IRQ_MASK': registers['dma_tx_irq_defined_mask'],
        'GENI_HS_DMA_RX_IRQ_MASK': registers['dma_rx_irq_defined_mask'],
        'GENI_HS_CFG_REVISION': producer['configuration_revision_byte'],
    }
    need(constants['GENI_HS_WRAPPER_VERSION_ADDRESS'] >= dt['wrapper_base'] and
         constants['GENI_HS_WRAPPER_VERSION_ADDRESS'] + policy['wrapper_mapping_bytes'] <= dt['wrapper_base'] + dt['wrapper_dt_window_bytes'],
         'version-only wrapper cell outside DT wrapper')
    return {'schema': 'tb371fc-geni-hs-sm8250-registers-v1',
            'source_pins': list(SOURCE_PINS), 'source_input_count': len(SOURCE_PINS),
            'source_input_bytes': sum(pin['bytes'] for pin in SOURCE_PINS),
            'reference_input_pins': reference_pins or [],
            'generated_constant_count': len(constants), 'generated_output_count': len(OUTPUTS),
            'window_count': 2, 'clock_binding_count': len(clocks['bindings']),
            'constants': constants, 'public_dt': dt, 'public_clock_bindings': clocks,
            'public_register_definitions': registers, 'producer_consumer_contract': producer,
            'legacy_policy_and_lifecycle': lifecycle,
            'reference_policy': policy, 'sanitized_private_producer_facts': facts,
            'wrapper_profile_version': {'major': (wrapper & vf['HW_VER_MAJOR_MASK']) >> vf['HW_VER_MAJOR_SHFT'],
                                        'minor': (wrapper & vf['HW_VER_MINOR_MASK']) >> vf['HW_VER_MINOR_SHFT'],
                                        'step': wrapper & vf['HW_VER_STEP_MASK'],
                                        'live_hardware_read_claimed': False},
            'source_contract_ids': [pin['id'] for pin in SOURCE_PINS],
            'scope': {'default_enabled': False, 'default_off_mapped_bytes': 0,
                      'strict_reference_fifo_model': True, 'private_binary_review_publicly_reproducible': False,
                      'physical_se6_firmware_state_known': False, 'firmware_execution_claimed': False,
                      'dma_data_path_or_completion_claimed': False, 'android_boot_claimed': False,
                      'source_generation_is_runtime_acceptance': False}}


def render_inc(manifest):
    wide = {'GENI_HS_SE_BASE', 'GENI_HS_WRAPPER_VERSION_ADDRESS'}
    lines = ['/* SPDX-License-Identifier: GPL-2.0-or-later */',
             '/* Generated by tb371fc/scripts/gen_geni_hs_sm8250.py; do not edit. */',
             '/* 19 full public inputs plus explicit policy and sanitized private facts. */',
             '/* Reference identity only: physical SE6 firmware/reset state is unknown. */']
    for name, value in manifest['constants'].items():
        macro = 'UINT64_C' if name in wide else 'UINT32_C'
        lines.append(f'#define {name} {macro}(0x{value:08x})')
    return '\n'.join(lines) + '\n'


def render_outputs(manifest):
    return {OUTPUTS[0]: render_inc(manifest).encode(),
            OUTPUTS[1]: (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()}


def compare_outputs(expected, actual):
    need(set(expected) == set(actual) == set(OUTPUTS), 'generated output set mismatch')
    for path in OUTPUTS:
        need(expected[path] == actual[path], 'generated output mismatch: ' + path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True,
                        help='Directory containing the nineteen exact files named by SOURCE_PINS')
    parser.add_argument('--output-root', type=Path, default=ROOT)
    args = parser.parse_args()
    sources = load_sources(args.source_root)
    policy, facts, pins = load_reference_inputs()
    manifest = derive(sources, policy, facts, pins)
    for relative, raw in render_outputs(manifest).items():
        path = args.output_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    print(json.dumps({'source_inputs': len(SOURCE_PINS), 'source_bytes': manifest['source_input_bytes'],
                      'outputs': list(OUTPUTS), 'constants': manifest['constants']}))


if __name__ == '__main__':
    main()
