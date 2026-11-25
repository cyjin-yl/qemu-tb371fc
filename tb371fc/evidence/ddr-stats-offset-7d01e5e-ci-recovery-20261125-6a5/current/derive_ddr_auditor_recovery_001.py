#!/usr/bin/env python3
"""New source-only DDR derivation from the exact public RPM auditor.

This is a recovery recipe, not a claim to reconstruct lost original byte hashes.
It never opens the CI archive or imports/executes producer tests or generators.
"""
import ast
import difflib
import hashlib
import json
from pathlib import Path
import pprint

ROOT = Path('/dev/shm/ddrstats-recovery-6a5')
BASELINE = ROOT / 'predecessor/audit_rpm_stats_offset_artifact.py'
OUT = ROOT / 'audit_ddr_stats_offset_artifact_7d01e5e_recovery_001.py'
SRC = Path('/dev/shm/ddrstats-candidate-6a5')
BASELINE_SHA = '7eb65a626ce253f3f8469948ddb0669d6bb5256f186f0ef9ffbb3c76b55b10f0'
ORIGINAL_LOST_SHA = 'e083e95d5087a71416afecf87c921255433306bb6c7b2c0ba10ae586f528a024'
raw = BASELINE.read_bytes()
assert len(raw) == 87901 and hashlib.sha256(raw).hexdigest() == BASELINE_SHA
s = raw.decode()
changes = []

def replace(old, new, expected=1):
    global s
    n = s.count(old)
    assert n == expected, (old[:100], n, expected)
    s = s.replace(old, new)
    changes.append({'kind': 'literal-or-block', 'old': old, 'new': new, 'count': n})

def function(name, new):
    global s
    node, = [n for n in ast.parse(s).body if isinstance(n, ast.FunctionDef) and n.name == name]
    lines = s.splitlines(keepends=True)
    old = ''.join(lines[node.lineno - 1:node.end_lineno])
    assert new.rstrip().startswith('def ' + name + '(')
    s = ''.join(lines[:node.lineno - 1]) + new.rstrip() + '\n' + ''.join(lines[node.end_lineno:])
    changes.append({'kind': 'function', 'name': name, 'before_sha256': hashlib.sha256(old.encode()).hexdigest(),
                    'after_sha256': hashlib.sha256((new.rstrip() + '\n').encode()).hexdigest()})

def assignment(name, value):
    global s
    node, = [n for n in ast.parse(s).body if isinstance(n, ast.Assign) and
              any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    lines = s.splitlines(keepends=True)
    old = ''.join(lines[node.lineno - 1:node.end_lineno])
    new = name + ' = ' + pprint.pformat(value, width=110, sort_dicts=False) + '\n'
    s = ''.join(lines[:node.lineno - 1]) + new + ''.join(lines[node.end_lineno:])
    changes.append({'kind': 'assignment', 'name': name, 'before': old, 'after': new})

# Rename only the model-specific names. rpmh machine/configuration names are retained.
for old, new in [('ddr-offset-modeled', 'rpm-offset-modeled'), ('ddr_offset_modeled', 'rpm_offset_modeled'),
                 ('ddr_offset_added', 'rpm_offset_added'), ('ddr_statistics_pointer_implemented', 'rpm_statistics_pointer_implemented'),
                 ('rpm-stats-offset', 'ddr-stats-offset'), ('rpm_stats_offset', 'ddr_stats_offset'),
                 ('RPM statistics', 'DDR statistics'), ('all RPM', 'all DDR'), ('RPM offset', 'DDR offset'),
                 ('Unknown RPM scenario', 'Unknown DDR scenario'), ('rpmstats-candidate', 'ddrstats-candidate')]:
    count = s.count(old)
    assert count > 0, old
    replace(old, new, count)
assignment('HEAD', '7d01e5e294b788f97a8120c2d34122ddb25e6343')
assignment('TREE', '5c25fff35bb41c3a8b018f0be965b4a9d3e9d5c4')
assignment('UPSTREAM', '75f2c0b3690702c90863c2e138cb5520670845ea')
replace('0x0c3f0004, 0x80010000, 0x52534f31, 0x000f0048',
        '0x0c3f001c, 0x80010000, 0x44534f31, 0x000f00b8')
replace('0x52534f00', '0x44534f00', 2)
replace('48000f00', 'b8000f00', 3)
replace('0000000152534f31000000000c3f000400000004000f0048',
        '0000000144534f31000000000c3f001c00000004000f00b8')
replace('0000000052534f31000000000c3f00040000000400000000',
        '0000000044534f31000000000c3f001c0000000400000000')
replace('0x0c3f0000 + 0x48 - 0x0c300000', '0x0c3f0000 + 0xb8 - 0x0c300000')
assignment('TEST_SHA', 'ec83a76070316b57fdf0590de152cb07dcb0b66464f1d5a30fc7a083527d0bc4')
assignment('MODEL_SHA', '4237cdc3a63263785c14a091f4eb92d14242ba79d114820c8ac3bb443ecaaced')
pins = {
    'hw/misc/qcom-sm8250-ddr-stats-offset.c': (9691, '4237cdc3a63263785c14a091f4eb92d14242ba79d114820c8ac3bb443ecaaced'),
    'include/hw/misc/qcom-sm8250-ddr-stats-offset.h': (364, '3f37eb47beb17f2f85af9b378a2400b8dc6fe93ff69d9f8cba9104bfad1c40dc'),
    'hw/misc/qcom-sm8250-ddr-stats-offset-data.inc': (346, 'e6ec892f78dac7e3f87a06618a3cc69e9692fa819c2ea60027bdb5e526fccd4f'),
    'hw/arm/lenovo-tb371fc.c': (103334, '51ee3224180a2e927572ee56f011b3d222f68fb97fef7e75ad188f972abaeaf1'),
    'hw/arm/Kconfig': (15508, '6cbe01a64aeea18706c4d255276819a6addf66bf6d4636381b75fbf12a6984af'),
    'hw/misc/Kconfig': (4239, '451eb84dce84cb9dde20e65220a39314b5acb8e36885efd172d878549b10fa5d'),
    'hw/misc/meson.build': (10935, 'ca26e093985792bb0ece8c598cc83a384c455b8b693e950d83678327946f121a'),
    'tb371fc/docs/ddr-stats-offset-sm8250-registers.json': (5304, '682074c1299455fa482b870a4b2f841ea1c1c74aee9ed6dac131cc9dcff33b7d'),
    'tb371fc/tests/test_ddr_stats_offset_sm8250.py': (41779, 'ec83a76070316b57fdf0590de152cb07dcb0b66464f1d5a30fc7a083527d0bc4'),
    'tb371fc/scripts/gen_ddr_stats_offset.py': (21540, '6d2c03467242065e21cc34247a4e944928d924a5f96021aa44252b075ae3dcf2'),
    'tb371fc/scripts/verify_ddr_stats_offset_generation.py': (5139, '4529d753567592ce1dfb957c052f551e397069e266e2a6f11a70bc0572bb8ae6'),
    'tb371fc/scripts/source_download.py': (4642, '4fde0300dc4c8777531402dc700fbed6364f16b0550fc852c3031fe641fe7dd7'),
    'tb371fc/scripts/ci_device_tests.py': (81555, 'c1478cab27f3937d2935571a0727233ae23bb392380762db10c76cda978d4a90'),
    'tb371fc/scripts/test_ci_device_tests.py': (52408, '9945c4a3fb45cfabf08fc27271243be8a72487d872aecfc56c9332c61b36f761'),
    'tb371fc/evidence/ddr-stats-offset-20261125-6a5/test-ci-contract.json': (4628, '0f0363f4d50339667bb53d4d9b8c4e456ce337b581b7b2f719f57037fa386901'),
}
for rel, expected in pins.items():
    data = (SRC / rel).read_bytes()
    assert (len(data), hashlib.sha256(data).hexdigest()) == expected, rel
assignment('SOURCE_PINS', pins)
assignment('PUBLIC_INPUTS', (
    ('primary-r11', 'OnePlusOSS/android_kernel_oneplus_sm8250', '98719830b0de5584f5588bbe9f4c8304d97a64c6',
     'drivers/soc/qcom/ddr_stats.c', 6442, '49b020908d7b3b22d0590eea466860c4d42a3a18',
     '26d08430d1ae76562118e313f91f285a3bce8b4a71175082aee6441b4dd3ebfb'),
    ('primary-r11', 'OnePlusOSS/android_kernel_oneplus_sm8250', '98719830b0de5584f5588bbe9f4c8304d97a64c6',
     'arch/arm64/boot/dts/vendor/qcom/kona-pm.dtsi', 2817, 'a5eba34468ee25209d8f07baf5efe9c53462ec2a',
     'c1f3a89f461cf1381335ffb2e3066026c28a27642389f9f92fccf540646e5231'),
    ('primary-upstream', 'torvalds/linux', '75f2c0b3690702c90863c2e138cb5520670845ea',
     'drivers/soc/qcom/qcom_stats.c', 11697, '2e380faf9080354fae120e74f0b9bd1f3786d3e5',
     'cf7447d3c9501203c699a403ea5f394ab9a809177f8b8b43e95699ff5dee5ed4'),
    ('primary-upstream', 'torvalds/linux', '75f2c0b3690702c90863c2e138cb5520670845ea',
     'arch/arm64/boot/dts/qcom/sm8250.dtsi', 177763, '7076720413ab2c34aaab618a4dcf7a27beb6076e',
     'ec9a5a3b9d9425475bf2fdaad772395ef2b331a9a4f92ac139bf83d0cad79ad0'),
))

helper = r'''
def disabled_core_expected(command):
    """Off-profile has no DDR leaf; actual unassigned transport chunks are checked."""
    op = "write" if command[0].startswith("write") else "read"
    width = {"b": 1, "w": 2, "l": 4, "q": 8}[command[0][-1]]
    same(command[1], BASE, "Disabled probes use the configured four-byte-aligned base")
    chunks = ((BASE, 4), (BASE + 4, 4)) if width == 8 else ((BASE, width),)
    return [(op, address, length, "(null)", "rejected") for address, length in chunks]


def active_mapping(store, directory, filename, profile):
    """Read the saved active FlatView and later bind it to the complete QMP reply."""
    boolean(profile, directory + ".configured profile")
    tree = store.text(directory + "/" + filename)
    need("FlatView #" in tree, directory + ": active flat-view header absent")
    pattern = r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (" + re.escape(TYPE) + r"\.[^\s]+)(?:\s|$)"
    leaves = {(int(a, 16), int(b, 16), name) for a, b, name in re.findall(pattern, tree)}
    same(leaves, {(BASE, BASE + 3, TYPE + ".offset")} if profile else set(),
         directory + ": exact active four-byte reference leaf or absent disabled leaf")
    return tree, {"active_leaf_count": len(leaves), "active_mapped_bytes": 4 * len(leaves),
                  "mtree_file": filename, "configured_profile_enabled": profile}


'''
replace('def probe_receipt(command):', helper.lstrip() + 'def probe_receipt(command):')
function('probe_receipt', r'''
def probe_receipt(command, profile=True):
    op, offset, width, reason = expected_rejection(command)
    text = command[0] + " " + hex(command[1])
    if op == "write":
        text += " " + hex(command[2])
    if profile:
        observation = {"callback_offset": offset, "callback_size": width,
                       "specific_diagnostic": f"{TYPE}: rejected {op} offset={offset:#x} size {width} ({reason})",
                       "device_callback_rejection_count": 1, "actual_core_rejections": []}
    else:
        observation = {"requested_address": BASE, "requested_size": width,
                       "unassigned_transport": True, "device_callback_rejection_count": 0,
                       "actual_core_rejections": core_rows(disabled_core_expected(command))}
    return {"command": text, **observation, "state_unchanged": True,
            "error_numeric_value_used_as_oracle": False}
''')
replace('''            tree = store.text(directory + "/mtree.txt")
            pattern = r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (" + re.escape(TYPE) + r"\.[^\s]+)(?:\s|$)"
            leaves = {(int(a, 16), int(b, 16), n) for a, b, n in re.findall(pattern, tree)}
            same(leaves, {(BASE, BASE + 3, TYPE + ".offset")}, directory + ".one exact 4-byte leaf")
            addresses = [0x0c3f0000, 0x0c3f0008, 0x0c3f001c, 0x0c3f0048, 0x0c3f004c, 0x0c3f00a4]''',
'''            tree, mapping = active_mapping(store, directory, "mtree.txt", True)
            addresses = [0x0c3f0000, 0x0c3f0018, 0x0c3f0020, 0x0c3f00b8, 0x0c3f00bc, 0x0c3f01fc]''')
replace('''            detail = {"exact_leaves": 1, "mapped_bytes": 4, "true_holes": addresses,''',
'''            detail = {"exact_leaves": 1, "mapped_bytes": 4, "active_mapping": mapping, "true_holes": addresses,''')
replace('''    worlds = qmp_sequence(qc, ["qmp_capabilities", "cont"] + [off] * 21 + ["system_reset", off], directory)
    same(device_rejections(stderr), [expected_rejection(cmd) for cmd in rejected], directory + ".10 disabled rejections")
    same(core_rejections(stderr), [], directory + ".no false core callback or hole diagnostics")
    same(checks[name], {"name": name, "passed": True, "default_profile_used": True,
                       "actual_rejections": [probe_receipt(c) for c in rejected],
                       "unknown_zero_is_not_a_valid_guest_offset": True}, directory + ".default-off report")
    results.append({"name": name, "actual_pure_getters": len(worlds) * GETTERS,
                    "actual_rejections": len(rejected), "no_global_profile_override": True,
                    "unknown_zero_not_treated_as_valid_read": True})''',
'''    before_tree, before_mapping = active_mapping(store, directory, "mtree-before-reset.txt", False)
    after_tree, after_mapping = active_mapping(store, directory, "mtree-after-reset.txt", False)
    worlds = qmp_sequence(qc, ["qmp_capabilities", "cont", off, ("hmp", before_tree)] + [off] * 20 +
                          ["system_reset", off, ("hmp", after_tree)], directory)
    generic = [row for command in rejected for row in disabled_core_expected(command)]
    same(len(generic), 12, directory + ".ten commands include two split qword operations")
    same(device_rejections(stderr), [], directory + ".no disabled device callback")
    same(core_rejections(stderr), generic, directory + ".all twelve exact unassigned chunks")
    same(checks[name], {"name": name, "passed": True, "default_profile_used": True,
                       "actual_rejections": [probe_receipt(c, False) for c in rejected],
                       "active_mapping_before_reset": before_mapping, "active_mapping_after_reset": after_mapping,
                       "default_off_mapped_bytes": 0,
                       "unknown_zero_is_not_a_valid_guest_offset": True}, directory + ".default-off report")
    results.append({"name": name, "actual_qom_snapshots": len(worlds), "actual_pure_getters": len(worlds) * GETTERS,
                    "actual_rejections": len(rejected), "actual_device_rejections": 0,
                    "actual_hole_rejections": len(generic), "actual_qtest_commands": len(tc),
                    "active_mapping_before_reset": before_mapping, "active_mapping_after_reset": after_mapping,
                    "default_off_mapped_bytes": 0, "no_global_profile_override": True,
                    "unknown_zero_not_treated_as_valid_read": True})''')
replace('''    expected_rejections = [("write", 0, 4, "read-only-register")]
    if not profile:
        expected_rejections = [("read", 0, 4, "disabled-or-invalid-access")] + expected_rejections + [
            ("read", 0, 4, "disabled-or-invalid-access")]
    same(device_rejections(stderr), expected_rejections, directory + ".restored access diagnostics")
    same(core_rejections(stderr), [], directory + ".no generic callback or hole rejection")''',
'''    expected_rejections = [("write", 0, 4, "read-only-register")] if profile else []
    expected_holes = [] if profile else [(op, BASE, 4, "(null)", "rejected") for op in ("read", "write", "read")]
    same(device_rejections(stderr), expected_rejections, directory + ".exact enabled callback or no disabled callback")
    same(core_rejections(stderr), expected_holes, directory + ".exact disabled unassigned reads and write")
    restored_tree, restored_mapping = active_mapping(store, directory, "mtree-after-restore.txt", profile)
    reset_tree, reset_mapping = active_mapping(store, directory, "mtree-after-reset.txt", profile)
    same(migration["active_mapping_after_restore"], restored_mapping, directory + ".reported restored active mapping")
    same(migration["active_mapping_after_reset"], reset_mapping, directory + ".reported reset active mapping")''')
replace('''    tail = ["query-status", state, state, "cont"] + [state] * 5 + ["system_reset"] + [state] * 3''',
'''    tail = ["query-status", state, state, ("hmp", restored_tree), "cont"] + [state] * 5 + ["system_reset"] + [state] * 3 + [("hmp", reset_tree)]''')
replace('''        "source_status": "completed", "target_status": "completed", "shared_timestamp_used": False,''',
'''        "source_status": "completed", "target_status": "completed", "shared_timestamp_used": False,
        "active_mapping_after_restore": restored_mapping, "active_mapping_after_reset": reset_mapping,
        "actual_device_rejections": len(expected_rejections), "actual_hole_rejections": len(expected_holes),
        "first_flatview_after_both_qom_snapshots_and_restored_ram_witness": True,
        "first_flatview_before_cont": True, "second_flatview_after_final_post_reset_device_read": True,''')

function('independent_public_derivation', r'''
def independent_public_derivation(texts):
    """Independent structural reads of all four pinned complete public sources."""
    vendor, vendor_dt, upstream, upstream_dt = texts
    node = one_match(r"\bqcom,ddr-stats@c3f0000\s*\{([^{}]*)\};", vendor_dt,
                     "vendor DDR offset node", re.S).group(1)
    regs = one_match(r"\breg\s*=\s*([^;]+);", node, "vendor reg").group(1)
    cells = [int(n, 0) for n in re.findall(r"0x[0-9a-fA-F]+|\b[0-9]+\b", regs)]
    same(cells, [0x0c300000, 0x1000, BASE, 4], "pinned vendor resource cells")
    names = re.findall(r'"([^"]+)"', one_match(r"\breg-names\s*=\s*([^;]+);", node, "vendor resource names").group(1))
    same(names, ["phys_addr_base", "offset_addr"], "vendor named resources")
    same(one_match(r'\bcompatible\s*=\s*"([^"]+)"\s*;', node, "vendor compatible").group(1),
         "qcom,ddr-stats", "vendor consumer compatible")
    probe = one_match(r"static int ddr_stats_probe\b(.*?)static int ddr_stats_remove\b",
                      vendor, "bounded vendor DDR probe", re.S).group(1)
    need(re.search(r"phys_ptr\s*=\s*ioremap_nocache\s*\(\s*offset->start\s*,\s*SZ_4\s*\)", probe) is not None,
         "Vendor maps exactly the four-byte offset resource")
    need(re.search(r"offset_addr\s*=\s*readl_relaxed\s*\(\s*phys_ptr\s*\)", probe) is not None,
         "Vendor uint32 offset read absent")
    need(re.search(r"if\s*\(\s*!offset_addr\s*\)\s*return\s+-ENODEV\s*;", probe) is not None,
         "Vendor zero-offset rejection absent")
    need(re.search(r"pdata->phys_addr_base\s*=\s*res->start\s*\+\s*offset_addr\s*;", probe) is not None,
         "Vendor base-plus-offset consumer absent")
    mmio_pattern = r"\b((?:read|write)[bwlq](?:_relaxed)?|__raw_(?:read|write)[bwlq]|ioread\w*|iowrite\w*|memcpy_(?:from|to)io)\s*\("
    same(re.findall(mmio_pattern, probe), ["readl_relaxed"], "Only one direct MMIO operation in vendor probe")
    sysfs = one_match(r"static int ddr_stats_create_sysfs\b(.*?)static int ddr_stats_probe\b",
                      vendor, "bounded vendor sysfs construction", re.S).group(1)
    same(re.findall(mmio_pattern, sysfs), [], "Sysfs construction has no direct payload MMIO")
    need(re.search(r"\.show\s*=\s*ddr_stats_show\s*;", sysfs) is not None,
         "Vendor payload reader is registered as sysfs callback")
    need(re.search(r"\bddr_stats_show\s*\(", sysfs) is None, "Sysfs construction does not invoke payload reader")
    upstream_node = one_match(r"\bsram@c3f0000\s*\{([^{}]*)\};", upstream_dt,
                              "upstream SM8250 stats node", re.S).group(1)
    same(one_match(r'\bcompatible\s*=\s*"([^"]+)"\s*;', upstream_node, "upstream compatible").group(1),
         "qcom,rpmh-stats", "upstream layout compatible")
    regs = one_match(r"\breg\s*=\s*([^;]+);", upstream_node, "upstream reg").group(1)
    cells_upstream = [int(n, 0) for n in re.findall(r"0x[0-9a-fA-F]+|\b[0-9]+\b", regs)]
    same(cells_upstream, [0, 0x0c3f0000, 0, 0x400], "upstream complete resource")
    same(one_match(r'\{\s*\.compatible\s*=\s*"qcom,rpmh-stats"\s*,\s*\.data\s*=\s*&(\w+)\s*\}',
                   upstream, "upstream compatible dispatch").group(1), "rpmh_data", "actual config selector")
    config = one_match(r"\bstatic const struct stats_config rpmh_data\s*=\s*\{([^{}]*)\};",
                       upstream, "upstream RPMh config", re.S).group(1)
    fixed = {}
    for field, expected in (("stats_offset", 0x48), ("ddr_stats_offset", 0xb8), ("num_records", 3)):
        fixed[field] = int(one_match(r"\." + field + r"\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*,", config, field).group(1), 0)
        same(fixed[field], expected, "upstream fixed " + field)
    for field, expected in (("dynamic_offset", "false"), ("appended_stats_avail", "false"),
                            ("subsystem_stats_in_smem", "true")):
        same(one_match(r"\." + field + r"\s*=\s*(true|false)\s*,", config, field).group(1), expected,
             "upstream config." + field)
    def macro(source, name):
        return int(one_match(r"^#define\s+" + name + r"\s+(0x[0-9a-fA-F]+|\d+)\s*$",
                             source, name, re.M).group(1), 0)
    header, maximum, magic = macro(upstream, "DDR_STATS_ENTRY_START_ADDR"), macro(upstream, "DDR_STATS_MAX_NUM_MODES"), macro(upstream, "DDR_STATS_MAGIC_KEY")
    same((header, maximum, magic, macro(upstream, "DDR_STATS_MAGIC_KEY_ADDR"), macro(upstream, "DDR_STATS_NUM_MODES_ADDR")),
         (8, 20, 0xa1157a75, 0, 4), "upstream DDR header and maximum ABI bounds")
    same((macro(vendor, "MAX_NUM_MODES"), macro(vendor, "MAGIC_KEY1")), (maximum, magic), "vendor ABI agrees")
    record = one_match(r"\bstruct ddr_stats_entry\s*\{([^{}]*)\};", upstream, "upstream DDR entry", re.S).group(1)
    same(re.findall(r"\b(u32|u64)\s+(\w+)\s*;", record),
         [("u32", "name"), ("u32", "count"), ("u64", "duration")], "upstream DDR record fields")
    vendor_record = one_match(r"\bstruct stats_entry\s*\{([^{}]*)\};", vendor, "vendor DDR entry", re.S).group(1)
    same(re.findall(r"\b(uint32_t|uint64_t)\s+(\w+)\s*;", vendor_record),
         [("uint32_t", "name"), ("uint32_t", "count"), ("uint64_t", "duration")], "vendor DDR record fields")
    vendor_header = one_match(r"\bstruct ddr_stats_data\s*\{([^{}]*)\};", vendor, "vendor DDR header", re.S).group(1)
    same(re.findall(r"\buint32_t\s+(\w+)\s*;", vendor_header), ["key", "entry_count"], "two-word vendor header")
    init = one_match(r"static void qcom_create_ddr_stat_files\b(.*?)static void qcom_create_soc_sleep_stat_files\b",
                     upstream, "upstream DDR construction", re.S).group(1)
    need(re.search(r"readl_relaxed\s*\(\s*reg\s*\+\s*config->ddr_stats_offset\s*\+\s*DDR_STATS_MAGIC_KEY_ADDR\s*\)", init) is not None,
         "Upstream construction differs: it probes payload magic")
    payload_address = cells_upstream[1] + fixed["ddr_stats_offset"]
    relative = payload_address - cells[0]
    same((payload_address, relative), (0x0c3f00b8, OFFSET_VALUE), "independent reference pointer arithmetic")
    return {"vendor_resource_base": cells[0], "offset_word_address": cells[2], "offset_window_bytes": cells[3],
            "vendor_probe_direct_mmio_reads": 1, "vendor_probe_direct_mmio_writes": 0,
            "vendor_zero_offset_returns": "-ENODEV", "vendor_probe_payload_read": False,
            "upstream_region_base": cells_upstream[1], "fixed_ddr_stats_offset": fixed["ddr_stats_offset"],
            "reference_payload_address": payload_address, "relative_word": relative,
            "little_endian_word_hex": relative.to_bytes(4, "little").hex(),
            "reference_header_bytes": header, "reference_maximum_records": maximum,
            "reference_record_bytes": 16, "reference_maximum_payload_bytes": header + maximum * 16,
            "payload_bytes_are_maximum_abi_span": True, "runtime_record_count_known": False,
            "upstream_cold_probe_differs_from_vendor": True, "payload_mapped_by_this_device": False,
            "captured_runtime_word_claimed": False, "native_reset_value_claimed": False,
            "generic_firmware_to_apss_alias_claimed": False}
''')
replace('''        "default_enabled": False, "enabled_aligned_read32_supported": True, "writes_supported": False,''',
'''        "default_enabled": False, "enabled_aligned_read32_supported": True,
        "reference_window_mapped_only_when_enabled": True, "default_off_mapped_bytes": 0,
        "statistics_magic_implemented": False, "statistics_entry_count_implemented": False,
        "writes_supported": False,''')
replace('''                 "payload_base": 0x0c3f0048, "payload_bytes": 96, "record_bytes": 32, "num_records": 3},''',
'''                 "payload_base": 0x0c3f00b8, "payload_bytes": 328, "record_bytes": 16,
                 "header_bytes": 8, "maximum_records": 20, "ddr_stats_offset": 0xb8,
                 "payload_bytes_is_maximum_abi_span": True,
                 "cold_probe_differs_from_vendor_sysfs_consumer": True},''')
replace('''        "binary_unchanged": True,
    }''',
'''        "binary_unchanged": True, "reference_window_mapped_only_when_enabled": True,
        "default_off_mapped_bytes": 0,
    }''')
replace('''        "scope": "One configured read-only DDR statistics offset word. No statistics payload, "
                 "DDR offset, firmware, READY, clock or timer behavior. No mutable device state "''',
'''        "scope": "One configured read-only DDR statistics offset word, mapped only when enabled. "
                 "The default-off address remains unmapped. No statistics payload, "
                 "RPM offset, firmware, READY, clock or timer behavior. No mutable device state "''')
replace('''        "mmio_limit": "A typed invalid operation reaches one rejecting device callback.''',
'''        "mmio_limit": "Enabled typed invalid operations reach one rejecting device callback. Disabled DDR has no active leaf and uses exact generic unassigned chunks, including two four-byte chunks for qword requests.''')
replace('''            "source_generation_checks": 2, "source_generation_inputs": 4,
            "actual_archive_evidence_verified"''',
'''            "source_generation_checks": 2, "source_generation_inputs": 4,
            "enabled_active_mapped_bytes": 4, "default_off_active_mapped_bytes": 0,
            "default_off_device_callback_count": 0, "default_off_generic_rejection_count": 12,
            "disabled_target_device_callback_count": 0, "disabled_target_generic_rejection_count": 3,
            "active_mapping_queries_verified": 7, "target_pure_getters_total_per_direction": 187,
            "actual_archive_evidence_verified"''')
replace('''        "shared_log_timestamp_claimed": False,
        "scope": "Actual compiled DDR offset component''',
'''        "shared_log_timestamp_claimed": False,
        "source_recovery": {"new_derivation": True,
                            "original_lost_auditor_sha256": "e083e95d5087a71416afecf87c921255433306bb6c7b2c0ba10ae586f528a024",
                            "original_source_byte_identity_claimed": False,
                            "predecessor_commit": "d103750ca2a191559daeb9eff4341b8c80013dc6",
                            "predecessor_auditor_sha256": "7eb65a626ce253f3f8469948ddb0669d6bb5256f186f0ef9ffbb3c76b55b10f0",
                            "lost_actual_receipts_recreated": False},
        "scope": "Actual compiled DDR offset component''')

compile(s, str(OUT), 'exec')
assert not OUT.exists()
OUT.write_text(s)
diff = ''.join(difflib.unified_diff(raw.decode().splitlines(True), s.splitlines(True),
                                  fromfile='public-d103750c/audit_rpm_stats_offset_artifact.py',
                                  tofile=OUT.name))
with (ROOT / 'ddr-auditor-recovery-001.diff').open('x') as f:
    f.write(diff)
def functions(source):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(source).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
before, after = functions(raw.decode()), functions(s)
same_functions = sorted(k for k in before.keys() & after.keys() if before[k] == after[k])
changed_functions = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
receipt = {'schema': 'tb371fc-ddr-auditor-new-source-derivation-v1',
           'predecessor': {'commit': 'd103750ca2a191559daeb9eff4341b8c80013dc6', 'path': str(BASELINE),
                           'bytes': len(raw), 'sha256': BASELINE_SHA},
           'new_auditor': {'path': str(OUT), 'bytes': len(s.encode()), 'sha256': hashlib.sha256(s.encode()).hexdigest()},
           'original_lost_auditor_sha256': ORIGINAL_LOST_SHA,
           'original_byte_identity_recovered': hashlib.sha256(s.encode()).hexdigest() == ORIGINAL_LOST_SHA,
           'source_pins': {k: {'bytes': v[0], 'sha256': v[1]} for k, v in pins.items()},
           'functions_classes_identical_to_predecessor': same_functions,
           'changed_functions_classes': changed_functions,
           'added_functions': sorted(after.keys() - before.keys()),
           'removed_functions': sorted(before.keys() - after.keys()),
           'diff': {'path': str(ROOT / 'ddr-auditor-recovery-001.diff'), 'bytes': len(diff.encode()),
                    'sha256': hashlib.sha256(diff.encode()).hexdigest()},
           'changes': changes, 'syntax_compiled': True, 'artifact_opened': False,
           'producer_imported_or_executed': False, 'auditor_executed': False,
           'runtime_or_artifact_pass_claimed': False, 'independent_review_required_before_artifact_audit': True}
with (ROOT / 'ddr-auditor-recovery-derivation-001.json').open('x') as f:
    json.dump(receipt, f, sort_keys=True, indent=2); f.write('\n')
print(json.dumps({k: receipt[k] for k in ('new_auditor', 'original_byte_identity_recovered',
                                        'changed_functions_classes', 'added_functions', 'removed_functions')}))
