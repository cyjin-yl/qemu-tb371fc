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
import hashlib
import http.client
import math
import os
import re
import secrets
import socket
import ssl
import stat
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


ATTEMPTS = 3
MAX_RETRY_WAIT = 30.0
RETRY_HTTP_STATUS = frozenset((408, 429, 500, 502, 503, 504))
RETRY_ERRNO = frozenset((errno.ECONNRESET, errno.ECONNABORTED,
                         errno.ETIMEDOUT, errno.EPIPE, errno.ENETDOWN,
                         errno.ENETUNREACH, errno.EHOSTUNREACH))

CACHE_ENV = "TB371FC_PUBLIC_SOURCE_CACHE"
CACHE_MAGIC = b"TB371FC-PUBLIC-SOURCE-V1\0"
CACHE_HEADER = struct.Struct(">32sQ32s")  # URL digest, body length, body digest
CACHE_HEADER_BYTES = len(CACHE_MAGIC) + CACHE_HEADER.size

def _directory_flags():
    # Platforms lacking these protections still support the uncached path.
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _cache_key(url):
    """Cache only unambiguous HTTPS raw-GitHub URLs at a complete Git revision."""
    if not isinstance(url, str) or not url.isascii() or len(url) > 4096:
        raise ValueError("public source cache requires a bounded ASCII URL")
    parsed = urllib.parse.urlsplit(url)
    parts = parsed.path.split("/")
    if (parsed.scheme != "https" or parsed.netloc != "raw.githubusercontent.com" or
            parsed.query or parsed.fragment or len(parts) < 5 or parts[0] or
            not re.fullmatch(r"[A-Za-z0-9_.-]+", parts[1]) or
            not re.fullmatch(r"[A-Za-z0-9_.-]+", parts[2]) or
            not re.fullmatch(r"[0-9a-fA-F]{40}", parts[3]) or
            any(p in ("", ".", "..") for p in parts[1:]) or
            any(not 33 <= ord(c) <= 126 or c in "%\\" for c in url)):
        raise ValueError("public source cache requires a fixed-revision raw.githubusercontent.com HTTPS URL")
    return hashlib.sha256(url.encode("ascii")).digest()


def _private_fd(fd, directory):
    info = os.fstat(fd)
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    mode = 0o700 if directory else 0o600
    if (not kind(info.st_mode) or stat.S_IMODE(info.st_mode) != mode or
            info.st_uid != os.geteuid() or (not directory and info.st_nlink != 1)):
        raise ValueError("public source cache has unsafe type, ownership, permissions or links")
    return info


def _cache_directory(path):
    """Walk with directory FDs so no path component can follow a symlink."""
    parts = path.split("/")
    if not path.startswith("/") or any(p in ("", ".", "..") for p in parts[1:]):
        raise ValueError("public source cache path must be an absolute directory without dot components")
    fd = os.open("/", _directory_flags())
    try:
        for index, part in enumerate(parts[1:], 1):
            if index == len(parts) - 1:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, _directory_flags(), dir_fd=fd)
            os.close(fd)
            fd = child
        _private_fd(fd, True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _cache_read(directory_fd, key, max_bytes):
    try:
        entry = os.open(key.hex(), _directory_flags(), dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    try:
        _private_fd(entry, True)
        try:
            fd = os.open("source", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC |
                         os.O_NONBLOCK, dir_fd=entry)
        except FileNotFoundError as error:
            raise ValueError("public source cache entry is incomplete") from error
        with os.fdopen(fd, "rb") as stream:
            info = _private_fd(stream.fileno(), False)
            if not CACHE_HEADER_BYTES <= info.st_size <= CACHE_HEADER_BYTES + max_bytes:
                raise ValueError("public source cache entry is truncated or exceeds the caller limit")
            framed = stream.read(CACHE_HEADER_BYTES + max_bytes + 1)
        if len(framed) < CACHE_HEADER_BYTES or not framed.startswith(CACHE_MAGIC):
            raise ValueError("public source cache header is invalid")
        stored_key, length, digest = CACHE_HEADER.unpack(framed[len(CACHE_MAGIC):CACHE_HEADER_BYTES])
        raw = framed[CACHE_HEADER_BYTES:]
        if (stored_key != key or length != len(raw) or length > max_bytes or
                hashlib.sha256(raw).digest() != digest):
            raise ValueError("public source cache identity or length is invalid")
        return raw
    finally:
        os.close(entry)


def _cache_rename_noreplace(directory_fd, source, target):
    """Linux atomic no-replace publication, including existing empty targets."""
    import ctypes

    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError as error:
        raise OSError(errno.ENOSYS, "public source cache requires Linux renameat2") from error
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                         ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    # Linux RENAME_NOREPLACE = 1. No fallback may replace any existing target.
    if renameat2(directory_fd, os.fsencode(source), directory_fd,
                 os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), target)


def _cache_publish(directory_fd, key, raw):
    """Atomically publish a complete directory; never delete failed staging data."""
    pending = ".pending-" + secrets.token_hex(16)
    os.mkdir(pending, mode=0o700, dir_fd=directory_fd)
    entry = os.open(pending, _directory_flags(), dir_fd=directory_fd)
    try:
        _private_fd(entry, True)
        fd = os.open("source", os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=entry)
        with os.fdopen(fd, "wb") as stream:
            _private_fd(stream.fileno(), False)
            stream.write(CACHE_MAGIC)
            stream.write(CACHE_HEADER.pack(key, len(raw), hashlib.sha256(raw).digest()))
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(entry)
    finally:
        os.close(entry)
    try:
        # Even an incomplete/empty target must survive a concurrent commit.
        _cache_rename_noreplace(directory_fd, pending, key.hex())
        os.fsync(directory_fd)
    except OSError as error:
        if error.errno not in (errno.EEXIST, errno.ENOTEMPTY):
            raise
        if _cache_read(directory_fd, key, len(raw)) != raw:
            raise ValueError("public source cache concurrent response differs") from error


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


def _read_source_network(url, *, max_bytes, timeout, caching=False):
    """Use the original three-attempt/30-second-delay transport policy.

Every retry starts a new GET and discards the failed response.  HTTP status,
certificate, and argument errors that are not explicitly transient propagate
immediately.  An excessive Retry-After propagates the original HTTP failure
without shortening the server's requested delay.
"""
    waited = 0.0
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                if caching and (response.geturl() != url or response.status != 200 or
                                response.headers.get("Content-Range") is not None):
                    raise ValueError("public source cache requires an unchanged URL and a complete HTTP200 response")
                raw = response.read(max_bytes + 1)
                # read(amt) can return a short EOF without IncompleteRead.
                # A declared, unfinished body is a transport failure; a
                # complete body with the wrong identity is left to the caller.
                length = response.headers.get("Content-Length")
                if caching and length is not None and not (length.isascii() and length.isdecimal()):
                    raise ValueError("public source cache response has an invalid Content-Length")
                if (len(raw) <= max_bytes and length is not None and
                        length.isascii() and length.isdecimal() and
                        float(length) > len(raw)):
                    raise http.client.IncompleteRead(raw)
                if (caching and len(raw) <= max_bytes and length is not None and
                        float(length) != len(raw)):
                    raise ValueError("public source cache response length differs")
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


def read_source(url, *, max_bytes, timeout=30):
    """Return bounded bytes; callers must still verify full length/SHA/Git blob.

Without TB371FC_PUBLIC_SOURCE_CACHE this is the original uncached transport.
When explicitly enabled on Linux, the value names a private0700 directory whose parent
already exists. Only fixed-revision public raw-GitHub URLs enter the cache;
other URLs use the original uncached transport and caller validation. Cache
framing detects local truncation/corruption but does not authenticate a source;
every caller's existing pinned-content validation remains mandatory. A bad
entry fails closed without network fallback. Oversize or incomplete responses
are never published. Failed or concurrent staging directories are retained.
Atomic publication requires renameat2(RENAME_NOREPLACE); missing support fails.
"""
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    cache = os.environ.get(CACHE_ENV)
    if not cache:
        return _read_source_network(url, max_bytes=max_bytes, timeout=timeout)
    try:
        key = _cache_key(url)
    except ValueError:
        # Cache eligibility is not a restriction on the existing downloader.
        # In particular, older generators use tags with complete content pins.
        return _read_source_network(url, max_bytes=max_bytes, timeout=timeout)
    if sys.platform != "linux":
        raise OSError(errno.ENOSYS, "public source cache is supported only on Linux")
    directory = _cache_directory(cache)
    try:
        raw = _cache_read(directory, key, max_bytes)
        if raw is not None:
            return raw
        raw = _read_source_network(url, max_bytes=max_bytes, timeout=timeout, caching=True)
        if len(raw) <= max_bytes:
            _cache_publish(directory, key, raw)
        return raw
    finally:
        os.close(directory)
