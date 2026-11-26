# GENI HS: CI qualification and one native diagnostic

Both natural workflows for source `70b387fb064cdf2099e85dd89213a5ea7e2c398f` completed successfully on attempt 1. Ten independent read-only artifact audits and three read-only observations each passed on their first execution. A reviewed qualification consumer then passed after one retained consumer-schema failure and a bounded correction. The exact qualified artifact was subsequently used for one recorded original-input native diagnostic. **CI qualification passed; the native result was `kernel-failure`, with a fatal four-byte QCE read at `0x01dfa000`. Android userspace, completed boot and UI remain unestablished.**

This packet preserves the CI qualification as observed at 11:30:02 UTC and the independently reviewed later native attempt as separate records. The former GENI fatal signature is absent from the complete current logs, and actual serial registration and driver-initialization records are present; the observed fatal location is now QCE. The public native review is included byte-for-byte. Preparing this evidence did not trigger another CI or native run, publish a commit, update a PR or contact a device.

## Exact source, CI and artifact identity

| Item | Observed value |
|---|---|
| Source commit | `70b387fb064cdf2099e85dd89213a5ea7e2c398f` |
| Source tree | `37bf696bc687422eac2bd967abda7d80af8e02b9` |
| Ordered source parents | `9e5dddcd0e0ba228821e733a7cf853bbd57dfd7b`, `a49ea65e503e08ec3230eb694c4baa9d3c7a27cb` |
| Immutable CI base | `a49ea65e503e08ec3230eb694c4baa9d3c7a27cb` |
| Actual PR checkout | `252366376d64454bc1abccf874e2e459847c3406`; same tree; parents `[CI base, source]` |
| Push run / board / A77 | `45599002342` / `139152769553` / `139152769048` |
| PR run / board / A77 | `45599016290` / `139152837234` / `139152837634` |
| Artifact | `15016136268`; push run above |
| Full ZIP | 61,592,642 bytes; SHA256 `7692f9c702ae52ef04bf4f795f402ec9cf02e84a8cb7c42c402d7203838cae24` |
| Full QEMU ELF | 63,778,496 bytes; SHA256 `870c5cec547eacc316f87d3f688f0a2e38a25d0bcc3c9d240d72478af76341c3` |
| Original successful qualification | 101,092 bytes; SHA256 `c80a60c82415d16fde7ef974ca6da5d43dc232ec424c3b3ad73ac8576f49da65` |
| Actual successful qualification interval | 2026-11-25 11:29:59.342850 to 11:30:02.035097 UTC |

Public workflows: [push](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45599002342) and [PR](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45599016290). The mutable default-ref observation was `d0f586e9dabade815092f5a4ba9610c1143d76cc`; it remains separate from the immutable CI base. No current default-branch qualification or mutable PR mergeability claim is made.

The source contains the corrected LLCC input selection and a normal two-parent merge of two externally published storage metadata documents. The merge changed no runtime source. The proof recomputes 30 connected Git trees and binds 64 complete source files totaling 1,664,597 bytes; the five native runner source identities remain unchanged. Older source objects are inherited through explicit complete overlays, not described as 64 newly fetched objects.

## One actual original-input native diagnostic

After qualification, this exact artifact was used for one recorded original-input native attempt. The runner invocation lasted from **2026-11-25 11:35:48.685027 to 11:35:55.710199 UTC**. The result was **`kernel-failure`**: the runner, coordinator invocation and host job exited **2**. QEMU exited **0** after acknowledged host cleanup. The persistent artifact-scoped once claim, one recorded dispatch and one invocation/result pair are retained; automatic retry was disabled. This evidence does not make a global claim about unrecorded execution outside the observed attempt.

| Observation | Independently reviewed actual evidence |
|---|---|
| First fatal symbol | `qce_open+0x77c/0x1548`; caller `qcedev_probe+0x164/0x3b8` |
| Fatal access | One invalid 4-byte read at physical `0x01dfa000`; zero invalid-write diagnostics |
| Fatal CPU / ELR / FAR / ESR | CPU6 / `0xffffff80088930f4` / `0xffffff8016f5a000` / `0x96000010` |
| Guest times | First fatal error at 3.156066 s; panic at 3.160089 s |
| Old GENI fault | Zero matches for the former `0x00998068` invalid read and the former `get_se_proto` fatal signature in the complete current logs |
| Positive GENI-related records | `ttyHS0` registered at MMIO `0x00998000` at 2.934628 s; serial-driver initialization recorded at 2.936022 s |
| Full-log Data Aborts | Two; the earlier CPU4 abort returned from the exception before the later fatal QCE abort |
| Storage | Ready absent at selection and postrun; zero original LUNs, block nodes and overlays |
| Android result | Userspace, completed boot and interactive UI were not established |

The two serial records were absent from the previous diagnostic log. Their presence and the absence of the earlier GENI fault are bounded evidence that this recorded attempt reached a later failure location. They do not establish complete GENI probing, native serial transmit/receive, DMA, firmware execution or GIC/guest interrupt handling. The native serial log itself is empty. Earlier DDR invalid-read signatures at `0x0c3f001c` and `0x1c` and `ddr_stats_probe` mentions were also absent; no DDR probe or statistics-payload success is inferred.

The full QEMU log retains an earlier CPU4 Data Abort with `ESR 0x96000005`, `FAR 0` and `ELR 0xffffff800818e10c`, followed by a recorded exception return. Its cause is not classified as benign or fixed. The later CPU6 abort is the fatal QCE access described above. The run is therefore not represented as a single-abort run. This evidence records the fault address, access size and call trace only; it does not assign QCE register semantics or approve a reset value, DMA behavior or crypto implementation.

### Existing failures and warnings remain visible

All **113 prior whole-word `failed` message occurrences remain present** after removal of only the leading printk timestamps, with multiplicity retained and the kernel command line excluded. There are **three additional occurrences**: two PRNG clock-enable failures and one QSEE version-query error `-19`, for **116 total**. A hardware-RNG no-data observation also remains recorded.

WARNING blocks change from **9 to 10**, with **8 shared occurrences**. The earlier QUPV3 master-AHB clock warning is absent and two PRNG-AHB clock warnings are present. WARNING-block counts are not counts of every runtime error. This packet does not claim that unrelated device failures have been resolved.

### Input identity, collection and cleanup

The independent review rehashed all **31 complete collected files / 2,325,721 bytes**. It independently decoded the full 1,048,576-byte printk ring into **1,202 records**, reproducing every byte of the **79,486-byte** collected text log; that text has 1,204 formatted lines. No guest memory was written by the diagnostic capture.

The review crosschecked **8 runtime identities and 7 original-input identities**, including the qualified QEMU, coordinator/configuration, five unchanged runner sources, original kernel, ramdisk, full device tree, command line, AOP data, matching symbols and selected prepared manifest. Runner/coordinator before/after digests and separately collected postrun full-file hash observations agree. The reviewer did not copy and independently rehash the private original input files or runtime ELF locally; the exact receipt and postrun-hash evidence boundary is preserved. The public JSON supplies semantic roles, complete sizes and SHA256 values without exposing those bytes or paths.

The exact recorded command retains the prior 31 global settings in order and adds only `qcom-sm8250-geni-hs.enabled-reference=on`, producing 32 unique globals. The original machine policy and command-line bytes remain unchanged, and no test init was injected. The ready set was absent, so this was explicitly a no-LUN diagnostic. An empty backing set does not establish preservation of attached LUN data, six-LUN boot behavior, complete storage or factory provenance.

The complete QMP sequence has **22 messages**: one greeting, ordered replies 1–16 and five events. Guest watchdog/reset and shutdown events precede the acknowledged host quit at request 16. The final socket reset records errno 104, zero residual bytes and no prior command error. These observations establish orderly host cleanup, while the guest outcome remains a kernel failure. Independent postrun observation found no matching host/container native processes, a dead foreground pane with exit 2, and successful reacquisition of the same shared lock.

The foreground terminal was actually mode **0664**, not 0600. Its inherited default ACL was directly observed; the parent effective access mask and other permissions remained `---`. The four packet files were observed as 0600. These are retained permission observations, with no claim that the reviewer repaired historical files or ACLs.

The exact public review is `native/independent-public-native-review.json`: **29,148 bytes**, SHA256 **`5a741793b1682348db24cfa9e61f46b92c1d2a2c344675144e59b2402002aadb`**. It retains the 31 collected-file identities, input/runtime/host identities, actual chronology, warning comparison and bounded conclusions. The private transport bundle and gzip have recorded hashes; the reviewer explicitly did not rehash those transport bytes. No raw native log, original input, instruction bytes or private control source is added to this public packet.

## Actual successful validation

Both complete board job logs contain exactly **102 component PASS gates and five Linux PASS gates**, with no failed gate. The previous 104 complete normalized contracts and their order are retained; their fingerprint is `2420779f94ded1adff2751ae2424764557a421b25f10784db9c69df4a4e5286a`. The only added contracts are GENI source-unit, source generation and compiled reference-device checks.

The six host unittest suites in each board log have counts **52, 40, 28, 43, 16 and 15**, in source-download / CI harness / Android input / boot runner / block collector / LUN assembly order. The GENI source-unit gate independently records **49 named tests**. Both A77 logs establish **852 guest checks across nine variants**, two rejected invalid configurations and 17 shared PCI/UFS cases. These are bounded raw-log observations; no A77 artifact was downloaded for this review and no CPU workload was rerun.

The ten independent artifact readers cover integration, DDR statistics offset, RPM statistics offset, SPSS, LLCC, DP, DSI, DISPCC, GENI compiled behavior and GENI source generation. Each used the one exact push ZIP, executed once, exited 0 and reported passed. The UFS, board-log and A77-log observers also each executed once and passed. Full execution receipts, stdout and stderr are retained in the two execution documents.

The GENI compiled audit independently checked 55 groups: 23 ordinary/special groups, two completed migrations, two profile rejections, 25 one-bit rejections and three structural rejections. Its ordinary/special object contains 20 ordinary processes and four special processes, because the default-off group exercises both omitted and explicit-off configurations. Full two-device state includes a 16,608-byte HS payload (including its 48-byte guard) and a 16,587-byte legacy payload. Both migrations preserve two 49-getter observations before device MMIO or continue. All 30 complete stream files were checked. The 58 cleanup records comprise 28 acknowledged normal exits and 30 expected rejected-load exits, with no forced termination or kill.

The separate GENI source audit reads all 19 public inputs (810,000 bytes), reconstructs the two reference originals from complete retained mutants, verifies both generated outputs (26,355 bytes), and independently reconstructs all 51 retained negative files (1,700,682 bytes). It checks the 49 verbose unit names, nine current source identities, 12 constants and 84 complete archive members without importing or executing the generator or verifier. Public parsing and explicit reference policy remain distinct from sanitized private-producer facts; withheld private binary bytes are not made publicly reproducible by their hashes.

The main qualifier independently reread the union of eight complete member inventories: **1,692 unique members / 305,829,940 uncompressed bytes**. The inventories contain 549 integration, 135 DDR, 129 RPM, 143 SPSS, 213 LLCC, 63 DISPCC, 464 GENI compiled and 84 GENI source entries, with overlap counted once. DP and DSI retain their separately scoped raw-evidence receipts. This is the verified-member union, not a claim that every ZIP entry was independently reread by the main qualifier. QEMU was never executed during qualification.

The extra Linux UFS observations retain 39 active-migration checks (21 source + 18 target) and 61 runtime-suspended-migration checks (34 + 27), including raw QMP and serial correlations. Their excluded disk images and full UFS migration files were not available in the public ZIP. Full-media comparison and UFS migration hashes remain host receipts, not an independent reread of absent bytes. No system-suspend or in-flight-UFS migration result is claimed.

## Retained first qualifier failure and correction

Main attempt 001 ran at 11:25:01 UTC, exited 1 and wrote no qualification. It incorrectly required the independent compiled reader's `ordinary_and_special` object to be a 23-element list. The original stderr is 1,341 bytes / SHA256 `0614a86d26dec8866f7e7bbf4c2cb60ad0cf8ebe263f29cd83154d4d984e6e9c`; the full original execution receipt is 1,430 bytes / SHA256 `bfe2a43c7e682619144323b4393c4333d89ac60141e3fc2b825f7a57b881cbd3`.

A versioned source changes only that consumer block. It strictly validates nine fixed fields, 23 groups / 24 processes, all 20 ordered ordinary names and fixtures, all four ordered special fixtures, and the five exact scope booleans. Four booleans are true; `gic_or_guest_interrupt_handling_verified` remains false. The other 19 functions and all nonfunction AST are unchanged. The actual binding, all ten artifact readers, all three observers and their results remain unchanged. No audit was rerun to obtain a passing consumer result.

An independent static review also compared all remaining consumer interfaces with the actual receipts: CI metadata, observers, older model audits, retained failures, source chain and member collections. It found no further mismatch. The reviewed main attempt 002 then actually exited 0 and produced the qualification above. The failed 001 record remains a failure; there was no automatic retry.

## Earlier failures remain historical

The qualification retains the actual B5 push 100/102 failures, the 4710 push 101/102 and PR 100/102 failures, and the e42 push/PR 101/102 LLCC HTTP-429 failures. Previous bounded component-diagnostic failures and their later passing 55-group diagnostic remain historical observations of the earlier ELF. Failed overall workflows are not relabeled as passes or used as qualification for this source. The 9e5 workflows were superseded by normal CI concurrency before the current normal base merge; no success is inferred for their canceled board jobs.

Earlier public records remain at the [B5 corrective publication](https://github.com/cyjin-yl/qemu-tb371fc/tree/4710b80bde965798dfe5f0c9684c5b5880b84d04/tb371fc/evidence/geni-hs-b5-ci-corrections-20261125-6a5), [4710 corrective publication](https://github.com/cyjin-yl/qemu-tb371fc/tree/e42c0a5934d36a1dc31de4ba30275672a919665a/tb371fc/evidence/geni-hs-4710-ci-corrections-20261125-6a5), and [e42 LLCC correction](https://github.com/cyjin-yl/qemu-tb371fc/tree/9e5dddcd0e0ba228821e733a7cf853bbd57dfd7b/tb371fc/evidence/llcc-bundled-geometry-e42-20261125-6a5).

## Public copies, provenance and limits

The four `logs/` files preserve the original complete public GitHub job-log UTF-8 bytes, including BOMs and newlines. Standard public GitHub runner paths in those logs are retained. No private native log is selected. JSON documents are explicitly wrapped public derivatives: all original JSON values remain, except local workstation locators become deterministic aliases. Original byte counts, SHA256 and Git blob identities are recorded separately from public-copy identities. The `reviewed-source/*.py.txt` files are complete reader/qualifier source text with the same locator substitutions; they are archival review material, not a relocated runnable environment. Assertions and numeric contracts are not rewritten.

`fullhash-provenance.json` describes each selected original and public copy, including complete execution source identities and an alias map without original local paths. `manifest.json` pins all content files except itself; its own identity belongs in the publication receipt. Source selection is explicit, and credential/private-locator/signed-URL scans run before staging. No private kernel, ramdisk, full device tree, command line, serial identity, native control packet, raw native log, credential, signed artifact URL or binary artifact bytes are included.

GENI remains an explicit default-off strict-FIFO reference profile. This evidence does not establish physical SE6 firmware selection or reset state, firmware execution, DMA transfers, a Bluetooth peer, measured FIFO geometry, GIC routing or guest interrupt handling, DDR/RPM statistics payloads, original Android userspace, UI or boot completion. The qualification remains a CI result as of 11:30:02 UTC; its native-not-executed fields describe that earlier observation boundary. The separate native review records the subsequent failed diagnostic and does not convert CI success into Android success.
