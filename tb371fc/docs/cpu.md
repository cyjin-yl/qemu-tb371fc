# Cortex-A77 architectural CPU model

The fork adds `cortex-a77` in
[target/arm/tcg/cpu64.c](../../target/arm/tcg/cpu64.c), with an independently
audited A77 architectural configuration. QEMU already implements the required
instruction families in its shared ARM translators. A76 and A77 legitimately
share many ID-register values; this model does not invent ISA differences.
It sets the correct A77 revision and PMU identity and exposes documented
cache and cryptographic configurations.

This CPU model does not reproduce the physical pipeline, cache latency,
speculation, power behavior or undocumented Qualcomm controls. Its addition
does not complete the whole-tablet emulator.

## Tablet evidence

The read-only capture on 2026-11-23 reports:

| Physical cores | MIDR_EL1 | REVIDR_EL1 |
| --- | --- | --- |
| CPU0-3, Qualcomm Kryo | `0x51df805e` | `0x1f` |
| CPU4-7, Cortex-A77 r1p0 | `0x411fd0d0` | `0x3` |

The live tree identifies all cores as `qcom,kryo`, with MPIDRs `0x000`
through `0x700` in steps of `0x100`. Both sysfs and the tree show private
L1 instruction/data and L2 caches, plus shared L3. Neither source exposes
capacities, line sizes or associativity on this tablet. The capture confirms
presence and sharing, not cache geometry. See the
[expansion inventory](../evidence/expansion-20261123/).

The [fresh stock-Android sysfs capture](../evidence/stock-cpu-cache-20261123/result.json)
at `2026-11-23T20:45:01.137991+00:00` repeats this observation on
`4.19.157-perf+`: all eight CPUs expose L1 Data, L1 Instruction, private
L2 Unified and L3 Unified shared by CPUs `0-7`. All 128 requested geometry
fields (size, sets, associativity and line size for 32 entries) are absent
with `FileNotFoundError`; no numeric geometry is inferred. The exact
[read-only collector and file hashes](../evidence/stock-cpu-cache-20261123/source-binding.json)
are retained beside the receipt.

| CPUs | MIDR from sysfs | Scheduler `cpu_capacity` | Sysfs package ID |
| --- | --- | --- | --- |
| 0-3 | `0x51df805e` | `313` | `0` |
| 4-6 | `0x411fd0d0` | `777` | `1` |
| 7 | `0x411fd0d0` | `1024` | `2` |

These are the running kernel's software topology and capacity declarations.
As described by the [Linux capacity documentation](https://docs.kernel.org/scheduler/sched-capacity.html),
capacity is a normalized scheduler quantity. This capture runs no performance
benchmark and does not turn the package IDs into a claim about physical
packages. It also does not validate a replacement Linux kernel on the tablet.

The observed Linux feature line is:

~~~text
fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm lrcpc dcpop asimddp
~~~

Linux's exported feature view is not a complete raw per-core EL1 ID-register
dump. The small-core Qualcomm implementation still requires separate work;
an A77 model does not resolve its differences.

## Architectural definition

The primary reference is Arm
[101111_0101_05, Cortex-A77 Core TRM](https://documentation-service.arm.com/static/64ac62b4df6cd61d528c60a0),
sections 13.4, 13.23-24, 13.63-88, 13.93, 15.23, 16.1 and 25.4.

| Area | Model configuration |
| --- | --- |
| Revision | r1p0; `MIDR_EL1=0x411fd0d0` |
| Execution states | AArch64 EL0-EL3; AArch32 EL0 |
| Addressing | 40-bit physical address, 16-bit ASID, 4/16/64 KiB granules |
| Selected ISA | Armv8.2-A plus RCpc, dot product and SSBS |
| Absent ISA | SVE, PAuth, SHA512, FHM, RNG, BF16 |
| L1 | 64 KiB instruction and data, 4 ways, 64-byte lines |
| L2 | 128/256/512 KiB, 8 ways, 64-byte lines |
| Optional L3 | 512 KiB or 1/2/4 MiB, 16 ways, 64-byte lines |
| PMU | Six event counters; identity `0x41103000` |
| GIC CPU interface | Four virtual list registers, five priority/preemption bits |

The public manual describes r1p1 and records PBHA as an r1p1 addition. The
model uses the observed r1p0 identity and does not introduce r1p1 PBHA
implementation-specific registers. PMU IDCODE is `0x10`, not a value
inferred from the MIDR part number.

Properties are set before realization:

| Property | Default | Allowed settings |
| --- | --- | --- |
| `revidr` | `0` | 32-bit ECO value; board can set observed `3` |
| `l2-cache-size` | `512K` | `128K`, `256K`, `512K` |
| `l3-cache-size` | `0` | `0`, `512K`, `1M`, `2M`, `4M` |
| `crypto` | `on` | `on` or `off` |

Default cache geometry is a reference-core configuration, **not a tablet
measurement**. In particular, default L3 is absent although the tablet has
shared L3 of unmeasured capacity. Cache properties describe register-visible
geometry; they do not implement cache timing or DSU behavior.

`crypto=off` removes AES/PMULL/SHA1/SHA256 from both AArch64 and AArch32
feature views and causes their instructions to be undefined. CRC, LSE, RDM
and dot product remain. This follows
[101113_0101_02, A77 Cryptographic Extension TRM](https://documentation-service.arm.com/static/5e859c28c10d1d3c5b705373),
sections 2.2, 2.4 and 2.5.

Shared TCG realization suppresses unimplemented trace, AMU, SPE and MPAM
advertisements. For example, the test expects `ID_DFR0_EL1=0x04000088`
after removing memory-mapped tracing. Such reductions are deliberate;
identical hardware ID registers are not claimed. The GIC field depends
on the controller attached by the machine.

## Actual execution result

[Final receipt](../evidence/cpu-a77-r1p0/result.json):

- Started **2026-11-23T05:29:19.967494+00:00**.
- QEMU SHA-256: `b9ac99b7ade997207f3d72a9380000af9b33a59049c05b00a969d03f5724376f`.
- **Nine configurations passed**, with 94/95/96 guest checks each,
  **852 guest checks total**, plus two invalid cache configurations rejected.
- Overall exit status **0**.

The receipt contains exact compiler, binary, CPU/test-source hashes,
commands, per-check results and raw-log hashes. The guest executes at EL3
with real exception vectors and a 4 KiB-granule Normal-memory stage-1 mapping.

| Checked behavior | Guest execution |
| --- | --- |
| IDs | Privileged MRS reads of AArch64/AArch32 IDs, MIDR, REVIDR, cache geometry and PMCR |
| Integer/atomic | LDADD, matching/mismatching CAS, LDAPR, CRC32/CRC32C |
| FP/SIMD | FP16 arithmetic, SDOT/UDOT, RDM |
| Crypto | Checked AES rounds, PMULL, SHA1 rotate and SHA256 schedule values |
| Cache operations | Exact 64-byte DC ZVA range and neighboring guards; DC CVAP decode |
| PMU | Six software-increment counters and Secure EL3 HPMN/HPME partitions |
| Negative ISA | SVE, non-HINT PAuth, SHA512, FHM, RNG, BF16 take actual Undefined Instruction exceptions |
| Properties | Crypto-off instructions fault; each documented L2/L3 size yields correct geometry |

The configurations are default, observed REVIDR, two alternate L2 sizes,
four L3 sizes and crypto disabled. The isolated `-M none -m 4M` harness
does not replace the separate eight-CPU, 8 GiB Linux board gate. It is not
a differential execution run on the physical tablet.

## PMU bug found and fixed

The probe initially observed zero instead of an incremented Secure EL3
event counter. Shared `cpregs-pmu.c` used `arm_mdcr_el2_eff()`, which
zeroed HPMN/HPME when EL2 was disabled in the current Security state.
Architectural counter partitioning still uses those fields in that state:
[MDCR_EL2](https://support.arm.com/documentation/ddi0595/2021-09/AArch64-Registers/MDCR-EL2--Monitor-Debug-Configuration-Register--EL2-?lang=en),
[PMCR_EL0](https://support.arm.com/documentation/ddi0601/latest/AArch64-Registers/PMCR-EL0--Performance-Monitors-Control-Register).

[target/arm/cpregs-pmu.c](../../target/arm/cpregs-pmu.c) now uses the
implemented MDCR_EL2 value for that decision. The regression checks all
six counters, then enables counters 0-2 using PMCR.E while HPME disables
3-5, then reverses the controls. It stays in Secure EL3.

The original failing
[log](../evidence/cpu-a77-r1p0/pmu-secure-before-fix.log) and
[receipt](../evidence/cpu-a77-r1p0/pmu-secure-before-fix.json)
are retained beside the successful results.

## Reproduce in the development container

~~~sh
mkdir -p /work/build-a77
cd /work/build-a77
/src/configure \
    --target-list=aarch64-softmmu \
    --with-devices-aarch64=a77-test \
    --without-default-devices --without-default-features \
    --enable-system --enable-tcg --enable-fdt \
    --disable-docs --disable-tools --disable-guest-agent
ninja -j 2 qemu-system-aarch64

python3 /src/tb371fc/tests/test_a77_cpu.py \
    --qemu /work/build-a77/qemu-system-aarch64 \
    --output /work/a77/new-results
~~~

The runner needs an AArch64 cross compiler and `CONFIG_GENERIC_LOADER`.
It refuses to overwrite a receipt and retains guest output on timeout.
It never accesses the tablet.

## Remaining CPU verification

Actual cache geometry and the complete raw per-core feature-register set
remain unmeasured. Setting `REVIDR=3` reproduces identity, not every
silicon ECO or erratum.

AArch32 feature registers are checked; AArch32 instruction execution and
all exception-level transitions are not exhaustively tested here.
Physical timing, speculative behavior, branch predictors, Qualcomm small-core
details, DSU coherency, power/clock coupling, implementation-specific
controls, external debug/trace and hardware PMU event accuracy remain outside
the demonstrated scope. The probe also does not exhaust the full ISA,
memory model or MMU configuration space.
