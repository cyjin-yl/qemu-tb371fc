#!/usr/bin/env python3
"""Start the prepared CT job in a new window of the existing host session."""
import json
import re
import shlex
import subprocess
import sys

SESSION = "qemu-tb371fc"
WINDOW = "smem-observed-slot-20261123-01"
STAGE = "/work/smem-observed-slot-20261123-01"

def call(argv, timeout=5):
    return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, check=False)

def windows():
    result = call(["tmux", "list-windows", "-t", SESSION,
                   "-F", "#{window_id}\t#{window_name}"])
    if result.returncode != 0:
        raise RuntimeError("list_windows_failed")
    return [line.split("\t", 1) for line in
            result.stdout.decode("utf-8", errors="strict").splitlines()]

def main():
    if sys.argv[1:] != ["--root-approved-observed-slot-lease"]:
        print(json.dumps({"started": False, "error": "explicit_root_run_lease_flag_required"}))
        return 64
    created_window = None
    phase = "verify_existing_session"
    try:
        # Resolve the actual name so a prefix match cannot satisfy this requirement.
        present = call(["tmux", "display-message", "-p", "-t", SESSION,
                        "#{session_name}"])
        if present.returncode != 0 or present.stdout.decode().strip() != SESSION:
            print(json.dumps({"started": False, "error": "required_host_session_missing",
                              "session": SESSION, "new_session_created": False}))
            return 1
        if any(len(row) == 2 and row[1] == WINDOW for row in windows()):
            print(json.dumps({"started": False, "error": "existing_named_window_preserved",
                              "session": SESSION, "window": WINDOW}))
            return 73
        command = shlex.join([
            "sudo", "-n", "/usr/sbin/pct", "exec", "102", "--", "docker", "exec",
            "qemu-tb371fc-dev", "python3", "-I", "-B", "-S",
            STAGE + "/run-job.py", "--run-approved-conditional-observation",
        ])
        phase = "create_exclusive_named_window"
        made = call(["tmux", "new-window", "-d", "-P", "-F", "#{window_id}",
                     "-t", SESSION + ":", "-n", WINDOW, "sh"], timeout=10)
        if made.returncode != 0:
            raise RuntimeError("new_window_failed")
        created_window = made.stdout.decode("ascii", errors="strict").strip()
        if re.fullmatch(r"@[0-9]+", created_window) is None:
            raise RuntimeError("new_window_id_invalid")
        # Detect a concurrent same-name creation before submitting any CT command.
        matches = [row[0] for row in windows() if len(row) == 2 and row[1] == WINDOW]
        if matches != [created_window]:
            raise RuntimeError("named_window_not_exclusive")
        steps = [
            ["tmux", "set-window-option", "-t", created_window,
             "automatic-rename", "off"],
            ["tmux", "set-window-option", "-t", created_window,
             "remain-on-exit", "on"],
            ["tmux", "send-keys", "-t", created_window, "-l", "exec " + command],
            ["tmux", "send-keys", "-t", created_window, "Enter"],
        ]
        for index, argv in enumerate(steps):
            phase = "submit_enter" if index == len(steps) - 1 else "prepare_window"
            result = call(argv, timeout=10)
            if result.returncode != 0:
                raise RuntimeError("window_launch_step_failed")
        print(json.dumps({"started": True, "session": SESSION,
                          "window": WINDOW, "window_id": created_window,
                          "new_session_created": False,
                          "ct_result": STAGE + "/job.result.json",
                          "ct_exit": STAGE + "/job.exit",
                          "launch_is_not_completion": True}, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"launch_confirmed": False, "phase": phase,
                          "error_type": type(error).__name__,
                          "session": SESSION, "window": WINDOW,
                          "created_window_id": created_window,
                          "created_window_preserved": True,
                          "execution_may_have_started": phase == "submit_enter",
                          "inspect_status_before_retry": True}, sort_keys=True))
        return 1

if __name__ == "__main__":
    sys.exit(main())
