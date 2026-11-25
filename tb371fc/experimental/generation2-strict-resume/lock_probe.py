#!/usr/bin/env python3
"""Read-only independent-process flock probe for synthetic fixtures."""
import fcntl
import json
import os
import stat
import sys

path, device, inode = sys.argv[1:]
fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
try:
    info = os.fstat(fd)
    assert stat.S_ISREG(info.st_mode)
    assert (info.st_dev, info.st_ino) == (int(device), int(inode))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
    except BlockingIOError:
        acquired = False
    print(json.dumps({"pid": os.getpid(), "device": info.st_dev,
                      "inode": info.st_ino, "acquired": acquired}))
finally:
    os.close(fd)
