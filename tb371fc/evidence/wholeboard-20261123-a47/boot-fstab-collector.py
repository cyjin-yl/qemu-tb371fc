"""Bounded read-only extraction of sanitized boot fstab requirements."""
import contextlib
import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import stat
import zlib

SYMBOLS_SOURCE = '"""Optional fixed-file symbol evidence only; no DT/device/guest access."""\nimport hashlib\nimport json\nfrom pathlib import Path\n\npath = Path(\'/work/kernel-symbols/production-4.19.157.kallsyms\')\nraw = path.read_bytes()\ndigest = hashlib.sha256(raw).hexdigest()\nassert digest == \'e32ef8e07019bd35105f7e2ddd6b7f47b9c23d828575afa6858b0ade37c49ab0\'\npc = 0xffffff80086a6844\nnames = {\'msm_bus_noc_sbm_config\', \'msm_bus_dev_sbm_config\',\n         \'msm_bus_commit_data\', \'gdsc_probe\'}\nsymbols = []\nfor line in raw.decode(\'ascii\').splitlines():\n    parts = line.split()\n    if len(parts) >= 3:\n        symbols.append((int(parts[0], 16), parts[1], parts[2]))\nsymbols.sort()\nselected = []\nfor address, kind, name in symbols:\n    if name not in names:\n        continue\n    successor = next((item for item in symbols if item[0] > address), None)\n    selected.append({\n        \'name\': name, \'type\': kind, \'address\': f\'0x{address:016x}\',\n        \'fault_pc_offset\': pc - address,\n        \'next_distinct_symbol\': None if successor is None else {\n            \'address\': f\'0x{successor[0]:016x}\', \'type\': successor[1],\n            \'name\': successor[2], \'address_span\': successor[0] - address},\n    })\nprimary = [entry for entry in selected if entry[\'name\'] == \'msm_bus_noc_sbm_config\']\nassert len(primary) == 1 and primary[0][\'fault_pc_offset\'] == 0x84\nprint(json.dumps({\n    \'scope\': \'Read only the fixed existing production kallsyms; bounded named output. The next symbol span is not an independent ELF function-size measurement.\',\n    \'input_symbols\': {\'path\': str(path), \'bytes\': len(raw), \'sha256\': digest},\n    \'fault_pc\': f\'0x{pc:016x}\', \'selected_symbols\': selected,\n    \'dt_read\': False, \'hardware_mmio_read\': False, \'guest_memory_written\': False,\n    \'qemu_started\': False,\n}, sort_keys=True))\n'
SYMBOLS_SOURCE_SHA = '5f36c7db5c56d580699b98fc42945a5f0b4aa4f0c3d71f3de3448635dce08048'
BOOT = Path('/work/device-assets/android-startup-inputs-20261123-01/boot_a.img')
OUTPUT = Path('/work/wholeboard-a47/boot-fstab-review-b95f97')
OFFSET, LENGTH, LIMIT = 37453824, 1298323, 2 * 1024 * 1024
stage = 'start'


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def flag_names(text):
    names = []
    for item in text.split(','):
        name = item.partition('=')[0]
        require(bool(re.fullmatch(r'[A-Za-z0-9_.-]+', name)), 'unsupported-flag-name')
        names.append(name)
    return names


def main():
    global stage
    stage = 'reviewed-symbol-collector'
    require(digest(SYMBOLS_SOURCE.encode()) == SYMBOLS_SOURCE_SHA, 'symbol-script-identity')
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exec(compile(SYMBOLS_SOURCE, '<reviewed-fixed-symbol-collector>', 'exec'),
             {'__name__': '__main__'})
    symbols = json.loads(buffer.getvalue())

    stage = 'fixed-boot-slice-read'
    before = BOOT.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_size == 100663296, 'boot-file-identity-size')
    with BOOT.open('rb') as stream:
        stream.seek(OFFSET)
        compressed = stream.read(LENGTH)
    require(len(compressed) == LENGTH, 'ramdisk-short-read')
    require(digest(compressed) == '563f4b334f8470155c6a451395342f7044b70e79b480a886c7641960f534f596', 'ramdisk-sha256')
    after = BOOT.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'boot-file-changed')

    stage = 'bounded-gzip'
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    archive = decoder.decompress(compressed, LIMIT + 1)
    require(len(archive) <= LIMIT and not decoder.unconsumed_tail, 'gzip-output-limit')
    require(decoder.eof and not decoder.unused_data, 'gzip-member-boundary')
    require(len(archive) == 1835592, 'cpio-size')
    require(digest(archive) == 'db7f63f0ccefd8d2320142b806d42ade4c6a2edaa3dc9f1ca6295d78e0f8e542', 'cpio-sha256')

    stage = 'bounded-newc-fstab-selection'
    offset, matches, trailer = 0, [], False
    while offset + 110 <= len(archive):
        header = archive[offset:offset + 110]
        require(header[:6] in (b'070701', b'070702'), 'cpio-header-magic')
        fields = [int(header[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        mode, size, name_size = fields[1], fields[6], fields[11]
        require(0 < name_size <= 4096, 'cpio-name-size')
        name_start, name_end = offset + 110, offset + 110 + name_size
        require(name_end <= len(archive), 'cpio-name-bounds')
        name = archive[name_start:name_end]
        require(name[-1:] == b'\0', 'cpio-name-terminator')
        name = name[:-1]
        data_start = (name_end + 3) & ~3
        data_end = data_start + size
        require(data_end <= len(archive), 'cpio-data-bounds')
        if name == b'TRAILER!!!':
            trailer = True
            break
        if name.rsplit(b'/', 1)[-1] == b'fstab.qcom':
            require(stat.S_ISREG(mode) and size == 5171, 'fstab-file-type-or-size')
            matches.append(archive[data_start:data_end])
        offset = (data_end + 3) & ~3
    require(trailer and len(matches) == 1, 'fstab-uniqueness-or-cpio-trailer')
    fstab = matches[0]

    stage = 'sanitized-fstab-config-parse'
    entries = []
    for number, line in enumerate(fstab.decode('utf-8').splitlines(), 1):
        columns = shlex.split(line, comments=True, posix=True)
        if not columns:
            continue
        require(len(columns) == 5, 'fstab-column-count')
        device, mountpoint, fs_type, mount_options, fs_mgr_options = columns
        require(bool(re.fullmatch(r'[A-Za-z0-9_./-]+', mountpoint)), 'mountpoint-shape')
        require(bool(re.fullmatch(r'[A-Za-z0-9_.-]+', fs_type)), 'filesystem-type-shape')
        mount_flags, fs_mgr_flags = flag_names(mount_options), flag_names(fs_mgr_options)
        label = None
        if '/by-name/' in device:
            source_kind, candidate = 'partition-label', device.rsplit('/by-name/', 1)[-1]
            require(bool(re.fullmatch(r'[A-Za-z0-9_.-]+', candidate)), 'partition-label-shape')
            label = candidate
        elif re.fullmatch(r'[A-Za-z0-9_.-]+', device):
            source_kind = 'logical-label' if 'logical' in fs_mgr_flags else 'named-source'
            label = device
        elif re.fullmatch(r'/dev/block/zram[0-9]+', device):
            source_kind, label = 'configured-zram-device', device.rsplit('/', 1)[-1]
        else:
            # Device wildcard paths are not partition-image evidence. Their
            # original path is deliberately not included in the report.
            source_kind = 'other-device-or-pattern-path-redacted'
        vbmeta_labels = []
        for item in fs_mgr_options.split(','):
            name, separator, value = item.partition('=')
            if name == 'avb' and separator and re.fullmatch(r'vbmeta(?:_[A-Za-z0-9_.-]+)?', value):
                vbmeta_labels.append(value)
        entries.append({
            'line_number': number, 'mountpoint': mountpoint, 'filesystem_type': fs_type,
            'source_kind': source_kind, 'partition_or_logical_label': label,
            'mount_flag_names': mount_flags, 'fs_mgr_flag_names': fs_mgr_flags,
            'first_stage_mount': 'first_stage_mount' in fs_mgr_flags,
            'logical': 'logical' in fs_mgr_flags,
            'slotselect': 'slotselect' in fs_mgr_flags,
            'slotselect_other': 'slotselect_other' in fs_mgr_flags,
            'avb': 'avb' in fs_mgr_flags, 'vbmeta_labels': vbmeta_labels,
            'flag_parameter_values_redacted_except_vbmeta_labels': True,
        })
    require(entries, 'empty-fstab-config')
    report = {
        'schema_version': 1,
        'scope': 'Sanitized configuration requirements from one fixed boot ramdisk fstab; no referenced partitions or paths were opened.',
        'captured_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'input_boot': {'path': str(BOOT), 'size_from_stat': before.st_size,
                       'full_image_sha256_from_prior_root_receipt': '78ba0f4f75bec85a80155a9e9ea8fa19546bbd88598a7923a16b069404899a9a',
                       'full_image_rehashed_by_this_collector': False,
                       'only_read_offset': OFFSET, 'only_read_bytes': LENGTH,
                       'metadata_unchanged_across_slice_read': True},
        'ramdisk_slice': {'bytes': len(compressed), 'sha256': digest(compressed)},
        'decompressed_cpio': {'bytes': len(archive), 'sha256': digest(archive),
                              'enforced_output_limit_bytes': LIMIT},
        'fstab': {'basename': 'fstab.qcom', 'bytes': len(fstab), 'sha256': digest(fstab),
                  'matching_archive_entries': len(matches), 'active_entry_count': len(entries),
                  'entries': entries},
        'image_presence_checked': False, 'android_boot_passed': False,
        'init_executed': False, 'partitions_mounted': False,
        'referenced_sources_or_key_paths_opened': False,
        'original_files_written': False, 'hardware_mmio_read': False,
        'raw_fstab_or_other_cpio_payload_published': False,
        'userdata_scope': 'Only a configured label/mount requirement; no userdata content, backup or partition read was requested or performed.',
        'interpretation': 'These entries describe this ramdisk configuration. They do not prove that system/vendor/product/super/userdata images exist, that a candidate image matches the configuration, or that Android executed successfully. Repeated mountpoints may express filesystem alternatives.',
    }
    stage = 'new-derived-output-files'
    OUTPUT.mkdir(parents=True, exist_ok=False)
    combined = {'success': True, 'symbols': symbols, 'boot_fstab': report,
                'ct_output_directory': str(OUTPUT)}
    for filename, value in [('sbm-symbols.json', symbols), ('fstab-configuration.json', report),
                            ('combined-report.json', combined)]:
        with (OUTPUT / filename).open('x') as stream:
            stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')
    print(json.dumps(combined, sort_keys=True))


try:
    main()
except Exception as error:
    # Never print archive text, flag parameters or unrelated member names on
    # failure. Root can distinguish access failures without exposing payloads.
    print(json.dumps({'success': False, 'stage': stage,
                      'error_type': type(error).__name__,
                      'error_code': str(error) if type(error) is RuntimeError else 'bounded-collector-failed'}))
    raise SystemExit(1)
