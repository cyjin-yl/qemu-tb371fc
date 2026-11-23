# CT attempt 04: source fetch failed before build

The doesvm CT102 job ran from 13:52:25 to 13:56:15 UTC on 2026-11-23. It initialized a new repository and attempted to fetch the fixed commit `fe89e5a553fe038babe07abb279a0975d4306715`. Git exited 128 with `Send failure: Broken pipe`. The controller then rejected the remaining process group.

**No checkout, compilation, XML test, CPU preflight or private firmware execution occurred in this attempt.** The public CI success for the same commit remains a separate result.

The three original result/log files and exact executed controller are retained here with hashes in [manifest.json](manifest.json). The failed directory is preserved. The original flags record that the group remained after the cleanup signal; they have not been rewritten.

A separate read-only observation at 14:26:20 UTC found the original leader absent and a single `git` process in state `Z`, reparented to PID 1. Its recorded start time matches the failed fetch. That observation sent no signals. A zombie is not an actively running fetch, but the observation does not prove the earlier cleanup succeeded.

The next attempt uses a new directory, the preserved fixed parent, and the exact public Git delta. It must reproduce the same Git tree and commit and pass the unchanged source, XML and fourteen-case CPU gates. Its result must be assessed separately.
