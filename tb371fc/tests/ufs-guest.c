/*
 * PID 1 for the synthetic UFS disk driver gate.
 * SPDX-License-Identifier: GPL-2.0-only
 * Only the harness-created 64 MiB empty disk is used. The host test ends
 * QEMU after reading the guest verdict; this program requests no reboot.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/fs.h>
#include <scsi/sg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define DISK_BYTES (64ULL * 1024 * 1024)
#define TEST_OFFSET (4 * 1024 * 1024)
#define TEST_BYTES 32768
static unsigned failures, checks;

static void check(const char *name, int passed)
{
    checks++;
    failures += !passed;
    printf("TB371FC_UFS_CHECK %s %s\n", passed ? "PASS" : "FAIL", name);
    fflush(stdout);
}
static int bound(const char *path, const char *driver)
{
    char data[512];
    ssize_t n = readlink(path, data, sizeof(data) - 1);
    if (n < 0) {
        return 0;
    }
    data[n] = 0;
    printf("TB371FC_UFS_BINDING %s %s\n", path, data);
    return strstr(data, driver) != NULL;
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
    printf("TB371FC_UFS_IOMMU %s %s", path, mode);
    return n == 4 && !memcmp(mode, "DMA\n", 4);
}
static int scsi(int fd, unsigned char *cmd, unsigned len, void *data,
                 unsigned bytes, unsigned char *sense, unsigned *status)
{
    sg_io_hdr_t h = {
        .interface_id = 'S',
        .dxfer_direction = bytes ? SG_DXFER_FROM_DEV : SG_DXFER_NONE,
        .cmd_len = len, .mx_sb_len = 32, .dxfer_len = bytes,
        .dxferp = data, .cmdp = cmd, .sbp = sense, .timeout = 5000,
    };
    int ret = ioctl(fd, SG_IO, &h);
    *status = h.status;
    printf("TB371FC_UFS_SCSI opcode=%02x ioctl=%d status=%u host=%u driver=%u resid=%d\n",
           cmd[0], ret, h.status, h.host_status, h.driver_status, h.resid);
    return ret == 0 && h.host_status == 0;
}

#ifdef TB371FC_REQUIRE_RPMH
static int rpmh_xo_rate(void)
{
    unsigned long long rate = 0;
    FILE *stream;

    if (mount("debugfs", "/sys/kernel/debug", "debugfs", 0, NULL) != 0 &&
        errno != EBUSY) {
        return 0;
    }
    /* clk-rpmh uses "bi_tcxo" as its public clock name; div2 is only
     * part of the C object identifier, not the debugfs directory name. */
    stream = fopen("/sys/kernel/debug/clk/bi_tcxo/clk_rate", "r");
    if (!stream) {
        return 0;
    }
    int count = fscanf(stream, "%llu", &rate);
    fclose(stream);
    printf("TB371FC_RPMH_XO_RATE %llu\n", rate);
    return count == 1 && rate == 19200000;
}

static int rpmh_completion_irq(void)
{
    FILE *stream = fopen("/proc/interrupts", "r");
    char line[2048];
    unsigned long long total = 0;
    unsigned columns = 0;

    if (!stream) {
        return 0;
    }
    while (fgets(line, sizeof(line), stream)) {
        if (!strstr(line, "18200000")) {
            continue;
        }
        char *text = strchr(line, ':');
        if (!text) {
            continue;
        }
        text++;
        for (columns = 0; columns < 8; columns++) {
            char *end;
            unsigned long long value = strtoull(text, &end, 10);
            if (end == text) {
                break;
            }
            total += value;
            text = end;
        }
        printf("TB371FC_RPMH_IRQ %s", line);
        break;
    }
    fclose(stream);
    return columns == 8 && total > 0;
}
#endif

int main(void)
{
    int fd = -1, logical = 0;
    uint64_t bytes = 0;
    unsigned char *writebuf = NULL, *readbuf = NULL;
    unsigned char sense[32] = {0}, capacity[8] = {0};
    unsigned char capcmd[10] = {0x25}, badcmd[16] = {0xff};
    unsigned status = 0;
    struct timespec delay = {.tv_nsec = 20000000};
    int flags = O_RDWR | O_DIRECT | O_CLOEXEC;

    setvbuf(stdout, NULL, _IONBF, 0);
    check("procfs", mount("proc", "/proc", "proc", 0, NULL) == 0);
    check("sysfs", mount("sysfs", "/sys", "sysfs", 0, NULL) == 0);
    check("devtmpfs", mount("devtmpfs", "/dev", "devtmpfs", 0, NULL) == 0 ||
                     errno == EBUSY);
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
    check("kgsl_smmu_driver", bound("/sys/bus/platform/devices/3da0000.iommu/driver",
                                    "arm-smmu"));
    check("ufs_translated_dma_domain", dma_domain());
#ifdef TB371FC_REQUIRE_RPMH
    check("rpmh_rsc_driver",
          bound("/sys/bus/platform/devices/18200000.rsc/driver", "/rpmh"));
    check("rpmh_clock_driver",
          bound("/sys/bus/platform/devices/18200000.rsc:clock-controller/driver",
                "/clk-rpmh"));
    check("rpmh_xo_rate_19200000", rpmh_xo_rate());
    check("rpmh_completion_interrupt", rpmh_completion_irq());
#endif

    check("ufs_block_discovered", fd >= 0);
    if (fd < 0) {
        goto done;
    }
    check("logical_sector_4096", ioctl(fd, BLKSSZGET, &logical) == 0 &&
                                   logical == 4096);
    check("capacity_64_mib", ioctl(fd, BLKGETSIZE64, &bytes) == 0 &&
                                bytes == DISK_BYTES);
    if (posix_memalign((void **)&writebuf, 4096, TEST_BYTES) ||
        posix_memalign((void **)&readbuf, 4096, TEST_BYTES)) {
        check("aligned_buffers", 0);
        goto done;
    }
    for (unsigned i = 0; i < TEST_BYTES; i++) {
        writebuf[i] = (i * 37 + 0x61) & 0xff;
    }
    memset(readbuf, 0, TEST_BYTES);
    check("direct_multiblock_write",
          pwrite(fd, writebuf, TEST_BYTES, TEST_OFFSET) == TEST_BYTES);
    check("cache_flush", fsync(fd) == 0);
    check("direct_multiblock_read",
          pread(fd, readbuf, TEST_BYTES, TEST_OFFSET) == TEST_BYTES &&
          !memcmp(readbuf, writebuf, TEST_BYTES));
    close(fd);
    fd = open("/dev/sda", flags);
    check("close_reopen_persistence", fd >= 0 &&
          pread(fd, readbuf, TEST_BYTES, TEST_OFFSET) == TEST_BYTES &&
          !memcmp(readbuf, writebuf, TEST_BYTES));
    if (fd < 0) {
        goto done;
    }
    check("scsi_read_capacity", scsi(fd, capcmd, sizeof(capcmd), capacity,
                                    sizeof(capacity), sense, &status) &&
          status == 0 && capacity[2] == 0x3f && capacity[3] == 0xff &&
          capacity[4] == 0 && capacity[5] == 0 &&
          capacity[6] == 0x10 && capacity[7] == 0);
    memset(sense, 0, sizeof(sense));
    check("unsupported_scsi_rejected", scsi(fd, badcmd, sizeof(badcmd),
                                           NULL, 0, sense, &status) &&
          status == 2 && (sense[2] & 0xf) == 5 && sense[12] == 0x20);
    check("read_beyond_disk_is_eof",
          pread(fd, readbuf, 4096, DISK_BYTES) == 0);
done:
    if (fd >= 0) {
        close(fd);
    }
    free(writebuf);
    free(readbuf);
    sync();
    printf("TB371FC_UFS_RESULT %s checks=%u failures=%u\n",
           failures ? "FAIL" : "PASS", checks, failures);
    fflush(stdout);
    for (;;) {
        pause();
    }
}
