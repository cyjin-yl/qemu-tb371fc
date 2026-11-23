def verify_core_v3_prefixes(payload):
    """Read both actual architectural headers without importing a test decoder."""
    import struct
    expected = {0: (0x100000, 96, 128, 1), 1: (0x10000, 8, 128, 1)}
    marker = b"\x0aarm-smmuv2"
    observed = {}
    cursor = 0
    while True:
        cursor = payload.find(marker, cursor)
        if cursor < 0:
            break
        header = cursor - 5
        start = cursor + len(marker)
        if header >= 8 and payload[header] in (1, 4):
            instance, version = struct.unpack_from(">II", payload, start)
            if instance in expected and version == 3:
                identity = struct.unpack_from(">IIII", payload, start + 8)
                assert identity == expected[instance], (instance, identity)
                assert instance not in observed, ("duplicate core", instance)
                observed[instance] = {
                    "instance_id": instance, "section_version": version,
                    "section_header_offset": header, "payload_offset": start + 8,
                    "mmio_size": identity[0], "context_banks": identity[1],
                    "smrs": identity[2], "aarch64_granules": identity[3]}
        cursor += len(marker)
    assert set(observed) == set(expected), observed
    return [observed[index] for index in sorted(observed)]

