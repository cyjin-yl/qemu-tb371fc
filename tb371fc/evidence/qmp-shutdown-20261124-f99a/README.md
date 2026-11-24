# QMP shutdown qualification: host validation

This bundle preserves seven actual local test attempts from 2026-11-24. The tests use real local AF_UNIX sockets with synthetic process state and synthetic Android eligibility inputs. **No VM or Android boot was executed by these attempts.** No device RPC was used. These results establish host-side runner behavior only.

## Recorded attempts

| Attempt | Scope | Tests reported | Exit | Observed result |
| --- | --- | ---: | ---: | --- |
| 001 | Initial socket regression subset | 9 | 0 | Pass |
| 002 | Initial complete test file | 25 | 0 | Pass |
| 003 | Paused reply followed by `RESUME` in the same read, before its fix | 1 | 1 | Actual red: one assertion failure |
| 004 | Capture callback catches an earlier QMP command error, before its fix | 1 | 1 | Actual red: one assertion failure |
| 005 | Pause, retained command-error and cleanup regressions after fixes | 3 | 0 | Pass |
| 006 | Final standalone QMP fix, complete test file | 28 | 0 | Pass |
| 007 | Combined VIDEO/early-stop and QMP fix, complete test file | 31 | 0 | Pass |

Attempts 003 and 004 contain ordinary unittest `FAILED (failures=1)` results and exit code 1. Their receipt flag `expected_failure_before_fix` records why those counterexamples were run; it does not convert them into unittest expected failures or passes. All original receipts, stdout and stderr are preserved byte for byte. Seven empty stdout logs are included. Earlier successful subsets are not substituted for the final source checks.

## Qualification covered

The frozen tests cover complete quit tails followed by EOF or an actual controlled `ECONNRESET`; missing, mismatched, malformed or duplicate ACKs; partial final frames; contradictory state events; a reset before quit; nonzero or forced process exit; an open socket that never reaches closure; and a frame that crosses the byte bound in its final fragment. Later regressions retain an earlier QMP command failure caught by the printk callback and reject a state change received with the final pause reply.

The socket reset fixture leaves a request delimiter unread to produce a real Linux AF_UNIX reset. It exercises the runner's classification; it does not establish the underlying cause of any earlier QEMU run's reset. Android completion fields used by the unit tests are synthetic eligibility fixtures. Historical run003 remains `kernel-failure` and is not rewritten by this bundle.

## Source identities and combination

The standalone source identity is local commit `f717649ea860dcdbb30da48dc2d74e4db5e40d66`, tree `374cde1b1df3ba3dddfc98ee19f8854182d270e2`; attempt 006 binds its source/test digests.

The combined source identity is local merge `d13df9784f3e0170c9ce66e40f5dac0469f9b39f`, tree `ca666a37698e4671475973c50923977583d7ec89`. Its parents are the standalone fix and fixed VIDEO commit `4cc9dee07f542697c129b60d3ff5162aa0f71a53`. Attempt 007 binds the tested tree and final merge identity. These are recorded local Git identities; this evidence makes no CI or publication claim for them.

Attempt 007's preservation record compares source-tree entries and early-stop code. At that source merge, only `tb371fc/scripts/boot_android.py` and `tb371fc/tests/test_boot_android.py` differ from the fixed VIDEO tree. The early-stop signature, snapshot loop, CLI/reporting behavior and three original early-stop test bodies are preserved. The test fixture additionally checks that final shutdown is qualified normally while kernel failures retain their rejection.

At the recorded combined source version, the complete host test command is:

```sh
python3 tb371fc/tests/test_boot_android.py -v
```

## Bundle integrity

`index.json` lists the 23 original files with byte lengths and SHA-256 digests, records the local source bindings, and hashes this README. The two generated files are this README and the index itself. No test was rerun while packaging; no source or test file was modified. The duplicated intermediate patch and the earlier six-attempt local index are omitted. Raw device payloads, original boot arguments and credentials are not included; retained traceback paths identify only the local test workspace.
