# GENI HS CI failure and compiled-component corrections

**Container diagnostic 004 passed all 55 checks, including 2 completed migrations, with all 58 cleanup records verified.** This result uses the corrected test and helper with the exact failed-CI QEMU ELF. The original 4710 CI failure and three intervening diagnostic failures remain part of this evidence. A passing natural CI run of the new source head is still required for qualification.

## Actual sequence

| Stage | Actual outcome | Correction following the failure |
| --- | --- | --- |
| Push run 45575456246, artifact 15005831019 | Ordinary00 passed. Ordinary01, `exact_original_cold_probe_and_dma_masks`, failed at `clock_step 0` with `FAIL could not advance time`. Both QEMU instances exited normally. | Replace the zero-progress IRQ fence with the read-only `endianness` query. |
| Diagnostic 001 | 13 checks passed. Ordinary13 failed because a 1-byte FW_MAIN read reported `invalid size (min:4 max:4)`, while the test expected `rejected`. All 14 cleanup records show exit0 and quit acknowledgement. | Require exact invalid-size fragments and reflect QTest buffer splitting. |
| Diagnostic 002 | 23 checks passed. The first outgoing `migrate` request was rejected because `channels[0].addr.offset` was missing. No migration completed. All 25 cleanup records show exit0 and quit acknowledgement. | Add integer `offset: 0` to the shared file-channel helper. |
| Diagnostic 003 | 53 checks passed and 2 migrations completed. The unknown-section target exited1 with the correct QEMU rejection, but the test expected the obsolete phrase `Unknown savevm section or instance`. The backend observer finish was not reached, so this whole case was not counted as passed. Cleanup: 28 normal exits and 29 expected rejection exits. | Remove only the obsolete word `savevm` from that diagnostic needle. |
| Diagnostic 004 | All 55 checks passed: 23 ordinary/special groups, 2 completed migrations, 2 profile rejections, 25 one-bit rejections and 3 structural rejections. The producer verified hashes for 30 actual stream files. Cleanup: 28 exit0/quit-ack records and 30 expected exit1 records, with no forced termination or kill. | No further correction required by this run. |

The final suite ran from `2026-11-25T09:58:03.468441+00:00` to `2026-11-25T10:00:07.561849+00:00`, took 124.093428008 seconds, and was actually waited for with exit0. Its complete result is 5,237,252 bytes, SHA-256 `1dff944bc84ad42acaa635b47ef1bacf0641e8dbed3f764c95d8bc82fb6ffff3`.

## What changed

Four bounded corrections affect two component-testing files. The IRQ helper still checks pure QOM state and actual IRQ output, and still rejects protocol errors and EOF. Its nine synthetic checks cover queued raise/lower messages, order, unchanged-low state and five rejection paths. Positive time steps and the separate exact-target `clock_set` calls remain unchanged.

QTest scalar spellings use a buffer interface. Misaligned 4-byte requests arrive as exact ordered 1/2-byte fragments. A 5-byte read preserves its accepted 4-byte revision prefix and rejects the final byte. An 8-byte request is delivered as two 4-byte accesses; the corrected test checks the exact revision pair, two read-only write refusals and unchanged full state. **An unsplit 8-byte device callback was not observed.** Fourteen synthetic shape checks and a separate replay of the actual first failure were retained.

The migration-address helper now supplies the exact-head QAPI's required zero offset. Three pure address constructions and all three positive/negative call sites were checked. The final text correction changes one string literal only, while preserving target exit, specific-failure, backend observation and cleanup assertions. The last unsupported-version needle was also checked against the pinned source.

The device C model, board source and header remain unchanged. The transport helper was unchanged for the first two corrections, then changed only for the required migration offset. The final test is 70,882 bytes, SHA-256 `01802bf20b9e9e5a68c32aa32bdb6bde48d733694f8d9559d4dc7db161a259b5`; the final helper is 20,061 bytes, SHA-256 `319e8049aa82a4e2be7b956e3e7dc2de9b48477dfdd5cc5442805980cefeed11`. All 55 group names remain. The companion source-download evidence covers the other three source-file changes; the overall correction set has five source changes, with C, the CI harness and workflow unchanged.

## Provenance and limits

All four diagnostics used the same 63,783,936-byte ELF, SHA-256 `404f621540f640ea8f7331d080cd826152ee76e77e27d7f1efceeb76f0862d00`, from source `4710b80bde965798dfe5f0c9684c5b5880b84d04`, tree `afc2fe21894f38803e6280f49465a0daba7413c6`. The ELF was unchanged before and after every run.

The byte-exact original clock-zero receipt is preserved. The source history separates all four corrections, their exact source identities and their existing pure validations. The combined diff contains all four original deltas in order; each segment's byte offset and complete hash is recorded. The diagnostic history preserves all actual results and full identities of the underlying receipts and selected raw evidence.

Early failures occurred before final report aggregation. Their separate cleanup records support the stated exit counts. Diagnostic003's correct unknown-section rejection did not establish its unfinished backend-observer result; diagnostic004 includes the completed raw observations for that case and the unsupported-version case, with zero backend output, no cont and no memory command.

The final producer reports full hashes for all 30 actual migration files. This bounded publication pass rehashed complete result files and retained raw evidence; it did not independently re-read the migration binary files. Full artifact auditing remains a later qualification step. There was no native kernel or private boot input, and no Android boot, physical serial-engine capture or guest interrupt-handling claim. Raw private paths, command lines, URLs and binary contents are excluded.
