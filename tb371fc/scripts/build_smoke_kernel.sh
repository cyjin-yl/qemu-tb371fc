#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Build an independent, unpatched upstream kernel input for QEMU smoke tests.
set -euo pipefail

SMOKE_VERSION=6.12.114
SMOKE_TAR_SHA256=7a8589912bd7a8adb07fed074283652cf482a35facef8954554654f0154b4af3
SMOKE_URL="https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-${SMOKE_VERSION}.tar.xz"
SMOKE_WORK="${1:-/work/clean-linux}"
SMOKE_JOBS="${SMOKE_JOBS:-4}"
SMOKE_CROSS_COMPILE="${SMOKE_CROSS_COMPILE:-aarch64-linux-gnu-}"
SMOKE_REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
SMOKE_FRAGMENT="$SMOKE_REPO/tb371fc/configs/linux-smoke.config"

case "$SMOKE_JOBS" in
    ''|*[!0-9]*|0) printf 'SMOKE_JOBS must be a positive integer\n' >&2; exit 2 ;;
esac

for program in make gcc bc bison flex python3 tar xz sha256sum \
               "${SMOKE_CROSS_COMPILE}gcc" "${SMOKE_CROSS_COMPILE}ld"; do
    command -v "$program" >/dev/null || {
        printf 'Missing build dependency: %s\n' "$program" >&2
        exit 2
    }
done

mkdir -p "$SMOKE_WORK/downloads" "$SMOKE_WORK/source" \
         "$SMOKE_WORK/build" "$SMOKE_WORK/artifacts"
SMOKE_WORK="$(cd -- "$SMOKE_WORK" && pwd)"
SMOKE_TAR="$SMOKE_WORK/downloads/linux-${SMOKE_VERSION}.tar.xz"
SMOKE_SOURCE="$SMOKE_WORK/source/linux-${SMOKE_VERSION}"
SMOKE_BUILD="$SMOKE_WORK/build"
SMOKE_ARTIFACTS="$SMOKE_WORK/artifacts"

if [[ ! -f "$SMOKE_TAR" ]]; then
    command -v curl >/dev/null || {
        printf 'Install curl or provide the pinned tarball at %s\n' "$SMOKE_TAR" >&2
        exit 2
    }
    curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
         --output "$SMOKE_TAR.part" "$SMOKE_URL"
    mv -- "$SMOKE_TAR.part" "$SMOKE_TAR"
fi
printf '%s  %s\n' "$SMOKE_TAR_SHA256" "$SMOKE_TAR" | sha256sum --check -

if [[ ! -d "$SMOKE_SOURCE" ]]; then
    tar --extract --xz --file "$SMOKE_TAR" --directory "$SMOKE_WORK/source"
    printf '%s\n' "$SMOKE_TAR_SHA256" > "$SMOKE_SOURCE/.tb371fc-source-tar-sha256"
elif [[ ! -f "$SMOKE_SOURCE/.tb371fc-source-tar-sha256" ]] ||
     [[ "$(cat "$SMOKE_SOURCE/.tb371fc-source-tar-sha256")" != "$SMOKE_TAR_SHA256" ]]; then
    printf 'Refusing an existing source directory without the pinned extraction receipt: %s\n' \
           "$SMOKE_SOURCE" >&2
    exit 2
fi

# Stable build identity. Toolchain binaries and the resulting config are also
# recorded: identical source alone does not promise identical output bytes.
export KBUILD_BUILD_TIMESTAMP='2026-11-22 00:00:00 +0000'
export KBUILD_BUILD_USER=tb371fc-qemu
export KBUILD_BUILD_HOST=isolated-builder
export KBUILD_BUILD_VERSION=1
export SOURCE_DATE_EPOCH=1795305600

SMOKE_MAKE=(make -C "$SMOKE_SOURCE" O="$SMOKE_BUILD" ARCH=arm64
            "CROSS_COMPILE=$SMOKE_CROSS_COMPILE")
KCONFIG_ALLCONFIG="$SMOKE_FRAGMENT" "${SMOKE_MAKE[@]}" allnoconfig

# Fail immediately if Kconfig discarded a requirement of the actual PID 1
# checks or the console/SMP boot path. Do not treat a small Image as success.
python3 - "$SMOKE_BUILD/.config" <<'PY'
from pathlib import Path
import sys

config = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if line.startswith("CONFIG_") and "=" in line:
        key, value = line.split("=", 1)
        config[key] = value
required = {
    key: "y" for key in (
        "CONFIG_ARM64", "CONFIG_SMP", "CONFIG_ARM64_4K_PAGES",
        "CONFIG_ARCH_QCOM", "CONFIG_OF", "CONFIG_OF_EARLY_FLATTREE",
        "CONFIG_ARM_GIC_V3", "CONFIG_ARM_ARCH_TIMER", "CONFIG_ARM_PSCI_FW",
        "CONFIG_COMMON_CLK", "CONFIG_PM", "CONFIG_PM_OPP",
        "CONFIG_QCOM_GENI_SE", "CONFIG_TTY",
        "CONFIG_SERIAL_QCOM_GENI", "CONFIG_SERIAL_QCOM_GENI_CONSOLE",
        "CONFIG_SERIAL_CORE_CONSOLE", "CONFIG_SERIAL_EARLYCON", "CONFIG_PRINTK",
        "CONFIG_BLK_DEV_INITRD", "CONFIG_RD_GZIP", "CONFIG_BINFMT_ELF",
        "CONFIG_PROC_FS", "CONFIG_SYSFS", "CONFIG_DEVTMPFS", "CONFIG_MULTIUSER",
        "CONFIG_POSIX_TIMERS", "CONFIG_HIGH_RES_TIMERS", "CONFIG_FUTEX",
        "CONFIG_STRICT_KERNEL_RWX", "CONFIG_RODATA_FULL_DEFAULT_ENABLED",
    )
}
required.update(CONFIG_NR_CPUS="8",
                CONFIG_LOCALVERSION='"-tb371fc-qemu-smoke"')
errors = [f"{key}: expected {value}, got {config.get(key, 'disabled')}"
          for key, value in required.items() if config.get(key) != value]
for key in ("CONFIG_MODULES", "CONFIG_DEBUG_INFO_BTF", "CONFIG_LOCALVERSION_AUTO"):
    if config.get(key) in ("y", "m"):
        errors.append(f"{key} must be disabled")
if errors:
    raise SystemExit("Required smoke kernel settings were lost:\n" + "\n".join(errors))
print(f"Verified {len(required)} required kernel settings before compilation")
PY

cp -- "$SMOKE_FRAGMENT" "$SMOKE_ARTIFACTS/linux-smoke.config"
cp -- "$SMOKE_BUILD/.config" "$SMOKE_ARTIFACTS/kernel.config"
printf 'Kernel build command:'
printf ' %q' "${SMOKE_MAKE[@]}" "-j$SMOKE_JOBS" Image
printf '\n'
"${SMOKE_MAKE[@]}" "-j$SMOKE_JOBS" Image
cp -- "$SMOKE_BUILD/arch/arm64/boot/Image" "$SMOKE_ARTIFACTS/Image"

python3 - "$SMOKE_ARTIFACTS" "$SMOKE_TAR" "$SMOKE_URL" \
          "$SMOKE_SOURCE" "$SMOKE_BUILD" "$SMOKE_CROSS_COMPILE" \
          "$SMOKE_JOBS" "$SMOKE_FRAGMENT" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

artifacts, tarball, url, source, build, cross, jobs, fragment = sys.argv[1:]
artifacts = Path(artifacts)

def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def tool(name):
    path = Path(shutil.which(name)).resolve()
    return {"command": name, "path": str(path), "sha256": digest(path),
            "version": subprocess.check_output([name, "--version"], text=True).splitlines()[0]}

base = ["make", "-C", source, f"O={build}", "ARCH=arm64", f"CROSS_COMPILE={cross}"]
report = {
    "schema_version": 1,
    "completed_utc": datetime.now(timezone.utc).isoformat(),
    "scope": "Independent upstream Linux input for QEMU tb371fc smoke; no tablet flash or boot",
    "source_version": "6.12.114",
    "source_url": url,
    "source_tar_sha256": digest(tarball),
    "patches_applied_by_script": [],
    "config_fragment_path": fragment,
    "config_fragment_sha256": digest(artifacts / "linux-smoke.config"),
    "effective_config_sha256": digest(artifacts / "kernel.config"),
    "image_path": str(artifacts / "Image"),
    "image_bytes": (artifacts / "Image").stat().st_size,
    "image_sha256": digest(artifacts / "Image"),
    "cross_compiler": tool(cross + "gcc"),
    "cross_linker": tool(cross + "ld"),
    "host_compiler": tool("gcc"),
    "config_command": base + ["allnoconfig"],
    "config_environment": {"KCONFIG_ALLCONFIG": fragment},
    "build_command": base + [f"-j{jobs}", "Image"],
    "build_environment": {key: os.environ[key] for key in (
        "KBUILD_BUILD_TIMESTAMP", "KBUILD_BUILD_USER", "KBUILD_BUILD_HOST",
        "KBUILD_BUILD_VERSION", "SOURCE_DATE_EPOCH")},
    "build_completed": True,
    "guest_boot_tested_by_this_script": False,
}
(artifacts / "build.json").write_text(json.dumps(report, indent=2) + "\n")
names = ("Image", "linux-smoke.config", "kernel.config", "build.json")
(artifacts / "SHA256SUMS").write_text("".join(
    f"{digest(artifacts / name)}  {name}\n" for name in names))
print(json.dumps(report, indent=2))
PY
