# GENI test and source-download corrections after 4710

The parent 4710 push board job failed with 101/102 components; its PR merge board job failed with 100/102. The GENI test used a zero clock advance rejected by QTest. The PR source-generation gate also exhausted its three HTTP attempts on a 429 response. These original failures remain recorded.

The five source changes correct the IRQ observation barrier, access-shape expectations for QEMU memory dispatch, the mandatory file migration offset, and the exact unknown-section error text; they also give HTTP 429 responses the existing bounded retry budget and enable verbose GENI source-unit names. Device-model C, the CI harness, the workflow and original boot inputs are unchanged.

The final corrected component test ran against the unchanged, fully hashed original 4710 CI ELF. All 55 checks passed, including two completed migrations, two profile mismatches, 25 single-bit corruptions and three structural corruptions. It verified all 30 stream hashes and 58 process cleanup records. The compiled directory preserves the three earlier component-diagnostic failures and the minimal corrections leading to this result. The source-download directory preserves the actual HTTP failure and the 52 passing local downloader tests.

This component diagnostic is a pre-publication check. A normal push/PR CI run at the new source head and its resulting artifact review are still required before another original-kernel diagnostic. No Android userspace, UI or boot-complete result is claimed. Private storage data and control files are not part of this publication.
