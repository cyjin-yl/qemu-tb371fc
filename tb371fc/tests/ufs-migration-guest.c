/*
 * PID 1 for a real Linux UFS checkpoint/continuation gate.
 * SPDX-License-Identifier: GPL-2.0-only
 * The harness creates the only disk: a disposable, initially empty 64 MiB LUN.
 * No reboot is requested. The host ends each QEMU process through QMP.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/fs.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define DISK_BYTES (64ULL * 1024 * 1024)
#define SOURCE_OFFSET (4 * 1024 * 1024)
#define TARGET_OFFSET (8 * 1024 * 1024)
#define TEST_BYTES 32768

static unsigned failures, checks;

static void check(const char *name, int passed)
{
    checks++;
    failures += !passed;
    printf("TB371FC_UFS_MIGRATION_CHECK %s %s\n",
           passed ? "PASS" : "FAIL", name);
    if (!passed) {
        printf("TB371FC_UFS_MIGRATION_ERRNO %s %d\n", name, errno);
    }
}

static int bound(const char *path, const char *driver)
{
    char data[512];
    ssize_t n = readlink(path, data, sizeof(data) - 1);

    if (n < 0) {
        return 0;
    }
    data[n] = 0;
    printf("TB371FC_UFS_MIGRATION_BINDING %s %s\n", path, data);
    const char *name = strrchr(data, '/');
    return name && !strcmp(name + 1, driver);
}

static int dma_domain(void)
{
    char group[512], path[640], mode[32] = {0};
    ssize_t n = readlink("/sys/bus/platform/devices/1d84000.ufshc/iommu_group",
                         group, sizeof(group) - 1);

    if (n < 0) {
        return 0;
    }
    group[n] = 0;
    const char *name = strrchr(group, '/');
    if (!name) {
        return 0;
    }
    snprintf(path, sizeof(path), "/sys/kernel/iommu_groups/%s/type", name + 1);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return 0;
    }
    n = read(fd, mode, sizeof(mode) - 1);
    close(fd);
    printf("TB371FC_UFS_MIGRATION_IOMMU %s %s", path, mode);
    return n == 4 && !memcmp(mode, "DMA\n", 4);
}

static int boot_id(char *id)
{
    char data[64] = {0};
    int fd = open("/proc/sys/kernel/random/boot_id", O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return 0;
    }
    ssize_t n = read(fd, data, sizeof(data) - 1);
    close(fd);
    if (n != 37 || data[36] != '\n') {
        return 0;
    }
    data[36] = 0;
    memcpy(id, data, 37);
    return 1;
}

static uint64_t monotonic_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) {
        return 0;
    }
    return (uint64_t)now.tv_sec * 1000000000 + now.tv_nsec;
}

static int read_line(int fd, char *data, size_t capacity)
{
    for (size_t i = 0; i + 1 < capacity; i++) {
        ssize_t n;
        do {
            n = read(fd, data + i, 1);
        } while (n < 0 && errno == EINTR);
        if (n != 1) {
            return 0;
        }
        if (data[i] == '\n') {
            data[i] = 0;
            return 1;
        }
    }
    return 0;
}

static void pattern(unsigned char *data, unsigned multiplier, unsigned salt)
{
    for (unsigned i = 0; i < TEST_BYTES; i++) {
        data[i] = (i * multiplier + salt) & 0xff;
    }
}

int main(void)
{
    int fd = -1, tty = -1, logical = 0;
    int flags = O_RDWR | O_DIRECT | O_CLOEXEC;
    pid_t saved_pid = getpid();
    uint64_t bytes = 0, saved_time = 0;
    char saved_boot[37] = "unset", current_boot[37] = "unset";
    char nonce[33] = "unset", command[64] = {0};
    unsigned char *writebuf = NULL, *readbuf = NULL;
    struct timespec delay = {.tv_nsec = 20000000};
    struct termios settings;

    setvbuf(stdout, NULL, _IONBF, 0);
    printf("TB371FC_UFS_MIGRATION_BOOT pid=%ld\n", (long)saved_pid);
    check("procfs", mount("proc", "/proc", "proc", 0, NULL) == 0);
    check("sysfs", mount("sysfs", "/sys", "sysfs", 0, NULL) == 0);
    check("devtmpfs", mount("devtmpfs", "/dev", "devtmpfs", 0, NULL) == 0 ||
                     errno == EBUSY);
    check("eight_online_cpus", sysconf(_SC_NPROCESSORS_ONLN) == 8);
    for (unsigned i = 0; i < 1000; i++) {
        fd = open("/dev/sda", flags);
        if (fd >= 0) {
            break;
        }
        nanosleep(&delay, NULL);
    }
    check("qcom_host_driver", bound("/sys/bus/platform/devices/1d84000.ufshc/driver",
                                   "ufshcd-qcom"));
    check("qmp_phy_driver", bound("/sys/bus/platform/devices/1d87000.phy/driver",
                                 "qcom-qmp-ufs-phy"));
    check("gcc_driver", bound("/sys/bus/platform/devices/100000.clock-controller/driver",
                             "gcc-sm8250"));
    check("apps_smmu_driver", bound("/sys/bus/platform/devices/15000000.iommu/driver",
                                   "arm-smmu"));
    check("ufs_translated_dma_domain_before", dma_domain());
    check("ufs_block_discovered", fd >= 0);
    if (fd < 0) {
        goto done;
    }
    check("logical_sector_4096_before", ioctl(fd, BLKSSZGET, &logical) == 0 &&
                                       logical == 4096);
    check("capacity_64_mib_before", ioctl(fd, BLKGETSIZE64, &bytes) == 0 &&
                                   bytes == DISK_BYTES);
    int aligned = posix_memalign((void **)&writebuf, 4096, TEST_BYTES) == 0 &&
                  posix_memalign((void **)&readbuf, 4096, TEST_BYTES) == 0;
    check("aligned_buffers", aligned);
    if (!aligned) {
        goto done;
    }
    pattern(writebuf, 37, 0x61);
    check("source_direct_write",
          pwrite(fd, writebuf, TEST_BYTES, SOURCE_OFFSET) == TEST_BYTES);
    check("source_flush", fsync(fd) == 0);
    memset(readbuf, 0, TEST_BYTES);
    check("source_direct_readback",
          pread(fd, readbuf, TEST_BYTES, SOURCE_OFFSET) == TEST_BYTES &&
          !memcmp(readbuf, writebuf, TEST_BYTES));
    check("source_close", close(fd) == 0);
    fd = -1;
    check("source_boot_id", boot_id(saved_boot));
    saved_time = monotonic_ns();
    check("source_monotonic", saved_time > 0);
    tty = open("/dev/ttyMSM0", O_RDWR | O_NOCTTY | O_CLOEXEC);
    int raw = tty >= 0 && tcgetattr(tty, &settings) == 0;
    if (raw) {
        cfmakeraw(&settings);
        raw = tcsetattr(tty, TCSANOW, &settings) == 0;
    }
    check("control_tty_raw", raw);
    if (failures) {
        goto done;
    }
    printf("TB371FC_UFS_MIGRATION_READY\n");
    int stored = read_line(tty, command, sizeof(command)) &&
                 strlen(command) == 38 && !memcmp(command, "STORE:", 6) &&
                 strspn(command + 6, "0123456789abcdef") == 32;
    if (stored) {
        memcpy(nonce, command + 6, 32);
        nonce[32] = 0;
    }
    check("stored_host_nonce", stored);
    if (failures) {
        goto done;
    }
    /* No disk request remains here. The next command carries no nonce. */
    printf("TB371FC_UFS_MIGRATION_CHECKPOINT nonce=%s boot_id=%s "
           "monotonic_ns=%llu checks=%u failures=%u\n", nonce, saved_boot,
           (unsigned long long)saved_time, checks, failures);
    check("resume_control_received",
          read_line(tty, command, sizeof(command)) && !strcmp(command, "CONTINUE"));
    check("preserved_pid1", saved_pid == 1 && getpid() == saved_pid);
    check("preserved_boot_id", boot_id(current_boot) &&
                              !strcmp(current_boot, saved_boot));
    check("preserved_monotonic", monotonic_ns() >= saved_time);
    check("eight_online_cpus_after", sysconf(_SC_NPROCESSORS_ONLN) == 8);
    check("ufs_translated_dma_domain_after", dma_domain());
    if (failures) {
        goto done;
    }
    fd = open("/dev/sda", flags);
    check("target_open", fd >= 0);
    if (fd < 0) {
        goto done;
    }
    logical = 0;
    bytes = 0;
    check("logical_sector_4096_after", ioctl(fd, BLKSSZGET, &logical) == 0 &&
                                      logical == 4096);
    check("capacity_64_mib_after", ioctl(fd, BLKGETSIZE64, &bytes) == 0 &&
                                  bytes == DISK_BYTES);
    memset(readbuf, 0, TEST_BYTES);
    check("target_reads_source_payload",
          pread(fd, readbuf, TEST_BYTES, SOURCE_OFFSET) == TEST_BYTES &&
          !memcmp(readbuf, writebuf, TEST_BYTES));
    pattern(writebuf, 53, 0xb7);
    check("target_direct_write",
          pwrite(fd, writebuf, TEST_BYTES, TARGET_OFFSET) == TEST_BYTES);
    check("target_flush", fsync(fd) == 0);
    memset(readbuf, 0, TEST_BYTES);
    check("target_direct_readback",
          pread(fd, readbuf, TEST_BYTES, TARGET_OFFSET) == TEST_BYTES &&
          !memcmp(readbuf, writebuf, TEST_BYTES));
    check("target_close", close(fd) == 0);
    fd = open("/dev/sda", flags);
    check("target_reopen", fd >= 0);
    if (fd < 0) {
        goto done;
    }
    pattern(writebuf, 37, 0x61);
    memset(readbuf, 0, TEST_BYTES);
    check("target_reopen_source_payload",
          pread(fd, readbuf, TEST_BYTES, SOURCE_OFFSET) == TEST_BYTES &&
          !memcmp(readbuf, writebuf, TEST_BYTES));
    pattern(writebuf, 53, 0xb7);
    memset(readbuf, 0, TEST_BYTES);
    check("target_reopen_target_payload",
          pread(fd, readbuf, TEST_BYTES, TARGET_OFFSET) == TEST_BYTES &&
          !memcmp(readbuf, writebuf, TEST_BYTES));
    check("target_final_close", close(fd) == 0);
    fd = -1;
done:
    if (fd >= 0) {
        close(fd);
    }
    if (tty >= 0) {
        close(tty);
    }
    free(writebuf);
    free(readbuf);
    printf("TB371FC_UFS_MIGRATION_RESULT %s nonce=%s boot_id=%s "
           "checks=%u failures=%u\n", failures ? "FAIL" : "PASS",
           nonce, current_boot, checks, failures);
    for (;;) {
        pause();
    }
}
