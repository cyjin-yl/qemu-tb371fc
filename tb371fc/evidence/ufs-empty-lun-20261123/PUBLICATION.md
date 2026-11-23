# Published subset and original runtime inventories

The original JSON reports and logs retain their original bytes. The former
`sha256.json` and `linux-suite/sha256.json` are preserved, without editing,
as `private-source-manifest.json` beside their original locations. Their
original hashes and names are in `manifest-reclassification.json`.

Those inventories describe the original full runtime output, including 15
unpublished fixture entries: generated ELF/object files, PID 1 executables,
initramfs archives and synthetic raw disks across the four Linux gates.
They are provenance inventories of those runs, not promises that every large
runtime artifact is in this Git repository. All inputs to these tests are
public; the word "private" identifies omitted local runtime artifacts, not
private production firmware.

The new `SHA256SUMS` covers every file actually published in this directory,
including both retained inventories. It can be checked from this directory
with `sha256sum -c SHA256SUMS`.
