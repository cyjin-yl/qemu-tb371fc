/* SPDX-License-Identifier: GPL-2.0-only */
/* Independent Linux runtime-PM fixture; only its new synthetic 64 MiB LUN. */
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

#define HOST "/sys/bus/platform/devices/1d84000.ufshc"
#define LUN "/sys/block/sda/device"
#define BYTES 32768
#define FIRST (4 * 1024 * 1024)
#define SECOND (8 * 1024 * 1024)
#define DISK_BYTES (64ULL * 1024 * 1024)
static unsigned checks, failures;
static char device_wlun[PATH_MAX];

static void check(const char *name, int ok)
{
    checks++;
    failures += !ok;
    printf("TB371FC_RPM_CHECK %s %s errno=%d\n", ok ? "PASS" : "FAIL", name, errno);
}

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
        printf("TB371FC_RPM_SYSFS_WRITE %s value=%s open_failed=%d\n", path, value, errno);
        return 0;
    }
    size_t n = strlen(value);
    ssize_t written = write(fd, value, n);
    int saved_errno = errno;
    close(fd);
    printf("TB371FC_RPM_SYSFS_WRITE %s value=%s bytes=%ld errno=%d\n",
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
            printf("TB371FC_RPM_OBSERVE %s %s/%s value=%s readable=%d\n",
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
        printf("TB371FC_RPM_SCSI %s resolved=%s before=%s\n", path, resolved, attr);
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
                printf("TB371FC_RPM_DEVICE_WLUN %s driver=%s\n", path, driver);
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
    printf("TB371FC_RPM_SCSI_CONFIGURED %u\n", configured);
    return ok && configured > 0 && device_wlun[0];
}

static int read_command(int tty, const char *expected)
{
    char data[64] = {0};
    for (size_t i = 0; i + 1 < sizeof(data); i++) {
        ssize_t n;
        do { n = read(tty, data + i, 1); } while (n < 0 && errno == EINTR);
        if (n != 1) {
            return 0;
        }
        if (data[i] == '\n') {
            data[i] = 0;
            return !strcmp(data, expected);
        }
    }
    return 0;
}

static void pattern(unsigned char *p, unsigned mult, unsigned salt)
{
    for (unsigned i = 0; i < BYTES; i++) {
        p[i] = (i * mult + salt) & 255;
    }
}

int main(void)
{
    int fd = -1, tty = -1, logical = 0;
    uint64_t bytes = 0;
    unsigned char *written = NULL, *readbuf = NULL;
    struct termios settings;
    struct stat before, after;
    char driver[PATH_MAX], target[64];
    ssize_t len;
    long first_time, later_time;
    int flags = O_RDWR | O_DIRECT | O_CLOEXEC;

    setvbuf(stdout, NULL, _IONBF, 0);
    check("procfs", mount("proc", "/proc", "proc", 0, NULL) == 0);
    check("sysfs", mount("sysfs", "/sys", "sysfs", 0, NULL) == 0);
    check("devtmpfs", mount("devtmpfs", "/dev", "devtmpfs", 0, NULL) == 0 || errno == EBUSY);
    for (unsigned i = 0; i < 1000; i++) {
        fd = open("/dev/sda", flags);
        if (fd >= 0) { break; }
        delay_ms(20);
    }
    check("ufs_disk_open", fd >= 0);
    if (fd < 0 || failures) { goto done; }
    len = readlink(HOST "/driver", driver, sizeof(driver) - 1);
    if (len >= 0) { driver[len] = 0; }
    check("qcom_ufs_bound", len >= 0 && strstr(driver, "/ufshcd-qcom") != NULL);
    check("block_identity_before", fstat(fd, &before) == 0 && S_ISBLK(before.st_mode));
    check("capacity_64mib_before", ioctl(fd, BLKGETSIZE64, &bytes) == 0 && bytes == DISK_BYTES);
    check("sector_4096_before", ioctl(fd, BLKSSZGET, &logical) == 0 && logical == 4096);
    check("host_control_on", set_attr(HOST, "power/control", "on"));
    check("lun_control_on", set_attr(LUN, "power/control", "on"));
    check("host_active_before", status_is(HOST, "active"));
    check("lun_active_before", status_is(LUN, "active"));
    check("runtime_target_hibern8", read_attr(HOST, "rpm_target_link_state", target, sizeof(target)) && !strcmp(target, "HIBERN8"));
    check("aligned_buffers", posix_memalign((void **)&written, 4096, BYTES) == 0 &&
                             posix_memalign((void **)&readbuf, 4096, BYTES) == 0);
    if (failures) { goto done; }
    pattern(written, 37, 0x61);
    check("write_before_suspend", pwrite(fd, written, BYTES, FIRST) == BYTES);
    check("flush_before_suspend", fsync(fd) == 0);
    memset(readbuf, 0, BYTES);
    check("read_before_suspend", pread(fd, readbuf, BYTES, FIRST) == BYTES && !memcmp(written, readbuf, BYTES));
    tty = open("/dev/ttyMSM0", O_RDWR | O_NOCTTY | O_CLOEXEC);
    int raw = tty >= 0 && tcgetattr(tty, &settings) == 0;
    if (raw) {
        cfmakeraw(&settings);
        raw = tcsetattr(tty, TCSANOW, &settings) == 0;
    }
    check("control_tty", raw);
    if (failures) { goto done; }
    observe("before");
    printf("TB371FC_RPM_STAGE BASELINE\n");
    check("baseline_ack", read_command(tty, "SUSPEND"));
    check("scsi_autosuspend_enabled", configure_scsi());
    check("host_control_auto", set_attr(HOST, "power/control", "auto"));
    if (failures) { goto done; }
    unsigned elapsed;
    for (elapsed = 0; elapsed < 20000; elapsed += 20) {
        if (status_is(HOST, "suspended") && status_is(LUN, "suspended") &&
            status_is(device_wlun, "suspended")) { break; }
        delay_ms(20);
    }
    observe("suspended");
    printf("TB371FC_RPM_IDLE_WAIT_MS %u\n", elapsed);
    check("controller_runtime_suspended", status_is(HOST, "suspended"));
    check("lun_runtime_suspended", status_is(LUN, "suspended"));
    check("device_wlun_runtime_suspended", status_is(device_wlun, "suspended"));
    first_time = suspended_ms();
    delay_ms(120);
    later_time = suspended_ms();
    printf("TB371FC_RPM_SUSPENDED_TIME before=%ld after=%ld\n", first_time, later_time);
    check("controller_stays_suspended", status_is(HOST, "suspended") &&
        first_time >= 0 && later_time >= first_time + 80);
    if (failures) { goto done; }
    printf("TB371FC_RPM_STAGE SUSPENDED\n");
    check("wake_ack", read_command(tty, "READ_WAKE"));
    check("suspended_immediately_before_io", status_is(HOST, "suspended"));
    /* The fd stayed open. No sysfs control=on or host-side state change is
     * issued here: the ordinary block read itself must wake the controller. */
    memset(readbuf, 0, BYTES);
    check("direct_read_wakes_and_preserves_payload", pread(fd, readbuf, BYTES, FIRST) == BYTES && !memcmp(written, readbuf, BYTES));
    check("controller_runtime_active_after_io", status_is(HOST, "active"));
    check("lun_runtime_active_after_io", status_is(LUN, "active"));
    observe("resumed");
    if (failures) { goto done; }
    pattern(written, 53, 0xb7);
    check("write_after_resume", pwrite(fd, written, BYTES, SECOND) == BYTES);
    check("flush_after_resume", fsync(fd) == 0);
    memset(readbuf, 0, BYTES);
    check("read_after_resume", pread(fd, readbuf, BYTES, SECOND) == BYTES && !memcmp(written, readbuf, BYTES));
    check("close_after_resume", close(fd) == 0);
    fd = open("/dev/sda", flags);
    check("reopen_after_resume", fd >= 0);
    if (fd < 0) { goto done; }
    check("same_block_identity", fstat(fd, &after) == 0 && S_ISBLK(after.st_mode) &&
         before.st_rdev == after.st_rdev && before.st_ino == after.st_ino);
    check("capacity_64mib_after", ioctl(fd, BLKGETSIZE64, &bytes) == 0 && bytes == DISK_BYTES);
    pattern(written, 37, 0x61);
    memset(readbuf, 0, BYTES);
    check("reopened_original_payload", pread(fd, readbuf, BYTES, FIRST) == BYTES && !memcmp(written, readbuf, BYTES));
    pattern(written, 53, 0xb7);
    memset(readbuf, 0, BYTES);
    check("reopened_new_payload", pread(fd, readbuf, BYTES, SECOND) == BYTES && !memcmp(written, readbuf, BYTES));
    check("final_close", close(fd) == 0);
    fd = -1;
done:
    if (fd >= 0) { close(fd); }
    if (tty >= 0) { close(tty); }
    free(written);
    free(readbuf);
    printf("TB371FC_RPM_RESULT %s checks=%u failures=%u\n", failures ? "FAIL" : "PASS", checks, failures);
    for (;;) { pause(); }
}
