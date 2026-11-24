/*
 * PID 1 for a real Linux UFS checkpoint/continuation gate.
 * SPDX-License-Identifier: GPL-2.0-only
 * The harness creates the only disk: a disposable, initially empty 64 MiB LUN.
 * No reboot is requested. The host ends each QEMU process through QMP.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/fs.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
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

#ifdef TB371FC_RUNTIME_SUSPENDED
/* Same Linux interfaces as the independent runtime-PM fixture. All writes
 * target the disposable QEMU guest; they never reach a physical device. */
#define HOST "/sys/bus/platform/devices/1d84000.ufshc"
#define LUN "/sys/block/sda/device"
static char device_wlun[PATH_MAX];

static void delay_ms(unsigned ms)
{
    struct timespec t = {.tv_sec = ms / 1000, .tv_nsec = (ms % 1000) * 1000000L};
    while (nanosleep(&t, &t) && errno == EINTR) {}
}

static int read_attr(const char *base, const char *attr, char *data, size_t size)
{
    char path[PATH_MAX];
    if (snprintf(path, sizeof(path), "%s/%s", base, attr) >= (int)sizeof(path)) {
        return 0;
    }
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        data[0] = 0;
        return 0;
    }
    ssize_t n = read(fd, data, size - 1);
    close(fd);
    if (n <= 0) {
        data[0] = 0;
        return 0;
    }
    while (n > 0 && (data[n - 1] == '\n' || data[n - 1] == '\r')) {
        n--;
    }
    data[n] = 0;
    return 1;
}

static int set_attr(const char *base, const char *attr, const char *value)
{
    char path[PATH_MAX];
    if (snprintf(path, sizeof(path), "%s/%s", base, attr) >= (int)sizeof(path)) {
        return 0;
    }
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) {
        printf("TB371FC_UFS_MIGRATION_PM_SYSFS_WRITE %s value=%s open_failed=%d\n", path, value, errno);
        return 0;
    }
    size_t n = strlen(value);
    ssize_t written = write(fd, value, n);
    int saved_errno = errno;
    close(fd);
    printf("TB371FC_UFS_MIGRATION_PM_SYSFS_WRITE %s value=%s bytes=%ld errno=%d\n",
           path, value, (long)written, written == (ssize_t)n ? 0 : saved_errno);
    return written == (ssize_t)n;
}

static int status_is(const char *base, const char *want)
{
    char data[64];
    return read_attr(base, "power/runtime_status", data, sizeof(data)) &&
           !strcmp(data, want);
}

static long suspended_ms(void)
{
    char data[64], *end;
    if (!read_attr(HOST, "power/runtime_suspended_time", data, sizeof(data))) {
        return -1;
    }
    long value = strtol(data, &end, 10);
    return *end == 0 ? value : -1;
}

static void observe(const char *phase)
{
    static const char *attrs[] = {"power/control", "power/runtime_status",
        "power/runtime_active_time", "power/runtime_suspended_time"};
    const char *bases[] = {HOST, LUN, device_wlun};
    char data[256];
    for (unsigned b = 0; b < 3; b++) {
        if (!bases[b][0]) { continue; }
        for (unsigned a = 0; a < sizeof(attrs) / sizeof(attrs[0]); a++) {
            int ok = read_attr(bases[b], attrs[a], data, sizeof(data));
            printf("TB371FC_UFS_MIGRATION_PM_OBSERVE %s %s/%s value=%s readable=%d\n",
                   phase, bases[b], attrs[a], data, ok);
        }
    }
}

static int configure_scsi(void)
{
    DIR *dir = opendir("/sys/bus/scsi/devices");
    struct dirent *entry;
    unsigned configured = 0;
    int ok = 1;
    if (!dir) {
        return 0;
    }
    while ((entry = readdir(dir))) {
        char path[PATH_MAX], resolved[PATH_MAX], attr[64];
        if (entry->d_name[0] == '.') {
            continue;
        }
        if (snprintf(path, sizeof(path), "/sys/bus/scsi/devices/%s", entry->d_name)
            >= (int)sizeof(path)) {
            ok = 0;
            break;
        }
        if (!realpath(path, resolved) || !strstr(resolved, "/1d84000.ufshc/")) {
            continue;
        }
        if (!read_attr(path, "power/runtime_status", attr, sizeof(attr))) {
            continue;
        }
        printf("TB371FC_UFS_MIGRATION_PM_SCSI %s resolved=%s before=%s\n", path, resolved, attr);
        unsigned host_id, channel, target_id;
        unsigned long long lun;
        char trailing;
        if (sscanf(entry->d_name, "%u:%u:%u:%llu%c", &host_id, &channel,
                   &target_id, &lun, &trailing) != 4) {
            /* scsi bus also includes hostN/targetH:C:T parents. These are
             * runtime-PM participants but do not use block autosuspend. */
            if (!set_attr(path, "power/control", "auto")) { ok = 0; }
            continue;
        }
        char driverpath[PATH_MAX + 8], driver[PATH_MAX];
        snprintf(driverpath, sizeof(driverpath), "%s/driver", path);
        ssize_t n = readlink(driverpath, driver, sizeof(driver) - 1);
        if (n >= 0) {
            driver[n] = 0;
            const char *last = strrchr(driver, '/');
            if (last && !strcmp(last + 1, "ufs_device_wlun")) {
                strcpy(device_wlun, path);
                printf("TB371FC_UFS_MIGRATION_PM_DEVICE_WLUN %s driver=%s\n", path, driver);
            }
        }
        /* SCSI devices use blk_pm_runtime_init(); the platform host does not
         * use autosuspend_delay_ms, so only its control attribute is changed. */
        if (!set_attr(path, "power/autosuspend_delay_ms", "500") ||
            !set_attr(path, "power/control", "auto")) {
            ok = 0;
        }
        configured++;
    }
    closedir(dir);
    printf("TB371FC_UFS_MIGRATION_PM_SCSI_CONFIGURED %u\n", configured);
    return ok && configured > 0 && device_wlun[0];
}

#endif

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
#ifdef TB371FC_RUNTIME_SUSPENDED
    struct stat source_stat, target_stat;
    char target_state[64];
    long suspended_time = -1;
#endif

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
#ifdef TB371FC_RUNTIME_SUSPENDED
    fd = open("/dev/sda", flags);
    check("pm_source_reopen", fd >= 0);
    if (fd < 0) { goto done; }
    check("pm_source_block_identity", fstat(fd, &source_stat) == 0 &&
          S_ISBLK(source_stat.st_mode));
    check("pm_host_control_on", set_attr(HOST, "power/control", "on"));
    check("pm_lun_control_on", set_attr(LUN, "power/control", "on"));
    check("pm_host_active", status_is(HOST, "active"));
    check("pm_lun_active", status_is(LUN, "active"));
    check("pm_target_hibern8", read_attr(HOST, "rpm_target_link_state",
          target_state, sizeof(target_state)) && !strcmp(target_state, "HIBERN8"));
    observe("before_suspend");
    check("pm_scsi_autosuspend", configure_scsi());
    check("pm_host_control_auto", set_attr(HOST, "power/control", "auto"));
    if (failures) { goto done; }
    unsigned elapsed;
    for (elapsed = 0; elapsed < 20000; elapsed += 20) {
        if (status_is(HOST, "suspended") && status_is(LUN, "suspended") &&
            status_is(device_wlun, "suspended")) { break; }
        delay_ms(20);
    }
    printf("TB371FC_UFS_MIGRATION_PM_IDLE_WAIT_MS %u\n", elapsed);
    observe("source_suspended");
    check("pm_source_host_suspended", status_is(HOST, "suspended"));
    check("pm_source_lun_suspended", status_is(LUN, "suspended"));
    check("pm_source_wlun_suspended", status_is(device_wlun, "suspended"));
    long first_time = suspended_ms();
    delay_ms(120);
    suspended_time = suspended_ms();
    printf("TB371FC_UFS_MIGRATION_PM_SUSPENDED_MS before=%ld checkpoint=%ld\n",
           first_time, suspended_time);
    check("pm_source_stays_suspended", status_is(HOST, "suspended") &&
          first_time >= 0 && suspended_time >= first_time + 80);
    if (failures) { goto done; }
#endif
    /* No disk request remains here. The next command carries no nonce.
     * Runtime-PM mode deliberately keeps the file descriptor open in RAM. */
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
#ifdef TB371FC_RUNTIME_SUSPENDED
    observe("target_before_io");
    check("pm_target_host_suspended", status_is(HOST, "suspended"));
    check("pm_target_lun_suspended", status_is(LUN, "suspended"));
    check("pm_target_wlun_suspended", status_is(device_wlun, "suspended"));
    long restored_time = suspended_ms();
    printf("TB371FC_UFS_MIGRATION_PM_RESTORED_MS checkpoint=%ld target=%ld\n",
           suspended_time, restored_time);
    check("pm_suspended_time_preserved", suspended_time >= 0 &&
          restored_time >= suspended_time);
    check("pm_open_fd_preserved", fstat(fd, &target_stat) == 0 &&
          S_ISBLK(target_stat.st_mode) &&
          source_stat.st_rdev == target_stat.st_rdev &&
          source_stat.st_ino == target_stat.st_ino);
    if (failures) { goto done; }
    /* The existing O_DIRECT read must perform the wake. No control=on,
     * device-state forcing or fresh block open occurs on this receiver. */
    memset(readbuf, 0, TEST_BYTES);
    check("pm_direct_read_wakes", pread(fd, readbuf, TEST_BYTES, SOURCE_OFFSET)
          == TEST_BYTES && !memcmp(readbuf, writebuf, TEST_BYTES));
    check("pm_target_host_active", status_is(HOST, "active"));
    check("pm_target_lun_active", status_is(LUN, "active"));
    observe("target_after_io");
    check("pm_retained_fd_close", close(fd) == 0);
    fd = -1;
    if (failures) { goto done; }
#endif
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

