#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
set -eu
src=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
build=${BUILD_DIR:-"$src/build-tb371fc"}
jobs=${JOBS:-4}
mkdir -p "$build"
cd "$build"
exec 9> .tb371fc-build.lock
flock -n 9 || { printf '%s\n' 'Another build owns this directory' >&2; exit 1; }
if [ ! -f build.ninja ]; then
    "$src/configure" \
        --target-list=aarch64-softmmu \
        --with-devices-aarch64=tb371fc \
        --without-default-devices \
        --without-default-features \
        --enable-system --enable-tcg --enable-fdt \
        --disable-docs --disable-tools --disable-guest-agent
fi
ninja -j "$jobs" qemu-system-aarch64
printf '\nBuilt %s/qemu-system-aarch64\n' "$build"
