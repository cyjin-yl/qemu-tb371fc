import gzip
import hashlib
import io
import json
import tarfile


ARCHIVE_SIZE = 2849209
ARCHIVE_SHA = "30a30d8670bd69579d2a250ddcc542b99f3140d062a8b95cb61c09bfd8f1671e"
CAPTURE_SHA = "610c04eb3ef5dc5599721a778d6fba8b1804e154f20672b11dd5a60fb6d75f0a"
CAPTURE_SOURCE_SHA = "8d17a0820cd36f00fb3897367a8e2c99c40ce3a944aa6c9c604875d121ed1e24"
EXPECTED = {
    "xbl_a.bin": {
        "size": 3670016,
        "sha256": "eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55",
        "name": "xbl_a", "path": "/dev/block/by-name/xbl_a",
        "real": "/dev/block/sdb1", "major": 8, "minor": 17,
    },
    "xbl_config_a.bin": {
        "size": 131072,
        "sha256": "bb16d00d0f7581caa641d2f1c736adf5cc6505403c67250c47e8a893cbda93ec",
        "name": "xbl_config_a", "path": "/dev/block/by-name/xbl_config_a",
        "real": "/dev/block/sdb2", "major": 8, "minor": 18,
    },
}

MAX_TAR_BYTES = 4 * 1024 * 1024
MAX_CAPTURE_BYTES = 64 * 1024


class CompletionError(Exception):
    pass

def require(condition, code):
    if not condition:
        raise CompletionError(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_capture_json_key")
        result[key] = value
    return result

def reject_json_constant(_value):
    raise CompletionError("non_finite_capture_json_number")

def verify_capture_json(data):
    require(sha(data) == CAPTURE_SHA, "capture_json_hash_mismatch")
    document = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
                          parse_constant=reject_json_constant)
    require(type(document) is dict, "capture_json_not_object")
    require(document.get("schema") == "tb371fc-private-boot-firmware-capture-v1",
            "capture_schema_mismatch")
    require(document.get("capture_source_sha256") == CAPTURE_SOURCE_SHA,
            "capture_source_hash_mismatch")
    properties = {"ro.product.model": "TB371FC", "ro.boot.slot_suffix": "_a"}
    require(document.get("properties_before") == properties and
            document.get("properties_after") == properties, "capture_model_or_slot_mismatch")
    entries = document.get("entries")
    require(type(entries) is list and len(entries) == 2, "capture_entry_count_mismatch")
    seen = set()
    for entry in entries:
        require(type(entry) is dict, "capture_entry_not_object")
        member = entry.get("member")
        require(type(member) is str and member in EXPECTED and member not in seen,
                "capture_member_not_exact_unique_allowlist")
        seen.add(member)
        expected = EXPECTED[member]
        for key in ("name", "path", "real", "major", "minor", "size", "sha256"):
            require(type(entry.get(key)) is type(expected[key]) and
                    entry.get(key) == expected[key], "capture_entry_field_mismatch")
        require(type(entry.get("captured_bytes")) is int and
                entry["captured_bytes"] == expected["size"], "capture_byte_count_mismatch")
        require(entry.get("verification_read_sha256") == expected["sha256"] and
                entry.get("two_read_passes_equal") is True, "capture_stability_hash_mismatch")
    require(seen == set(EXPECTED), "capture_entries_incomplete")
    scope = document.get("scope")
    require(type(scope) is dict and scope.get("read_passes") == 2 and
            scope.get("device_unique_identifiers_read") is False and
            scope.get("userdata_read") is False and
            scope.get("arbitrary_ram_read") is False and
            scope.get("device_configuration_changed") is False,
            "capture_scope_mismatch")
    return document

def validate_archive(archive):
    require(len(archive) == ARCHIVE_SIZE and sha(archive) == ARCHIVE_SHA,
            "whole_archive_size_or_hash_mismatch")
    with gzip.GzipFile(fileobj=io.BytesIO(archive), mode="rb") as compressed:
        unpacked = compressed.read(MAX_TAR_BYTES + 1)
    require(len(unpacked) <= MAX_TAR_BYTES, "decompressed_tar_exceeds_bound")
    require(len(unpacked) % 512 == 0, "unaligned_tar_length")
    contents = {}
    required = set(EXPECTED) | {"capture.json"}
    physical_cursor = 0
    with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as tf:
        members = tf.getmembers()
        require(len(members) == 3 and not tf.pax_headers, "tar_member_count_or_global_pax")
        for member in members:
            require(type(member.name) is str and member.name in required and
                    member.name not in contents, "tar_member_not_exact_unique_allowlist")
            require(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and
                    member.isreg() and not member.linkname and
                    not member.pax_headers and not member.sparse, "tar_non_regular_or_link_member")
            require(member.offset == physical_cursor and
                    member.offset_data == member.offset + 512,
                    "tar_hidden_or_noncontiguous_header")
            require(unpacked[member.offset + 257:member.offset + 265] == b"ustar\x0000",
                    "tar_not_expected_ustar_format")
            require(type(member.size) is int and member.size >= 0, "tar_bad_size")
            if member.name in EXPECTED:
                require(member.size == EXPECTED[member.name]["size"], "tar_payload_size_mismatch")
            else:
                require(0 < member.size <= MAX_CAPTURE_BYTES, "capture_json_size_out_of_bounds")
            physical_cursor = member.offset_data + ((member.size + 511) // 512) * 512
            require(physical_cursor <= len(unpacked), "tar_member_outside_archive")
            require(not any(unpacked[member.offset_data + member.size:physical_cursor]),
                    "nonzero_member_padding")
            stream = tf.extractfile(member)
            require(stream is not None, "regular_member_stream_missing")
            with stream:
                payload = stream.read(member.size + 1)
            require(len(payload) == member.size, "tar_member_short_or_oversized")
            expected_sha = CAPTURE_SHA if member.name == "capture.json" else EXPECTED[member.name]["sha256"]
            require(sha(payload) == expected_sha, "tar_member_hash_mismatch")
            contents[member.name] = payload
    require(set(contents) == required, "tar_members_incomplete")
    require(len(unpacked) - physical_cursor >= 1024 and
            not any(unpacked[physical_cursor:]), "tar_nonzero_trailing_content_or_missing_end")
    capture = verify_capture_json(contents["capture.json"])
    return contents, capture, len(unpacked)

