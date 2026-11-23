#!/usr/bin/env python3
"""Publish allowlisted hardware configuration/dependencies from a private DTB.

The input may contain bootargs, random seeds, serials and calibration data. No
unknown property's value or value hash is published. This is a configuration
inventory, not peripheral emulation, trace-derived behavior or a completeness
claim. Raw input and firmware must remain outside the source repository.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
from pathlib import Path
import re
import struct

MAX_DTB = 16 * 1024 * 1024
MAX_PROPERTY = 65536
STRINGS = frozenset({
    'compatible', 'device_type', 'status', 'model', 'reg-names', 'clock-names',
    'reset-names', 'power-domain-names', 'phy-names', 'interconnect-names',
    'dma-names', 'mbox-names', 'interrupt-names', 'memory-region-names',
    'nvmem-cell-names', 'clock-output-names', 'firmware-name', 'firmware-names',
    'enable-method', 'method', 'regulator-name', 'label', 'pinctrl-names',
    'qcom,gmu-firmware-name', 'qcom,sqe-firmware-name', 'qcom,firmware-name',
    'qcom,mdss-dsi-panel-name', 'qcom,mdss-dsi-panel-type', 'qcom,mdss-dsi-panel-destination',
    'function', 'pins', 'regulator-names', 'qcom,regulator-names',
    'asoc-codec-names', 'asoc-cpu-names', 'asoc-platform-names',
    'qcom,proxy-clock-names', 'qcom,proxy-reg-names', 'qcom,cmd_db_name',
})
CELLS = frozenset({
    '#address-cells', '#size-cells', '#interrupt-cells', '#clock-cells',
    '#reset-cells', '#power-domain-cells', '#phy-cells', '#iommu-cells',
    '#interconnect-cells', '#dma-cells', '#mbox-cells', '#gpio-cells',
    '#pwm-cells', '#sound-dai-cells', '#io-channel-cells', '#cooling-cells',
    'phandle', 'linux,phandle', 'reg', 'ranges', 'dma-ranges', 'interrupts',
    'interrupts-extended', 'interrupt-parent', 'interrupt-map', 'interrupt-map-mask',
    'clocks', 'assigned-clocks', 'assigned-clock-parents', 'assigned-clock-rates',
    'clock-frequency', 'clock-ranges', 'resets', 'power-domains', 'phys', 'iommus',
    'iommu-map', 'iommu-map-mask', 'interconnects', 'dmas', 'mboxes', 'gpios',
    'memory-region', 'nvmem-cells', 'remote-endpoint', 'next-level-cache',
    'pinctrl-0', 'pinctrl-1', 'pinctrl-2', 'pinctrl-3', 'sound-dai', 'pwms',
    'io-channels', 'cooling-device', 'operating-points-v2', 'operating-points',
    'opp-hz', 'opp-microvolt', 'opp-microamp', 'opp-level', 'opp-supported-hw',
    'clock-latency-ns', 'capacity-dmips-mhz', 'dynamic-power-coefficient',
    'cache-level', 'cache-size', 'cache-line-size', 'cache-sets',
    'd-cache-size', 'd-cache-line-size', 'd-cache-sets',
    'i-cache-size', 'i-cache-line-size', 'i-cache-sets',
    'redistributor-stride', '#redistributor-regions', 'num-cs', 'num-lanes',
    'bus-width', 'max-frequency', 'spi-max-frequency', 'i2c-clock-frequency',
    'regulator-min-microvolt', 'regulator-max-microvolt', 'regulator-min-microamp',
    'regulator-max-microamp', 'regulator-initial-mode', 'regulator-allowed-modes',
    'startup-delay-us', 'off-on-delay-us', 'reset-delay-us', 'reset-deassert-us',
    'qcom,msm-id', 'qcom,board-id', 'qcom,pmic-id', 'qcom,chipid',
    'qcom,gmu', 'qcom,kgsl-smmu', 'qcom,gpu-smmu', 'qcom,scm',
    'qcom,smem-state', 'qcom,smem-states', 'qcom,remote-pid',
    'qcom,mdss-dsi-panel-width', 'qcom,mdss-dsi-panel-height',
    'qcom,mdss-dsi-bpp', 'qcom,mdss-dsi-panel-framerate',
    'qcom,mdss-dsc-slice-height', 'qcom,mdss-dsc-slice-width',
    'qcom,mdss-dsc-slice-per-pkt', 'qcom,mdss-dsc-bit-per-component',
    'qcom,mdss-dsc-bit-per-pixel', 'qcom,mdss-dsc-version',
    'qcom,dev-ref-clk-en-reg', 'qcom,scsi-cmd-timeout',
    'hid-descr-addr', 'touchscreen-size-x', 'touchscreen-size-y',
    'drive-strength', 'post-power-on-delay-ms', 'data-lanes', '#global-interrupts',
    'dev-ref-clk-freq', 'freq-table-hz', 'lanes-per-direction',
    'qcom,drv-id', 'qcom,tcs-config', 'qcom,tcs-offset', 'qcom,pas-id',
    'qcom,smem-id', '#qcom,smem-state-cells', 'qcom,wlan-rc-num',
    'qcom,dsi-ctrl-num', 'qcom,dsi-phy-num',
    'qcom,iommu-dma-addr-pool', 'mhi,max-channels', 'mhi,buffer-len', 'mhi,timeout',
})
BOOLEANS = frozenset({
    'interrupt-controller', 'gpio-controller', 'dma-coherent', 'no-map',
    'reusable', 'cache-unified', 'opp-shared', 'regulator-always-on',
    'regulator-boot-on', 'regulator-active-discharge', 'wakeup-source',
    'always-on', 'non-removable', 'broken-cd', 'cap-sd-highspeed',
    'cap-mmc-highspeed', 'mmc-ddr-1_8v', 'mmc-hs200-1_8v', 'mmc-hs400-1_8v',
    'touchscreen-inverted-x', 'touchscreen-inverted-y', 'touchscreen-swapped-x-y',
    'bias-disable', 'bias-pull-up', 'bias-pull-down', 'input-enable',
    'output-low', 'output-high',
    'novatek,pen-support', 'novatek,stylus-resol-double', 'novatek,mp-support-dt',
})
DEPENDENCIES = {
    'clocks': '#clock-cells', 'assigned-clocks': '#clock-cells',
    'assigned-clock-parents': '#clock-cells', 'resets': '#reset-cells',
    'power-domains': '#power-domain-cells', 'phys': '#phy-cells',
    'iommus': '#iommu-cells', 'interconnects': '#interconnect-cells',
    'dmas': '#dma-cells', 'mboxes': '#mbox-cells',
    'interrupts-extended': '#interrupt-cells', 'gpios': '#gpio-cells',
    'pwms': '#pwm-cells', 'sound-dai': '#sound-dai-cells',
    'io-channels': '#io-channel-cells', 'cooling-device': '#cooling-cells',
    'memory-region': None, 'nvmem-cells': None, 'remote-endpoint': None,
    'next-level-cache': None, 'operating-points-v2': None,
    'pinctrl-0': None, 'pinctrl-1': None, 'pinctrl-2': None, 'pinctrl-3': None,
    'qcom,gmu': None, 'qcom,kgsl-smmu': None, 'qcom,gpu-smmu': None, 'qcom,scm': None,
    'qcom,dsi-ctrl': None, 'qcom,dsi-phy': None, 'qcom,mdp': None,
    'qcom,dsi-default-panel': None, 'qcom,panel-supply-entries': None,
    'panel': None, 'extcon': None, 'usb-phy': None,
    'asoc-codec': None, 'asoc-cpu': None, 'asoc-platform': None,
    'qcom,codec-aux-devs': None, 'qcom,wsa-devs': None,
    'qcom,msm_audio_ssr_devs': None, 'fsa4480-i2c-handle': None,
    'qcom,gpubw-dev': None,
    'qcom,smem-states': '#qcom,smem-state-cells',
}
NON_REFERENCE_GPIO_PROPERTIES = frozenset({'qcom,num-gpios', 'qcom,tlmm-gpio'})


class InvalidDTB(ValueError):
    pass


def aligned(value: int) -> int:
    return (value + 3) & ~3


def parse_dtb(blob: bytes) -> dict[str, dict[str, bytes]]:
    if not 40 <= len(blob) <= MAX_DTB:
        raise InvalidDTB('DTB length outside supported bound')
    magic, total, start, strings_start, reserve_start, version, compatible_version, _, strings_size, structure_size = struct.unpack_from('>10I', blob)
    if magic != 0xd00dfeed or total > len(blob) or total < 40:
        raise InvalidDTB('invalid header magic or totalsize')
    if version < 17 or compatible_version > 17:
        raise InvalidDTB('unsupported DTB version')
    if start % 4 or start + structure_size > total or strings_start + strings_size > total:
        raise InvalidDTB('out-of-bounds structure or strings')
    if reserve_start < 40 or reserve_start >= total:
        raise InvalidDTB('invalid reservation map offset')
    strings = blob[strings_start:strings_start + strings_size]
    end, cursor, stack, nodes = start + structure_size, start, [], {}
    while cursor + 4 <= end:
        token, = struct.unpack_from('>I', blob, cursor)
        cursor += 4
        if token == 1:
            terminal = blob.find(b'\0', cursor, end)
            if terminal < 0:
                raise InvalidDTB('unterminated node name')
            name = blob[cursor:terminal].decode('ascii')
            if (not re.fullmatch(r'[A-Za-z0-9,._+@\-]*', name)
                    or (not stack and name) or (stack and not name)):
                raise InvalidDTB('invalid node name/root')
            path = (stack[-1].rstrip('/') + '/' + name) if stack else '/'
            if path in nodes:
                raise InvalidDTB('duplicate node path')
            nodes[path] = {}
            stack.append(path)
            cursor = aligned(terminal + 1)
        elif token == 2:
            if not stack:
                raise InvalidDTB('unbalanced node end')
            stack.pop()
        elif token == 3:
            if not stack or cursor + 8 > end:
                raise InvalidDTB('property outside a node or truncated')
            length, name_offset = struct.unpack_from('>2I', blob, cursor)
            cursor += 8
            if cursor + length > end or name_offset >= len(strings):
                raise InvalidDTB('property data/name outside bounds')
            terminal = strings.find(b'\0', name_offset)
            if terminal < 0:
                raise InvalidDTB('unterminated property name')
            name = strings[name_offset:terminal].decode('ascii')
            if name in nodes[stack[-1]]:
                raise InvalidDTB('duplicate property')
            nodes[stack[-1]][name] = blob[cursor:cursor + length]
            cursor = aligned(cursor + length)
        elif token == 4:
            continue
        elif token == 9:
            if stack or '/' not in nodes:
                raise InvalidDTB('unfinished device tree')
            return nodes
        else:
            raise InvalidDTB(f'unknown FDT token {token}')
    raise InvalidDTB('missing FDT_END')


def u32s(raw: bytes) -> list[int]:
    if len(raw) % 4:
        raise InvalidDTB('cell data is not aligned to four bytes')
    return list(struct.unpack(f'>{len(raw) // 4}I', raw))


def integer(cells: list[int]) -> int:
    result = 0
    for value in cells:
        result = (result << 32) | value
    return result


def string_list(raw: bytes) -> list[str]:
    if not raw or not raw.endswith(b'\0'):
        raise InvalidDTB('string property is not NUL terminated')
    values = raw[:-1].decode('ascii').split('\0')
    if any(any(ord(char) < 32 or ord(char) > 126 for char in value) for value in values):
        raise InvalidDTB('non-printable string property')
    return values


def property_kind(path: str, name: str) -> str | None:
    # Chosen, aliases and symbols can carry bootloader/private additions. This
    # inventory does not publish their values, even under a usually safe key.
    if path == '/chosen' or path.startswith(('/chosen/', '/__', '/aliases')):
        return None
    if name in STRINGS:
        if name == 'model' and path != '/':
            return None
        return 'strings'
    if name in CELLS or name in DEPENDENCIES or name.endswith(('-supply', '-gpios', '-gpio', '_gpio')):
        return 'cells'
    if name in BOOLEANS:
        return 'boolean'
    return None


def encode_property(raw: bytes, kind: str) -> dict:
    if len(raw) > MAX_PROPERTY:
        raise InvalidDTB('property exceeds publication size bound')
    value = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
             'base64': base64.b64encode(raw).decode('ascii')}
    if kind == 'strings':
        value['strings'] = string_list(raw)
    elif kind == 'cells':
        value['be32'] = u32s(raw)
    elif raw:
        raise InvalidDTB('boolean property has nonempty value')
    else:
        value['boolean'] = True
    return value


def scalar(props: dict, name: str, default=None):
    if name not in props:
        return default
    values = u32s(props[name])
    if len(values) != 1:
        raise InvalidDTB(f'{name} is not one cell')
    return values[0]


def parent(path: str) -> str:
    return path.rsplit('/', 1)[0] or '/'


def bus_widths(nodes, path):
    props = nodes[path]
    ac, sc = scalar(props, '#address-cells', 2), scalar(props, '#size-cells', 1)
    if not 0 <= ac <= 2 or not 0 <= sc <= 2 or ac + sc == 0:
        raise InvalidDTB('unsupported bus address/size cell widths')
    return ac, sc


def decode_reg(nodes, path):
    bus = parent(path)
    ac, sc = bus_widths(nodes, bus)
    cells = u32s(nodes[path]['reg'])
    stride = ac + sc
    if not cells or len(cells) % stride:
        raise InvalidDTB('reg is empty or has a partial tuple')
    entries = []
    for offset in range(0, len(cells), stride):
        address = integer(cells[offset:offset + ac])
        size = integer(cells[offset + ac:offset + stride]) if sc else None
        entry = {'bus_address': address, 'parent_bus': bus}
        if size is not None:
            entry['size'] = size
        current, translated = bus, address
        while current != '/':
            props = nodes[current]
            if 'ranges' not in props:
                entry['translation_unavailable_at'] = current
                break
            child_ac, child_sc = bus_widths(nodes, current)
            higher = parent(current)
            parent_ac, _ = bus_widths(nodes, higher)
            ranges = u32s(props['ranges'])
            width = child_ac + parent_ac + child_sc
            if len(ranges) % width:
                raise InvalidDTB('ranges has a partial tuple')
            if ranges:
                matches = []
                for index in range(0, len(ranges), width):
                    child = integer(ranges[index:index + child_ac])
                    host = integer(ranges[index + child_ac:index + child_ac + parent_ac])
                    span = integer(ranges[index + child_ac + parent_ac:index + width])
                    if child <= translated and translated + (size or 1) <= child + span:
                        matches.append(host + translated - child)
                if len(matches) != 1:
                    entry['translation_unavailable_at'] = current
                    break
                translated = matches[0]
            current = higher
        else:
            entry['system_address'] = translated
        entries.append(entry)
    return entries


def groups(path: str, compatible: list[str], props: dict) -> list[str]:
    text = (path + ' ' + ' '.join(compatible)).lower()
    rules = {
        'cpu_cache': r'(?:^/cpus/|cortex|kryo|cache-controller|/l[123]-cache)',
        'interrupts_gpio': r'(?:gic|interrupt-controller|pinctrl|gpio|pdc)',
        'smmu_iommu': r'(?:smmu|iommu)',
        'gpu_gmu': r'(?:kgsl|adreno|(?:^|[/,_-])gpu(?:[/,@_-]|$)|(?:^|[/,_-])gmu(?:[/,@_-]|$))',
        'display': r'(?:mdss|dsi|dpu|display|panel|rotator)',
        'ufs_storage': r'(?:ufs|sdhci|sdcc)',
        'power_firmware': r'(?:rpmh?|rsc|aoss|aop|cmd-db|scm|psci|spmi|pmic|regulator|pm8150)',
        'clocks': r'(?:gcc|clock-controller|/clocks(?:/|$)|gpll)',
        'usb': r'(?:usb|dwc3|xhci|typec|redriver)',
        'audio': r'(?:audio|adsp|lpass|q6|slim|sound|wcd|tfa|codec|tdm|i2s)',
        'input_pen_keyboard': r'(?:novatek|touch|keyboard|mouse|hid|ctn730|pogo|(?:^|[/,_-])pen(?:[/,@_-]|$))',
        'wireless': r'(?:cnss|qca6390|wlan|wifi|bluetooth|bt-uart)',
        'camera_sensors': r'(?:camera|camss|cam_|cci@|csiphy|gc08a3|sensor)',
    }
    result = [name for name, pattern in rules.items() if re.search(pattern, text)]
    if '#clock-cells' in props and 'clocks' not in result:
        result.append('clocks')
    return result


def effective_status(nodes, path):
    """DT availability only; a node may still have no successfully bound driver."""
    current = path
    restrictions = []
    while True:
        if 'status' in nodes[current]:
            try:
                values = string_list(nodes[current]['status'])
                if len(values) != 1:
                    raise InvalidDTB('status is not a single string')
                if values[0] not in ('ok', 'okay'):
                    restrictions.append({'node': current, 'status': values[0]})
            except (ValueError, UnicodeError):
                restrictions.append({'node': current, 'status': 'invalid encoding'})
        if current == '/':
            break
        current = parent(current)
    return {'dt_available': not restrictions, 'restrictions': restrictions,
            'scope': 'status plus ancestor status; not a driver-binding observation'}


def interrupt_parent(nodes, phandles, path):
    """Follow explicit parent phandles or the closest enclosing IRQ domain.

    This matches the DT parent walk used by Linux of_irq_find_parent(). Merely
    inheriting the root's GIC would misdecode SPMI four-cell interrupt tuples.
    An interrupt-map nexus is preserved as a provider, not guessed into a GIC.
    """
    current, visited = path, set()
    while current not in visited:
        visited.add(current)
        props = nodes[current]
        if 'interrupt-parent' in props:
            handle = scalar(props, 'interrupt-parent')
            target = phandles.get(handle)
            if target is None:
                raise InvalidDTB(f'unknown interrupt-parent phandle 0x{handle:x}')
        elif current != '/':
            target = parent(current)
        else:
            return None
        if '#interrupt-cells' in nodes[target]:
            return target
        current = target
    raise InvalidDTB('cyclic interrupt parent chain')


def resolve_dependencies(nodes, phandles, path):
    result, problems = [], []
    props = nodes[path]
    for name, raw in props.items():
        if name in NON_REFERENCE_GPIO_PROPERTIES:
            continue
        if name in DEPENDENCIES:
            argument_key = DEPENDENCIES[name]
        elif name.endswith('-supply'):
            argument_key = None
        elif name.endswith(('-gpios', '-gpio', '_gpio')):
            argument_key = '#gpio-cells'
        else:
            continue
        try:
            values = u32s(raw)
            offset, item = 0, 0
            while offset < len(values):
                handle = values[offset]
                offset += 1
                if handle == 0:
                    result.append({'property': name, 'index': item, 'provider': None,
                                   'arguments': [], 'meaning': 'empty phandle placeholder'})
                    item += 1
                    continue
                provider = phandles.get(handle)
                if provider is None:
                    raise InvalidDTB(f'unknown provider phandle 0x{handle:x} at cell {offset - 1}')
                count = scalar(nodes[provider], argument_key) if argument_key else 0
                if count is None or not 0 <= count <= 16:
                    raise InvalidDTB(f'{provider} lacks a supported {argument_key}')
                if offset + count > len(values):
                    raise InvalidDTB('truncated provider argument tuple')
                result.append({'property': name, 'index': item, 'provider': provider,
                               'provider_phandle': handle, 'arguments': values[offset:offset + count]})
                offset += count
                item += 1
        except (ValueError, UnicodeError) as error:
            problems.append({'property': name, 'error': str(error)})
    if 'interrupts' not in props and 'interrupt-parent' not in props:
        return result, problems
    try:
        provider = interrupt_parent(nodes, phandles, path)
        if provider:
            handle = scalar(nodes[provider], 'phandle',
                            scalar(nodes[provider], 'linux,phandle'))
            result.append({'property': 'interrupt-parent', 'provider': provider,
                           'provider_phandle': handle,
                           'resolution': 'explicit phandle or closest enclosing interrupt domain'})
            if 'interrupts' in props:
                count = scalar(nodes[provider], '#interrupt-cells')
                values = u32s(props['interrupts'])
                if not count or len(values) % count:
                    raise InvalidDTB('interrupts lacks a resolvable tuple width')
                result.extend({'property': 'interrupts', 'index': index // count,
                               'provider': provider, 'provider_phandle': handle,
                               'arguments': values[index:index + count]}
                              for index in range(0, len(values), count))
        elif 'interrupts' in props:
            raise InvalidDTB('interrupts has no resolvable interrupt parent')
    except (ValueError, UnicodeError) as error:
        problems.append({'property': 'interrupts' if 'interrupts' in props else 'interrupt-parent',
                         'error': str(error)})
    return result, problems


def build_inventory(blob: bytes) -> dict:
    nodes = parse_dtb(blob)
    phandles = {}
    for path, props in nodes.items():
        for key in ('phandle', 'linux,phandle'):
            if key in props:
                value = scalar(props, key)
                if value in phandles and phandles[value] != path:
                    raise InvalidDTB('conflicting phandle providers')
                phandles[value] = path
    output, errors = {}, []
    for path, props in sorted(nodes.items()):
        published, omitted, rejected = {}, [], {}
        for name, raw in sorted(props.items()):
            kind = property_kind(path, name)
            if kind is None:
                omitted.append(name)
                continue
            try:
                published[name] = encode_property(raw, kind)
            except (ValueError, UnicodeError) as error:
                rejected[name] = type(error).__name__ + ': ' + str(error)
        compatible = published.get('compatible', {}).get('strings', [])
        entry = {'properties': published, 'omitted_property_names': omitted,
                 'rejected_properties': rejected, 'groups': groups(path, compatible, props),
                 'availability': effective_status(nodes, path) if property_kind(path, 'status')
                 else {'dt_available': None, 'reason': 'private subtree values omitted'}}
        if property_kind(path, 'reg') and 'reg' in props:
            try:
                entry['register_regions'] = decode_reg(nodes, path)
            except (ValueError, UnicodeError) as error:
                entry['register_decode_error'] = str(error)
        dependencies, unresolved = resolve_dependencies(nodes, phandles, path)
        # Preserve only dependencies derived from public properties. Inherited
        # interrupt-parent is metadata from an allowlisted provider path.
        entry['dependencies'] = [item for item in dependencies if property_kind(path, item['property'])]
        entry['unresolved_dependencies'] = [item for item in unresolved if property_kind(path, item['property'])]
        if entry['unresolved_dependencies']:
            errors.extend({'node': path, **item} for item in entry['unresolved_dependencies'])
        output[path] = entry
    groups_summary = {}
    for path, entry in output.items():
        for group in entry['groups']:
            groups_summary.setdefault(group, []).append(path)
    return {
        'schema_version': 1, 'kind': 'tb371fc-allowlisted-device-dependency-inventory',
        'generated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'source': {'interface': 'private /sys/firmware/fdt copy from running owner TB371FC',
                   'dtb_sha256': hashlib.sha256(blob).hexdigest(), 'dtb_bytes': len(blob)},
        'scope': 'Static configuration/dependencies only; no emulation, behavioral or completeness attestation',
        'coverage_complete': False, 'nodes': output,
        'summary': {'nodes': len(output), 'provider_phandles': len(phandles),
                    'published_properties': sum(len(item['properties']) for item in output.values()),
                    'omitted_properties': sum(len(item['omitted_property_names']) for item in output.values()),
                    'unresolved_dependency_properties': len(errors),
                    'groups': {name: len(paths) for name, paths in groups_summary.items()}},
        'group_nodes': groups_summary, 'unresolved_dependencies': errors,
        'privacy': {'raw_dtb_public': False, 'unknown_property_values': 'omitted without per-value hashes',
                    'chosen_alias_symbol_values': 'all omitted',
                    'excluded': ['bootargs', 'serial numbers', 'MAC/Bluetooth addresses', 'seeds',
                                 'raw input', 'credentials', 'proprietary firmware payloads']},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dtb', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build_inventory(args.dtb.read_bytes())
    result['source']['normalizer_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result['summary'], sort_keys=True))


if __name__ == '__main__':
    main()
