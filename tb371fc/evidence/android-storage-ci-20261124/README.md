# Android storage metadata fixture validation

`local-combined/` preserves the exact result and test log from executing the
workflow run step in the combined local tree. The receipt records that local
commit, five source SHA-256 values, Python version and actual per-suite counts.
It passed 42 GPT tests and 23 super tests with zero skips, failures, errors,
expected failures or unexpected successes.

The code was then published as a combined Git tree; the local commit identifier
is provenance for this run, not a claim that the later publication commit
had already executed. The GitHub workflow records its own actual checkout and
source hashes. Match those values and the job conclusion before accepting it.

These are public synthetic fixtures. Real metadata captures and their limited
qualifications are preserved separately in `../ufs-gpt-inventory-20261124/` and
`../android-super-metadata-20261124/`. No fixture result proves original partition
payload validity, coherent full storage acquisition, AVB, snapshot state or
Android boot.
