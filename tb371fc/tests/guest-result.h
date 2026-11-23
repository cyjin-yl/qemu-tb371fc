/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef TB371FC_GUEST_RESULT_H
#define TB371FC_GUEST_RESULT_H
#include <stddef.h>
#include <stdint.h>

/* Little-endian bytes "TBFCRES1". This is guest RAM, not a device protocol. */
#define TB371FC_RESULT_MAGIC UINT64_C(0x3153455243464254)
#define TB371FC_RESULT_VERSION 1
#define TB371FC_RESULT_CHECKS 8
#define TB371FC_RESULT_PROBE_CAPACITY 2048

struct tb371fc_guest_result {
    uint64_t magic;
    uint64_t version;
    uint64_t structure_size;
    uint64_t completed;
    uint64_t checks_completed;
    uint64_t pass_mask;
    uint64_t fail_mask;
    uint64_t failures;
    int64_t elapsed_ns;
    uint64_t counter_probe_bytes;
    int64_t counter_probe_wait_status;
    uint64_t probe_truncated;
    uint64_t start_monotonic_ns;
    uint64_t finish_monotonic_ns;
    char uname_sysname[128];
    char uname_nodename[128];
    char uname_release[128];
    char uname_version[128];
    char uname_machine[128];
    char uname_domainname[128];
    char online[64];
    char model[128];
    char probe_stdout[TB371FC_RESULT_PROBE_CAPACITY];
};

_Static_assert(offsetof(struct tb371fc_guest_result, uname_sysname) == 112,
               "result ABI header changed");
_Static_assert(offsetof(struct tb371fc_guest_result, probe_stdout) == 1072,
               "result ABI strings changed");
_Static_assert(sizeof(struct tb371fc_guest_result) == 3120,
               "result ABI size changed");
#endif
