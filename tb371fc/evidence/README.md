# TB371FC passive ARM platform evidence

The reference was captured from the owner's running **Lenovo TB371FC** through
the existing Android root shell at **2026-11-22 21:54:30 UTC** (2026-11-23 in
Asia/Shanghai). It is a sequential configuration snapshot, not a successful
QEMU boot, an MMIO behavioral trace, or a hardware performance measurement.

`evidence/android-board.json` retains allowed DT property bytes as base64,
SHA-256 hashes, big-endian cell interpretations, absent/read-error status and
CPU identification registers already exposed by sysfs. The acquisition script
was executed through standard input without writing a file to the tablet.
`evidence/board-profile.json` is derived from those bytes by the validator.

## Important observations

| Item | Observed configuration |
| --- | --- |
| Product / platform / SoC | TB371FC / kona / SM8250; SoC ID 356 |
| CPU affinity | MPIDR 0x0, 0x100, …, 0x700; DT compatible `qcom,kryo` |
| CPU MIDR | CPUs 0–3: `0x51df805e`; CPUs 4–7: `0x411fd0d0` |
| Memory | `0x80000000 + 0x3bb00000`, `0xc0000000 + 0x1c0000000` |
| GIC distributor | `0x17a00000 + 0x10000` |
| GIC redistributors | `0x17a60000 + 0x100000`, stride `0x20000` |
| Architectural counter | 19,200,000 Hz in the running DT |
| Enabled console node | `qcom,qup_uart@a90000`, range `0xa90000 + 0x4000`, GIC SPI 357 |
| Other UART | `@988000` with SPI 603 is disabled; `@998000` is enabled and has mixed-controller `interrupts-extended` |
| PSCI | `arm,psci-1.0`, `smc` conduit |
| Chosen stdout path | Absent |

The memory tuples describe the OS-visible DT banks. Their combined size is
8 GiB minus 69 MiB. They do not prove that the omitted region is physically
unpopulated, nor enumerate every reserved-memory carveout. The parent `/soc`
uses 1 address cell and 1 size cell while the root uses 2 and 2; CPU `reg`
uses 2 address cells and 0 size cells. Decoding everything as 64-bit register
pairs would silently produce the wrong MMIO map.

The enabled Android console differs from the often-used upstream `uart2`
address. QEMU may intentionally expose a documented development console, but
it must distinguish that choice from the observed tablet configuration.

## Reproduce and check

The scripts need only Python's standard library. In an existing authorized
Android root shell:

```sh
python3 scripts/collect_board.py > android-board.json
```

For a source-attributed capture, compute the exact script's SHA-256 on the
development host and supply `--source-sha256 DIGEST`. The JSON labels this an
operator-supplied source digest; it is not a signed or remote attestation.

On the development host:

```sh
python3 scripts/board_profile.py evidence/android-board.json
python3 scripts/test_board_profile.py
```

The validator verifies the bytes and hashes, DT cell widths, identity bus
translation, UART interrupt parent, unique CPU affinities and non-overlapping
memory regions. It refuses unknown captured property/identity fields.
Mixed-controller `interrupts-extended` remains explicit raw phandle tuples
rather than being incorrectly decoded as a sequence of GIC triples.

## Collection limits

The collector reads only explicit node and property allowlists. It excludes
boot arguments, serial numbers, MAC addresses, credentials, firmware binaries,
raw input streams, debugfs and raw registers. Names of omitted DT properties
are recorded without their values or hashes. It performs no writes, reboots,
driver binding changes, tracing, charging changes or frequency-policy changes.
It does not infer a working keyboard, pen, display, GPU, Wi-Fi or power state
from configuration data alone.
