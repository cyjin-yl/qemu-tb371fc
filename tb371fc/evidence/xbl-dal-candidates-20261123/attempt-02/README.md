# XBL DAL diagnostic attempt 02 — 40 synthetic fixtures, zero accepted candidates

This is the original result of the doesvm CT102 run completed on 2026-11-23 at 14:23:24 UTC. The diagnostic extension preserves the earlier legacy-format rejection and adds bounded numeric context. It does not accept a compact property format or generate a SMEM handoff.

## Actual result

- The synthetic parser suite ran **40 tests and passed**, including the original 34 fixtures and six diagnostic-context fixtures.
- The fixed private XBL input was scanned without execution. The scanner considered 21,481 aligned heads, found 184 geometric candidates, rejected 183 for the device-count bound, and processed one bounded candidate.
- **Accepted candidates: 0.** The remaining rejection is still `property_name_offset`.
- Both child processes exited 0 without timeout or remaining process groups. The firmware input and all six method files have identical before/after hashes.
- The job's `overall_complete: true` means this diagnostic job completed. It does not mean full emulation, a successful SMEM initialization, or a firmware boot.

## Added observation

The one retained failure identifies DALProps head VA `0x14877e10`, property-bin VA `0x14879910`, and the first local entry VA `0x14879fc4`. The legacy decoder reads the words `0x028000e6` and `0x000c0000` as its type and name/id fields. With name-section offset `0x38`, that interpretation produces the out-of-range relative name offset `0x000c0038` within a bin of length `0x724`.

These are observations at a **rejected** entry. The second word is not an accepted SMEM version, and no partition pointer or partition contents have been accepted.

Removing only the three added failure fields and `limits.max_failure_details`, then restoring the v1 schema label and its original sorted compact JSON serialization, reproduces the attempt-01 scan byte for byte: SHA256 `b66ff967fec5f0e8e7ee1fdd0e9b9a5657b733124153b60c4edb839c83d90db6`. The publishing reviewer independently recomputed that projection from the archived original result.

## Files and limits

`manifest.json` preserves the original evidence manifest, including sizes, hashes, paths, source package identity and the five exact original result files. The `method/` directory contains the six installed method files plus the reviewed diff from attempt 01. The previous attempt and all CT stages remain preserved.

The 40 fixtures are authored synthetic format tests. The actual image is a private rooted-tablet backup; its payload is not in this repository. No firmware, private archive, disassembly, certificates, identity data or user data is included. Runtime platform selection, reachability from XBL entry and native SMEM initialization remain unproven.
