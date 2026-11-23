# Actual bounded XBL accessor observation

## Measured result

The fixed observer ran in doesvm CT102 / `qemu-tb371fc-dev` from 2026-11-23 15:48:59.494890 UTC to 15:49:03.679741 UTC. The original job exited 0 after all **18 synthetic fixtures and eight self-authored GNU-tool semantic controls passed**. Both phase process groups and every recorded tool process group were reaped. These counts describe parsing and observation controls, not tablet hardware coverage.

The input was the privately retained 3,670,016-byte `xbl_a.bin`, SHA-256 `eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55`. Before/after input hashes and the six installed source hashes matched. No firmware was executed.

The observer inspected the original two seed windows (168 and 96 bytes) and two qualified direct-call targets (512 bytes each), for **1,288 instruction bytes** in total. Static argument witnesses associated the `smem_vers` call at `0x1481b964` with target `0x148528bc`, and the handle candidate at `0x1481b914` with target `0x148526ac`. This is a bounded static observation; it does not establish runtime execution, whole-function extent, pointer validity or reachability from the firmware entry.

## Preserved limitation

The original public output was capped at 12,288 bytes and is preserved byte for byte. It contains 12,049 bytes and reports **267 omitted detail records**. Across the four observed windows there were 60 facts, 66 control-flow edges, 132 frontiers and 27 call candidates (285 records). Its `complete_witnesses_exported` is false. A later lossless paginated projection, if performed, must be a separate result; this original is never silently replaced.

**This attempt does not select a native DAL table or SMEM configuration ABI, construct a native SMEM handoff, prove SMEM initialization, or boot AOP/Android.** Successful execution of the observer is not full TB371FC emulation.

## Files

- `originals/`: exact installation, job, exit, fixture and observer receipts. The observer stdout and observations JSON intentionally reference the same Git blob.
- `method/`: exact approved source package and its six source files. Historical draft-status wording inside the method is retained; actual status comes from the originals and manifest.
- `actual-method-manifest.json`: actual source, input, process, observation and evidence-boundary identities.
- `independent-readback.json`: independent repository readback hashes for every original and the package.

All published observation outputs contain reviewed metadata. Firmware, instruction slices and private disassembly text remain outside this repository. The queued initial read did not cause installation or observation to be repeated; recovery read the existing receipts.
