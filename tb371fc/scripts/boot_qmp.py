#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared QMP observation for console and RAM-checkpoint boot acceptance."""
from __future__ import annotations

import json
import select
import socket
import time


def has_guest_shutdown(messages):
    return any(isinstance(item, dict) and item.get("event") == "SHUTDOWN" and
               isinstance(item.get("data"), dict) and
               item["data"].get("guest") is True and
               item["data"].get("reason") == "guest-shutdown"
               for item in messages)


def connect(path, process, deadline):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    while True:
        try:
            sock.connect(str(path))
            return sock
        except (FileNotFoundError, ConnectionRefusedError):
            if process.poll() is not None or time.monotonic() >= deadline:
                sock.close()
                raise RuntimeError(f"QEMU socket unavailable: {path}")
            time.sleep(.01)


class QMP:
    def __init__(self, sock):
        self.sock, self.buffer, self.messages = sock, b"", []
        self.identifier = 0
        self.closed = False

    def receive(self, timeout):
        if self.closed:
            return None
        if not select.select([self.sock], [], [], timeout)[0]:
            return []
        data = self.sock.recv(65536)
        if not data:
            self.closed = True
            return None
        self.buffer += data
        messages = []
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if line.strip():
                messages.append(json.loads(line))
        self.messages.extend(messages)
        return messages

    def command(self, name, arguments=None):
        self.identifier += 1
        request = {"execute": name, "id": self.identifier}
        if arguments is not None:
            request["arguments"] = arguments
        self.sock.sendall(json.dumps(request).encode() + b"\n")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            messages = self.receive(.1)
            if messages is None:
                raise RuntimeError("QMP connection closed")
            for message in messages:
                if message.get("id") == self.identifier:
                    if "error" in message:
                        raise RuntimeError(f"QMP error: {message}")
                    return message.get("return")
        raise TimeoutError(f"QMP {name} response timed out")

    def drain(self):
        while self.receive(0):
            pass
