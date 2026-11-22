/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Tiny PID 1: test the actual Linux kernel/PSCI/timer/initramfs path. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static int failures;

static void check(int ok, const char *name)
{
    printf("TB371FC_CHECK %s %s\n", ok ? "PASS" : "FAIL", name);
    fflush(stdout);
    failures += !ok;
}

static int read_text(const char *path, char *out, size_t size)
{
    int fd = open(path, O_RDONLY);
    ssize_t n;
    if (fd < 0) {
        return -1;
    }
    n = read(fd, out, size - 1);
    close(fd);
    if (n < 0) {
        return -1;
    }
    out[n] = 0;
    return n;
}

int main(void)
{
    char buf[512] = {0};
    struct utsname uts = {0};
    struct timespec before = {0}, after = {0};
    struct timespec delay = {.tv_sec = 0, .tv_nsec = 20000000};
    int status = 0;
    pid_t child;

    setvbuf(stdout, NULL, _IONBF, 0);
    printf("TB371FC_GUEST_BEGIN\n");
    mkdir("/proc", 0755);
    mkdir("/sys", 0755);
    mkdir("/dev", 0755);
    check(mount("proc", "/proc", "proc", 0, NULL) == 0, "procfs");
    check(mount("sysfs", "/sys", "sysfs", 0, NULL) == 0, "sysfs");
    check(uname(&uts) == 0 && !strcmp(uts.machine, "aarch64"), "aarch64");
    printf("TB371FC_KERNEL %s\n", uts.release);
    check(read_text("/sys/devices/system/cpu/online", buf, sizeof(buf)) > 0 &&
          !strcmp(buf, "0-7\n"), "eight_online_cpus_psci");
    printf("TB371FC_CPUS %s", buf);
    clock_gettime(CLOCK_MONOTONIC, &before);
    while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {
    }
    clock_gettime(CLOCK_MONOTONIC, &after);
    check(after.tv_sec > before.tv_sec ||
          (after.tv_sec == before.tv_sec && after.tv_nsec > before.tv_nsec),
          "timer_and_scheduler_progress");
    child = fork();
    if (child == 0) {
        _exit(42);
    }
    check(child > 0 && waitpid(child, &status, 0) == child &&
          WIFEXITED(status) && WEXITSTATUS(status) == 42, "fork_and_wait");
    check(read_text("/proc/device-tree/model", buf, sizeof(buf)) > 0 &&
          strstr(buf, "TB371FC"), "board_model");
    printf("TB371FC_MODEL %s\n", buf);
    child = fork();
    if (child == 0) {
        execl("/arch-probe", "/arch-probe", (char *)NULL);
        _exit(127);
    }
    check(child > 0 && waitpid(child, &status, 0) == child &&
          WIFEXITED(status) && WEXITSTATUS(status) == 0, "arch_counter_probe");
    printf("TB371FC_GUEST_RESULT %s failures=%d\n",
           failures ? "FAIL" : "PASS", failures);
    sync();
    reboot(RB_POWER_OFF);
    for (;;) {
        pause();
    }
}
