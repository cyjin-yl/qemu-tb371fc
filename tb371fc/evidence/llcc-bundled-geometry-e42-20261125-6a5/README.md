# Fixed public input after the e42 LLCC download failure

Both e42 first-attempt push and PR workflows passed 101 of 102 component gates. The only failure was LLCC source generation: its eighth complete source input exhausted three HTTP429 requests with 10s and 20s waits. Subsequent Linux gates were skipped. Both A77 jobs succeeded. No original-kernel diagnostic used these failed workflow artifacts.

The corrected GENI source and compiled gates passed in both archives. The compiled test reports 55 checks, two completed migrations, 30 stream files and 58 cleanups. These are observations of the failed overall workflow, not new-head qualification or Android boot results.

The sole executable-source change makes the LLCC verifier read the already-pinned eighth upstream input from a complete repository copy. Its original BSD-3-Clause notice, full license and exact revision provenance are included. The common length, SHA256 and Git blob checks still apply before generation. The other seven sources and explicit --source-root mode are unchanged. A missing or altered copy fails without a network fallback.

One local regeneration using all eight complete inputs passed and reproduced the two existing outputs byte for byte. Two separate branch checks passed: the exact bundled tuple reaches the generator boundary only after all identity checks; a one-bit corruption is rejected before generation. Those branch checks intentionally do not execute a generator or claim its mocked boundary passed. Their source, actual stderr and execution receipt are included here.

The model, generated tables, CI contracts, workflow and five original-kernel runners are unchanged. A successful normal push/PR CI at the new head, complete artifact review and the existing native admission remain required. No userspace, UI, boot-complete or restore result is claimed.
