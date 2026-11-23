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
#include "guest-result.h"

volatile struct tb371fc_guest_result tb371fc_guest_result = {
    .magic = TB371FC_RESULT_MAGIC,
    .version = TB371FC_RESULT_VERSION,
    .structure_size = sizeof(struct tb371fc_guest_result),
};
static int failures;
static unsigned check_index;

/*
 * The debugger may stop here after all results are in ordinary guest RAM.
 * This function has no guest side effects and also works without a debugger.
 * noipa keeps a fixed callable symbol even in the optimized static ELF.
 */
__attribute__((noinline, noclone, noipa, used,
               section(".tb371fc_checkpoint")))
void tb371fc_guest_checkpoint(void)
{
    __asm__ volatile("" ::: "memory");
}

static void store_text(volatile char *destination, size_t capacity,
                       const char *source)
{
    size_t i;
    for (i = 0; i + 1 < capacity && source[i]; ++i) {
        destination[i] = source[i];
    }
    destination[i] = 0;
}

static uint64_t monotonic_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
        return 0;
    }
    return (uint64_t)now.tv_sec * 1000000000 + now.tv_nsec;
}

static void check(int ok, const char *name)
{
    uint64_t bit = UINT64_C(1) << check_index++;
    tb371fc_guest_result.checks_completed |= bit;
    if (ok) {
        tb371fc_guest_result.pass_mask |= bit;
    } else {
        tb371fc_guest_result.fail_mask |= bit;
    }
    printf("TB371FC_CHECK %s %s\n", ok ? "PASS" : "FAIL", name);
    fflush(stdout);
    failures += !ok;
    tb371fc_guest_result.failures = failures;
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

static int run_arch_probe(void)
{
    int pipes[2], status = -1, read_ok = 1;
    pid_t child, waited;
    char chunk[256];
    ssize_t count;

    tb371fc_guest_result.counter_probe_wait_status = -1;
    if (pipe(pipes) != 0) {
        return 0;
    }
    child = fork();
    if (child == 0) {
        close(pipes[0]);
        /*
         * Without a kernel console, fd 1 may be available and pipe() can
         * allocate it. Do not close it after dup2(1, 1).
         */
        if (pipes[1] != STDOUT_FILENO) {
            if (dup2(pipes[1], STDOUT_FILENO) < 0) {
                _exit(126);
            }
            close(pipes[1]);
        }
        execl("/arch-probe", "/arch-probe", (char *)NULL);
        _exit(127);
    }
    close(pipes[1]);
    if (child < 0) {
        close(pipes[0]);
        return 0;
    }
    for (;;) {
        count = read(pipes[0], chunk, sizeof(chunk));
        if (count < 0 && errno == EINTR) {
            continue;
        }
        if (count <= 0) {
            read_ok = count == 0;
            break;
        }
        /* Preserve exact stdout bytes on the console when it is available. */
        (void)fwrite(chunk, 1, (size_t)count, stdout);
        for (ssize_t i = 0; i < count; ++i) {
            uint64_t offset = tb371fc_guest_result.counter_probe_bytes++;
            if (offset + 1 < TB371FC_RESULT_PROBE_CAPACITY) {
                tb371fc_guest_result.probe_stdout[offset] = chunk[i];
            } else {
                tb371fc_guest_result.probe_truncated = 1;
            }
        }
    }
    close(pipes[0]);
    do {
        waited = waitpid(child, &status, 0);
    } while (waited < 0 && errno == EINTR);
    tb371fc_guest_result.counter_probe_wait_status = status;
    return waited == child && WIFEXITED(status) && WEXITSTATUS(status) == 0 &&
           read_ok && !tb371fc_guest_result.probe_truncated &&
           tb371fc_guest_result.counter_probe_bytes > 0;
}

int main(void)
{
    char buf[512] = {0};
    struct utsname uts = {0};
    struct timespec before = {0}, after = {0};
    struct timespec delay = {.tv_sec = 0, .tv_nsec = 20000000};
    int status = 0;
    int before_result, sleep_result, after_result;
    long long elapsed_ns;
    pid_t child;

    tb371fc_guest_result.start_monotonic_ns = monotonic_ns();
    setvbuf(stdout, NULL, _IONBF, 0);
    printf("TB371FC_GUEST_BEGIN\n");
    mkdir("/proc", 0755);
    mkdir("/sys", 0755);
    mkdir("/dev", 0755);
    check(mount("proc", "/proc", "proc", 0, NULL) == 0, "procfs");
    check(mount("sysfs", "/sys", "sysfs", 0, NULL) == 0, "sysfs");
    check(uname(&uts) == 0 && !strcmp(uts.machine, "aarch64"), "aarch64");
    store_text(tb371fc_guest_result.uname_sysname, 128, uts.sysname);
    store_text(tb371fc_guest_result.uname_nodename, 128, uts.nodename);
    store_text(tb371fc_guest_result.uname_release, 128, uts.release);
    store_text(tb371fc_guest_result.uname_version, 128, uts.version);
    store_text(tb371fc_guest_result.uname_machine, 128, uts.machine);
    store_text(tb371fc_guest_result.uname_domainname, 128, uts.domainname);
    printf("TB371FC_KERNEL %s\n", uts.release);
    check(read_text("/sys/devices/system/cpu/online", buf, sizeof(buf)) > 0 &&
          !strcmp(buf, "0-7\n"), "eight_online_cpus_psci");
    store_text(tb371fc_guest_result.online, 64, buf);
    printf("TB371FC_CPUS %s", buf);
    before_result = clock_gettime(CLOCK_MONOTONIC, &before);
    do {
        sleep_result = nanosleep(&delay, &delay);
    } while (sleep_result != 0 && errno == EINTR);
    after_result = clock_gettime(CLOCK_MONOTONIC, &after);
    elapsed_ns = (after.tv_sec - before.tv_sec) * 1000000000LL +
                 after.tv_nsec - before.tv_nsec;
    tb371fc_guest_result.elapsed_ns = elapsed_ns;
    check(before_result == 0 && sleep_result == 0 && after_result == 0 &&
          elapsed_ns >= 20000000LL, "timer_and_scheduler_progress");
    child = fork();
    if (child == 0) {
        _exit(42);
    }
    check(child > 0 && waitpid(child, &status, 0) == child &&
          WIFEXITED(status) && WEXITSTATUS(status) == 42, "fork_and_wait");
    memset(buf, 0, sizeof(buf));
    check(read_text("/proc/device-tree/model", buf, sizeof(buf)) > 0 &&
          strstr(buf, "TB371FC"), "board_model");
    store_text(tb371fc_guest_result.model, 128, buf);
    printf("TB371FC_MODEL %s\n", buf);
    check(run_arch_probe(), "arch_counter_probe");
    printf("TB371FC_GUEST_RESULT %s failures=%d\n",
           failures ? "FAIL" : "PASS", failures);
    tb371fc_guest_result.finish_monotonic_ns = monotonic_ns();
    __sync_synchronize();
    tb371fc_guest_result.completed = 1;
    tb371fc_guest_checkpoint();
    sync();
    reboot(RB_POWER_OFF);
    for (;;) {
        pause();
    }
}
