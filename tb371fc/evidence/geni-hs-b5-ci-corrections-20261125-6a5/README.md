# Corrections after the first GENI CI failure

The source parent is `b5bf00086640cbee8ef0c13375ba281cd73b8369`.
Its push board job failed with 100/102 components passing. GCC source generation
rejected its existing content-pinned tag URL at cache admission, and the GENI
compiled suite stopped at an incorrect QOM metadata type label. Both A77 jobs
passed; both board jobs failed. The later Linux gates did not execute. The B5
archive remains a failure and has not been run against the original kernel.

The downloader now sends cache-ineligible URLs through its original uncached
transport and unchanged caller content checks. Only immutable 40-hex raw GitHub
URLs can enter the cache. Cache integrity, permissions and atomic-publication
errors still fail closed. The 49 local downloader tests include the real GCC
verifier rejecting a wrong complete tag response, preserving transient retries,
and proving that tag responses neither enter nor disturb the immutable cache.

One GENI test line changes the metadata label `str` to `string` for three
properties. The exact B5 QOM implementation and actual failed-run response both
establish `string`. Python getter value checks remain unchanged. The original
failure records replay the corrected 32 metadata checks and 98 getters; this is
not a compiled rerun and says nothing about the later ordinary or migration
groups. The device model, generator, CI harness and workflow are unchanged.

The original source-only review receipts remain unchanged, including the
reviews that missed these issues. This directory adds separate correction and
independent-review records. Public copies of receipt path fields use relative
labels; the root observation records both the original receipt hashes and the
published hashes. Full raw local stdout with workspace paths is not selected.

A normal new-head push/PR CI run is required before qualification and any new
original-kernel diagnostic. No Android userspace, UI or boot-complete result is
claimed. The storage continuation is maintained separately.
