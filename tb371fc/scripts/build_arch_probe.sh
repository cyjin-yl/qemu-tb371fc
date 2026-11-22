#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Keep the source, object and ELF for reproducibility; never installs to a device.
set -eu
tb371fc_probe_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tb371fc_probe_output=${1:-"$tb371fc_probe_root/build/arch-probe"}
tb371fc_probe_prefix=${TB371FC_CROSS_PREFIX:-aarch64-linux-gnu-}
mkdir -p -- "$(dirname -- "$tb371fc_probe_output")"
"${tb371fc_probe_prefix}as" -march=armv8-a \
  -o "$tb371fc_probe_output.o" "$tb371fc_probe_root/tests/arch-probe.S"
"${tb371fc_probe_prefix}ld" -static --build-id=none -z noexecstack -e _start \
  -o "$tb371fc_probe_output" "$tb371fc_probe_output.o"
sha256sum "$tb371fc_probe_root/tests/arch-probe.S" "$tb371fc_probe_output"
