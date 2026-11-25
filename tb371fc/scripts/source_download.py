#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read bounded public source inputs, retrying only transient transport errors.

Source identity validation belongs to the caller, outside this retry loop.  The
three request attempts and 30-second aggregate retry-delay allowance supplement
the source-generation gate's existing process timeout; a socket timeout is not
an absolute wall-clock deadline for a slow response.
"""
from email.utils import parsedate_to_datetime
import errno
import http.client
import math
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request


ATTEMPTS = 3
MAX_RETRY_WAIT = 30.0
RETRY_HTTP_STATUS = frozenset((408, 429, 500, 502, 503, 504))
RETRY_ERRNO = frozenset((errno.ECONNRESET, errno.ECONNABORTED,
                         errno.ETIMEDOUT, errno.EPIPE, errno.ENETDOWN,
                         errno.ENETUNREACH, errno.EHOSTUNREACH))


def _transient(error):
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRY_HTTP_STATUS
    if isinstance(error, urllib.error.URLError):
        return _transient(error.reason)
    if isinstance(error, ssl.SSLError):
        return False
    if isinstance(error, (TimeoutError, http.client.IncompleteRead,
                          http.client.RemoteDisconnected)):
        return True
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    return isinstance(error, OSError) and error.errno in RETRY_ERRNO


def _retry_after(error):
    """Return the server's minimum delay, or None for an absent/invalid field."""
    if not isinstance(error, urllib.error.HTTPError) or error.headers is None:
        return None
    value = error.headers.get("Retry-After")
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdecimal():
        # float also lets an enormous delta fail the budget check, rather than
        # ignoring a valid server delay after an integer-conversion limit.
        return float(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        return max(0.0, date.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return None


def read_source(url, *, max_bytes, timeout=30):
    """Return at most max_bytes + 1 bytes; callers reject oversize/bad pins.

Every retry starts a new GET and discards the failed response.  HTTP status,
certificate, and argument errors that are not explicitly transient propagate
immediately.  An excessive Retry-After propagates the original HTTP failure
without shortening the server's requested delay.
"""
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    waited = 0.0
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                raw = response.read(max_bytes + 1)
                # read(amt) can return a short EOF without IncompleteRead.
                # A declared, unfinished body is a transport failure; a
                # complete body with the wrong identity is left to the caller.
                length = response.headers.get("Content-Length")
                if (len(raw) <= max_bytes and length is not None and
                        length.isascii() and length.isdecimal() and
                        float(length) > len(raw)):
                    raise http.client.IncompleteRead(raw)
                return raw
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            if attempt == ATTEMPTS or not _transient(error):
                raise
            delay = float(2 ** (attempt - 1))
            server_delay = _retry_after(error)
            if server_delay is not None:
                delay = max(delay, server_delay)
            if not math.isfinite(delay) or delay > MAX_RETRY_WAIT - waited:
                raise
            reason = (f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError)
                      else type(error).__name__)
            print(f"public source download: {reason}; retry {attempt + 1}/{ATTEMPTS} "
                  f"after {delay:g}s", file=sys.stderr)
            time.sleep(delay)
            waited += delay
