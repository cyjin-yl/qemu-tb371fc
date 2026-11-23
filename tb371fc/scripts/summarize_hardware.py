#!/usr/bin/env python3
"""Join sanitized DT references and observed sysfs driver bindings for review."""
import argparse
import hashlib
import json
from pathlib import Path

ANCHORS = {
    'smmu': ['/soc/apps-smmu@15000000', '/soc/kgsl-smmu@3da0000', '/soc/qcom,kgsl-iommu@3da0000'],
    'gpu_gmu': ['/soc/qcom,kgsl-3d0@3d00000', '/soc/qcom,gmu@3d6a000',
                '/soc/qcom,gpucc@3d90000', '/soc/qcom,kgsl-hyp'],
    'display': ['/soc/qcom,mdss_mdp@ae00000', '/soc/qcom,mdss_dsi_ctrl0@ae94000',
                '/soc/qcom,mdss_dsi_ctrl1@ae96000', '/soc/qcom,dsi-display-primary',
                '/soc/qcom,dsi-display-secondary'],
    'ufs': ['/soc/ufshc@1d84000', '/soc/ufsphy_mem@1d87000', '/soc/ufsice@1d90000'],
    'power_clock_secure_firmware': ['/soc/qcom,gcc@100000', '/soc/rsc@18200000',
        '/soc/rsc@af20000', '/soc/rsc@18200000/qcom,rpmhclk',
        '/soc/qcom,qmp-aop@c300000', '/soc/qseecom@82400000'],
    'usb': ['/soc/ssusb@a600000', '/soc/ssusb@a600000/dwc3@a600000',
            '/soc/ssusb@a800000', '/soc/ssusb@a800000/dwc3@a800000',
            '/soc/hsphy@88e3000', '/soc/ssphy@88e8000'],
    'audio': ['/soc/qcom,lpass@17300000',
              '/soc/qcom,msm-audio-apr/qcom,q6core-audio/sound',
              '/soc/qcom,msm-audio-apr/qcom,q6core-audio/bolero-cdc/wcd938x-codec']
             + [f'/soc/i2c@884000/tfa98xx@{address:x}' for address in range(0x34, 0x38)],
    'input_pen_keyboard': ['/soc/spi@880000/novatek@0', '/soc/i2c@984000/ctn730@28',
                           '/soc/i2c@a94000/mouse@60'],
    'wireless': ['/soc/qcom,cnss-qca6390@b0000000', '/soc/qcom,pcie@1c00000',
                 '/vendor/bt_qca6390'],
}


def summarize(inventory, sysfs):
    if inventory.get('kind') != 'tb371fc-allowlisted-device-dependency-inventory':
        raise ValueError('wrong inventory kind')
    if sysfs.get('kind') != 'tb371fc-allowlisted-sysfs-hardware':
        raise ValueError('wrong sysfs capture kind')
    bindings = {}
    for item in sysfs['dt_driver_bindings']:
        bindings.setdefault(item['dt_node'], []).append(item)
    devices = {}
    for group, paths in ANCHORS.items():
        entries = []
        for path in paths:
            node = inventory['nodes'].get(path)
            if node is None:
                entries.append({'path': path, 'observed_in_dt': False})
                continue
            properties = {key: value.get('strings', value.get('be32', value.get('boolean')))
                          for key, value in node['properties'].items()}
            entries.append({'path': path, 'observed_in_dt': True,
                            'availability': node['availability'],
                            'observed_sysfs_bindings': bindings.get(path, []),
                            'properties': properties, 'dependencies': node['dependencies'],
                            'register_regions': node.get('register_regions', []),
                            'register_decode_error': node.get('register_decode_error'),
                            'unresolved_dependencies': node['unresolved_dependencies']})
        devices[group] = entries
    return {
        'kind': 'tb371fc-selected-hardware-dependency-summary', 'schema_version': 1,
        'devices': devices, 'inventory_counts': inventory['summary'],
        'sysfs_dt_binding_entries': len(sysfs['dt_driver_bindings']),
        'bound_driver_entries': sum(bool(b['bound_driver']) for b in sysfs['dt_driver_bindings']),
        'unresolved_dependencies': inventory['unresolved_dependencies'],
        'limits': [
            'Selected controller anchors; inspect the full inventory for children and other devices',
            'DT availability does not prove driver binding or a functioning physical peripheral',
            'A missing standalone driver binding may mean a parent driver manages the child',
            'A panel reference list does not identify the panel currently selected by the display driver',
            'No MMIO traces, firmware execution, secure-call behavior or hardware equivalence claim',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--sysfs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw_i, raw_s = args.inventory.read_bytes(), args.sysfs.read_bytes()
    result = summarize(json.loads(raw_i), json.loads(raw_s))
    result['source'] = {'inventory_sha256': hashlib.sha256(raw_i).hexdigest(),
                        'sysfs_sha256': hashlib.sha256(raw_s).hexdigest(),
                        'summarizer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')


if __name__ == '__main__':
    main()
