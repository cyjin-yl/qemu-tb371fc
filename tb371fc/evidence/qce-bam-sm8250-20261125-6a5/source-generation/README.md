# QCE/BAM source-generation evidence

This records local Python generation of the reviewed virtual cold-control
profile and its complete-source/parser validation. It does not claim compiled
QEMU, native probe, cryptographic, DMA, firmware or Android boot success.

The first generator and verifier failures, original source versions, finite
diffs and complete actual result records are retained. See `history.json` for
the precise correction scope. `validation-final.json` is the actual second
verifier execution: two checks, 12 complete public inputs, two exact outputs,
127 rejected negative cases and two valid parser fixtures.

The full negative fixture files remain in the actual verification output
directory and are recreated by the frozen verifier during formal CI. Production
source pins are never rewritten or accepted from those test fixtures.

The 12 complete source files and primary vendor licence notices are shipped
under `tb371fc/sources/qce-bam-sm8250/`. No private firmware, kernel bytes,
assembly, native logs, host credentials, or original boot arguments are included.
