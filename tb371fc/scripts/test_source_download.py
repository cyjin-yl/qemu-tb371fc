#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise real HTTP failures and verifier integrity without public network I/O."""
from argparse import Namespace
from collections import Counter
from contextlib import contextmanager
from email.utils import formatdate
import hashlib
import http.client
import ctypes
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import source_download
import verify_llcc_generation as llcc


class HttpFixture:
    def __init__(self, replies):
        self.replies = replies
        self.requests = Counter()

    def __enter__(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                index = fixture.requests[self.path]
                fixture.requests[self.path] += 1
                choices = fixture.replies[self.path]
                status, body, headers = choices[min(index, len(choices) - 1)]
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


@contextmanager
def quick_retries():
    # Keep transport, response handling, and retry selection real; only the
    # waits are observed instead of delaying every test by multiple seconds.
    with patch.object(source_download.time, "sleep") as sleep:
        yield sleep


@patch.dict(os.environ, {source_download.CACHE_ENV: ""})
class DownloadTests(unittest.TestCase):
    def test_transient_http_recovers_with_fresh_complete_body(self):
        for code in sorted(source_download.RETRY_HTTP_STATUS):
            with self.subTest(status=code), HttpFixture({"/pin": [
                    (code, b"temporary response", {}), (200, b"complete source", {})]
                    }) as server, quick_retries() as sleep:
                self.assertEqual(source_download.read_source(server.url + "/pin", max_bytes=100),
                                 b"complete source")
                self.assertEqual(server.requests["/pin"], 2)
                self.assertEqual(sleep.call_args_list[0].args, (1.0,))

    def test_retry_after_delta_is_respected(self):
        with HttpFixture({"/pin": [(429, b"", {"Retry-After": "7"}),
                                    (200, b"source", {})]}) as server, quick_retries() as sleep:
            self.assertEqual(source_download.read_source(server.url + "/pin", max_bytes=100), b"source")
            self.assertEqual(sleep.call_args.args, (7.0,))

    def test_retry_after_http_date_is_respected(self):
        now = 1700000000
        with HttpFixture({"/pin": [(503, b"", {"Retry-After": formatdate(now + 12, usegmt=True)}),
                                    (200, b"source", {})]}) as server, quick_retries() as sleep, \
                patch.object(source_download.time, "time", return_value=now):
            self.assertEqual(source_download.read_source(server.url + "/pin", max_bytes=100), b"source")
            self.assertEqual(sleep.call_args.args, (12.0,))

    def test_excessive_retry_after_does_not_retry_early(self):
        for delay in ("31", "9" * 5000):
            with self.subTest(delay=delay[:20]), HttpFixture({"/pin": [
                    (429, b"", {"Retry-After": delay}), (200, b"source", {})]
                    }) as server, quick_retries() as sleep:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    source_download.read_source(server.url + "/pin", max_bytes=100)
                self.assertEqual(caught.exception.code, 429)
                self.assertEqual(server.requests["/pin"], 1)
                sleep.assert_not_called()

    def test_retry_delay_budget_is_shared_across_attempts(self):
        with HttpFixture({"/pin": [(503, b"", {"Retry-After": "20"}),
                                    (429, b"", {"Retry-After": "15"}),
                                    (200, b"source", {})]}) as server, quick_retries() as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                source_download.read_source(server.url + "/pin", max_bytes=100)
            self.assertEqual(server.requests["/pin"], 2)
            self.assertEqual(sleep.call_count, 1)
            self.assertEqual(sleep.call_args.args, (20.0,))

    def test_exhausted_retries_preserve_http_failure(self):
        with HttpFixture({"/pin": [(503, b"temporary", {})]}) as server, quick_retries() as sleep:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                source_download.read_source(server.url + "/pin", max_bytes=100)
            self.assertEqual(caught.exception.code, 503)
            self.assertEqual(server.requests["/pin"], 3)
            self.assertEqual([x.args for x in sleep.call_args_list], [(1.0,), (2.0,)])

    def test_permanent_http_errors_are_not_retried(self):
        for code in (400, 401, 403, 404, 501):
            with self.subTest(status=code), HttpFixture({"/pin": [(code, b"rejected", {})]}) \
                    as server, quick_retries() as sleep:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    source_download.read_source(server.url + "/pin", max_bytes=100)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(server.requests["/pin"], 1)
                sleep.assert_not_called()

    def test_short_http_body_is_discarded_before_retry(self):
        with HttpFixture({"/pin": [(200, b"partial", {"Content-Length": "50"}),
                                    (200, b"complete source", {})]}) as server, quick_retries():
            self.assertEqual(source_download.read_source(server.url + "/pin", max_bytes=100),
                             b"complete source")
            self.assertEqual(server.requests["/pin"], 2)

    def test_oversize_body_is_bounded_and_not_retried(self):
        with HttpFixture({"/pin": [(200, b"x" * 100, {})]}) as server, quick_retries() as sleep:
            self.assertEqual(source_download.read_source(server.url + "/pin", max_bytes=10), b"x" * 11)
            self.assertEqual(server.requests["/pin"], 1)
            sleep.assert_not_called()

    def test_certificate_failures_are_not_retried(self):
        error = urllib.error.URLError(ssl.SSLCertVerificationError("fixture certificate failure"))
        with patch.object(source_download.urllib.request, "urlopen", side_effect=error) as opened, \
                quick_retries() as sleep:
            with self.assertRaises(urllib.error.URLError):
                source_download.read_source("https://fixture.invalid", max_bytes=100)
            self.assertEqual(opened.call_count, 1)
            sleep.assert_not_called()

    def test_temporary_dns_and_timeout_are_retried_but_other_dns_is_not(self):
        for reason, count in ((socket.gaierror(socket.EAI_AGAIN, "again"), 3),
                              (TimeoutError("fixture timeout"), 3),
                              (socket.gaierror(socket.EAI_NONAME, "unknown"), 1)):
            with self.subTest(reason=reason), \
                    patch.object(source_download.urllib.request, "urlopen",
                                 side_effect=urllib.error.URLError(reason)) as opened, \
                    quick_retries():
                with self.assertRaises(urllib.error.URLError):
                    source_download.read_source("https://fixture.invalid", max_bytes=100)
                self.assertEqual(opened.call_count, count)

    def test_http_error_response_is_closed(self):
        body = io.BytesIO(b"rejected")
        error = urllib.error.HTTPError("https://fixture.invalid", 404, "missing", {}, body)
        with patch.object(source_download.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                source_download.read_source("https://fixture.invalid", max_bytes=100)
        self.assertTrue(body.closed)


@patch.dict(os.environ, {source_download.CACHE_ENV: ""})
class LlccVerifierTests(unittest.TestCase):
    """Real HTTP plus the existing verifier's identity checks and reporting.

Eight synthetic complete sources keep this suite offline.  Only generation is
stubbed, because the test concerns downloading and pin admission, not parsing
the upstream kernel.  Actual source generation remains a separate CI gate.
"""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = {f"source{index}.c": f"source fixture {index}\n".encode() for index in range(8)}
        self.pins = tuple(("primary", "fixture/repo", "revision", path, len(raw),
                           hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
                           hashlib.sha256(raw).hexdigest()) for path, raw in self.raw.items())

    @contextmanager
    def verifier(self, server):
        original_open = urllib.request.urlopen
        def open_local(url, **kwargs):
            path = url.rsplit("/", 1)[1]
            response = original_open(server.url + "/" + path, **kwargs)
            # The fixture transports a requested public URL through localhost.
            # Cache tests still inspect a complete real HTTP response.
            response.geturl = lambda: url
            return response
        def generate(command, **kwargs):
            target = Path(command[command.index("--output-root") + 1])
            for relative in llcc.generator.OUTPUTS:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(llcc.ROOT / relative, destination)
            return Namespace(returncode=0, stdout="fixture generator\n", stderr="")
        with patch.object(llcc.generator, "SOURCE_PINS", self.pins), \
                patch.object(source_download.urllib.request, "urlopen", side_effect=open_local), \
                patch.object(llcc.subprocess, "run", side_effect=generate) as generated, \
                quick_retries() as sleep:
            yield generated, sleep

    def replies(self):
        return {"/" + name: [(200, raw, {})] for name, raw in self.raw.items()}

    def test_eighth_source_429_recovers_and_preserves_all_pin_checks(self):
        replies = self.replies()
        replies["/source7.c"].insert(0, (429, b"rate limited", {"Retry-After": "0"}))
        with HttpFixture(replies) as server, self.verifier(server) as (generated, _):
            out = self.root / "accepted"
            self.assertEqual(llcc.run(Namespace(out=out, source_root=None)), 0)
            report = json.loads((out / "result.json").read_text())
            self.assertIs(report["passed"], True)
            self.assertIs(report["source_pins_verified"], True)
            self.assertEqual(len(report["inputs"]), 8)
            self.assertEqual(len(report["checks"]), 2)
            self.assertEqual(server.requests["/source7.c"], 2)
            self.assertEqual(sum(server.requests.values()), 9)
            generated.assert_called_once()

    def test_wrong_hash_length_or_blob_fails_without_retry_or_generation(self):
        for failure in ("hash", "length", "blob"):
            with self.subTest(failure=failure):
                replies = self.replies()
                pins = list(self.pins)
                if failure == "hash":
                    old = replies["/source7.c"][0][1]
                    replies["/source7.c"] = [(200, bytes([old[0] ^ 1]) + old[1:], {})]
                else:
                    pin = list(pins[-1])
                    pin[4 if failure == "length" else 5] = (len(self.raw["source7.c"]) + 1
                                                           if failure == "length" else "0" * 40)
                    pins[-1] = tuple(pin)
                with HttpFixture(replies) as server, self.verifier(server) as (generated, sleep), \
                        patch.object(llcc.generator, "SOURCE_PINS", tuple(pins)):
                    out = self.root / failure
                    self.assertEqual(llcc.run(Namespace(out=out, source_root=None)), 1)
                    report = json.loads((out / "result.json").read_text())
                    self.assertIs(report["passed"], False)
                    self.assertIs(report["source_pins_verified"], False)
                    self.assertIn("Public source identity differs", report["error"])
                    self.assertEqual(sum(server.requests.values()), 8)
                    self.assertEqual(server.requests["/source7.c"], 1)
                    generated.assert_not_called()
                    sleep.assert_not_called()

    def test_source_root_does_not_use_network_and_rejects_bad_pin(self):
        local = self.root / "sources"
        for name, raw in self.raw.items():
            path = local / "primary" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        (local / "primary/source7.c").write_bytes(b"wrong fixture")
        with patch.object(llcc.generator, "SOURCE_PINS", self.pins), \
                patch.object(source_download.urllib.request, "urlopen") as opened, \
                patch.object(llcc.subprocess, "run") as generated:
            self.assertEqual(llcc.run(Namespace(out=self.root / "local", source_root=local)), 1)
            opened.assert_not_called()
            generated.assert_not_called()

    def test_cache_reuses_eight_inputs_and_runs_full_verifier_twice(self):
        pins = tuple((*pin[:2], "1" * 40, *pin[3:]) for pin in self.pins)
        with HttpFixture(self.replies()) as server, self.verifier(server) as (generated, sleep), \
                patch.object(llcc.generator, "SOURCE_PINS", pins), \
                patch.dict(os.environ, {source_download.CACHE_ENV: str(self.root / "cache")}):
            for label in ("first", "cached"):
                out = self.root / label
                self.assertEqual(llcc.run(Namespace(out=out, source_root=None)), 0)
                report = json.loads((out / "result.json").read_text())
                self.assertIs(report["source_pins_verified"], True)
                self.assertEqual(len(report["inputs"]), 8)
                self.assertEqual(len(report["checks"]), 2)
            self.assertEqual(sum(server.requests.values()), 8)
            self.assertEqual(generated.call_count, 2)
            sleep.assert_not_called()

    def test_cache_hits_still_fail_each_complete_source_pin(self):
        pins = tuple((*pin[:2], "1" * 40, *pin[3:]) for pin in self.pins)
        with HttpFixture(self.replies()) as server, self.verifier(server) as (generated, sleep), \
                patch.object(llcc.generator, "SOURCE_PINS", pins), \
                patch.dict(os.environ, {source_download.CACHE_ENV: str(self.root / "cache")}):
            self.assertEqual(llcc.run(Namespace(out=self.root / "warm", source_root=None)), 0)
            generated.reset_mock()
            for field, value in ((4, 12345), (5, "0" * 40), (6, "0" * 64)):
                changed = list(pins)
                tail = list(changed[-1]); tail[field] = value; changed[-1] = tuple(tail)
                with self.subTest(pin_field=field), patch.object(llcc.generator, "SOURCE_PINS", tuple(changed)):
                    out = self.root / f"bad-cache-pin-{field}"
                    self.assertEqual(llcc.run(Namespace(out=out, source_root=None)), 1)
                    report = json.loads((out / "result.json").read_text())
                    self.assertIs(report["source_pins_verified"], False)
                    self.assertIn("Public source identity differs", report["error"])
            self.assertEqual(sum(server.requests.values()), 8)
            generated.assert_not_called()
            sleep.assert_not_called()


class CacheResponse(io.BytesIO):
    """Complete-response fixture; tests never connect to public HTTP services."""
    def __init__(self, raw, url, *, headers=None, status=200):
        super().__init__(raw)
        self.url = url
        self.status = status
        self.headers = {"Content-Length": str(len(raw))} if headers is None else headers
        self.read_sizes = []

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class PublicSourceCacheTests(unittest.TestCase):
    URL = "https://raw.githubusercontent.com/fixture/repo/" + "1" * 40 + "/source.c"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tb371fc-source-cache-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        env = patch.dict(os.environ, {source_download.CACHE_ENV: str(self.cache)})
        env.start(); self.addCleanup(env.stop)

    def entry(self, url=None, cache=None):
        url = self.URL if url is None else url
        return (self.cache if cache is None else cache) / hashlib.sha256(url.encode()).hexdigest()

    def seed(self, raw=b"complete source\n", url=None, max_bytes=100):
        url = self.URL if url is None else url
        response = CacheResponse(raw, url)
        with patch.object(source_download.urllib.request, "urlopen", return_value=response) as opened:
            self.assertEqual(source_download.read_source(url, max_bytes=max_bytes), raw)
            opened.assert_called_once_with(url, timeout=30)
        return raw

    def assert_bad_without_network(self, url=None, max_bytes=100):
        with patch.object(source_download.urllib.request, "urlopen") as opened:
            with self.assertRaises((ValueError, OSError)):
                source_download.read_source(self.URL if url is None else url, max_bytes=max_bytes)
            opened.assert_not_called()

    def test_complete_cache_hit_uses_no_network_and_private_permissions(self):
        raw = self.seed()
        with patch.object(source_download.urllib.request, "urlopen") as opened:
            self.assertEqual(source_download.read_source(self.URL, max_bytes=100), raw)
            opened.assert_not_called()
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.entry().stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.entry() / "source").stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in self.cache.iterdir()], [self.entry().name])

    def test_revision_repository_and_path_are_distinct_keys(self):
        urls = [self.URL, self.URL.replace("1" * 40, "2" * 40),
                self.URL.replace("fixture/repo", "fixture/other"), self.URL + ".other"]
        for i, url in enumerate(urls):
            self.seed(f"source{i}".encode(), url=url)
        with patch.object(source_download.urllib.request, "urlopen") as opened:
            for i, url in enumerate(urls):
                self.assertEqual(source_download.read_source(url, max_bytes=100), f"source{i}".encode())
            opened.assert_not_called()
        self.assertEqual(len(list(self.cache.iterdir())), 4)

    def test_unpinned_nonpublic_or_ambiguous_urls_use_uncached_transport(self):
        bad = [self.URL.replace("https:", "http:"), self.URL.replace("raw.githubusercontent.com", "example.com"),
               self.URL.replace("1" * 40, "main"), self.URL.replace("1" * 40, "1" * 39),
               self.URL + "?token=fixture", self.URL + "#fragment",
               self.URL.replace("raw.githubusercontent.com", "user@raw.githubusercontent.com"),
               self.URL.replace("raw.githubusercontent.com", "raw.githubusercontent.com:443"),
               self.URL.replace("source.c", "../main/source.c"), self.URL.replace("source.c", "%2e%2e/source.c"),
               self.URL.replace("source.c", "dir\\source.c"), self.URL + "\n"]
        for url in bad:
            with self.subTest(url=url), \
                    patch.object(source_download, "_cache_directory") as directory, \
                    patch.object(source_download, "_read_source_network", return_value=b"uncached") as network:
                with self.assertRaises(ValueError):
                    source_download._cache_key(url)
                self.assertEqual(source_download.read_source(url, max_bytes=100), b"uncached")
                network.assert_called_once_with(url, max_bytes=100, timeout=30)
                directory.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_gcc_tag_url_is_never_cached_and_does_not_change_pinned_entries(self):
        url = "https://raw.githubusercontent.com/gregkh/linux/v6.12.114/drivers/clk/qcom/gcc-sm8250.c"
        cached = self.seed()
        before = (self.entry() / "source").read_bytes()
        with patch.object(source_download, "_cache_directory") as directory, \
                patch.object(source_download.urllib.request, "urlopen", side_effect=[
                    CacheResponse(b"first tag response", url), CacheResponse(b"second tag response", url)]) as opened:
            self.assertEqual(source_download.read_source(url, max_bytes=512*1024), b"first tag response")
            self.assertEqual(source_download.read_source(url, max_bytes=512*1024), b"second tag response")
            self.assertEqual(opened.call_count, 2)
            directory.assert_not_called()
        self.assertEqual((self.entry() / "source").read_bytes(), before)
        self.assertEqual([p.name for p in self.cache.iterdir()], [self.entry().name])
        with patch.object(source_download.urllib.request, "urlopen") as opened:
            self.assertEqual(source_download.read_source(self.URL, max_bytes=100), cached)
            opened.assert_not_called()

    def test_uncacheable_tag_retains_original_transient_retry_policy(self):
        url = "https://raw.githubusercontent.com/gregkh/linux/v6.12.114/drivers/clk/qcom/gcc-sm8250.c"
        error = urllib.error.HTTPError(url, 429, "limited", {"Retry-After": "5"}, io.BytesIO(b"limited"))
        with patch.object(source_download, "_cache_directory") as directory, \
                patch.object(source_download.urllib.request, "urlopen", side_effect=[
                    error, CacheResponse(b"complete tag response", url)]) as opened, quick_retries() as sleep:
            self.assertEqual(source_download.read_source(url, max_bytes=100), b"complete tag response")
            self.assertEqual(opened.call_count, 2)
            sleep.assert_called_once_with(5.0)
            directory.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_actual_gcc_verifier_still_rejects_wrong_tag_content_without_retry(self):
        import verify_gcc_generation as gcc

        output = self.root / "gcc-hash-rejection"
        calls = []
        def response(url, timeout):
            self.assertTrue(url.startswith("https://raw.githubusercontent.com/gregkh/linux/v6.12.114/"))
            self.assertEqual(timeout, 30)
            calls.append(url)
            return CacheResponse(b"wrong complete GCC source bytes\n", url)
        with patch.object(source_download, "_cache_directory") as directory, \
                patch.object(source_download.urllib.request, "urlopen", side_effect=response), \
                patch.object(gcc.subprocess, "run") as generated, quick_retries() as sleep:
            self.assertEqual(gcc.run(Namespace(out=output)), 1)
            generated.assert_not_called()
            sleep.assert_not_called()
            directory.assert_not_called()
        report = json.loads((output / "result.json").read_text())
        self.assertEqual(len(calls), 1)
        self.assertFalse(report["source_pins_verified"])
        self.assertEqual(report["checks"], [])
        self.assertEqual(len(report["inputs"]), 1)
        self.assertIn("Public source hash differs:", report["error"])
        self.assertNotIn("public source cache requires", report["error"])
        self.assertFalse(self.cache.exists())

    def test_disabled_cache_preserves_uncached_mutable_url_behavior(self):
        url = "https://fixture.invalid/main/source.c"
        with patch.dict(os.environ, {source_download.CACHE_ENV: ""}), \
                patch.object(source_download, "_cache_directory") as directory, \
                patch.object(source_download.urllib.request, "urlopen",
                             side_effect=[CacheResponse(b"first", url), CacheResponse(b"second", url)]) as opened:
            self.assertEqual(source_download.read_source(url, max_bytes=100), b"first")
            self.assertEqual(source_download.read_source(url, max_bytes=100), b"second")
            self.assertEqual(opened.call_count, 2)
            directory.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_invalid_arguments_fail_before_cache_or_network(self):
        for args in ({"max_bytes":0}, {"max_bytes":True}, {"max_bytes":1.5},
                     {"max_bytes":10,"timeout":0}, {"max_bytes":10,"timeout":float("inf")}):
            with self.subTest(args=args), patch.object(source_download.urllib.request, "urlopen") as opened:
                with self.assertRaises(ValueError):
                    source_download.read_source(self.URL, **args)
                opened.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_truncated_and_wrong_magic_entries_are_not_silent_misses(self):
        self.seed()
        path = self.entry() / "source"
        original = path.read_bytes()
        for bad in (b"", original[:source_download.CACHE_HEADER_BYTES - 1],
                    original[:-1], bytes([original[0] ^ 1]) + original[1:]):
            with self.subTest(size=len(bad)):
                path.write_bytes(bad)
                self.assert_bad_without_network()

    def test_same_length_body_corruption_is_rejected_without_retry(self):
        self.seed()
        path = self.entry() / "source"
        raw = path.read_bytes(); path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        self.assert_bad_without_network()

    def test_self_consistent_cache_is_not_an_external_source_pin(self):
        self.seed()
        path = self.entry() / "source"
        altered = b"malicious but internally consistent fixture"
        path.write_bytes(source_download.CACHE_MAGIC + source_download.CACHE_HEADER.pack(
            hashlib.sha256(self.URL.encode()).digest(), len(altered), hashlib.sha256(altered).digest()) + altered)
        with patch.object(source_download.urllib.request, "urlopen") as opened:
            raw = source_download.read_source(self.URL, max_bytes=100)
            # The cache checks framing; caller's immutable pin must still fail.
            self.assertNotEqual(hashlib.sha256(raw).hexdigest(), hashlib.sha256(b"complete source\n").hexdigest())
            self.assertNotEqual(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
                                hashlib.sha1(b"blob 16\0complete source\n").hexdigest())
            opened.assert_not_called()

    def test_other_url_entry_cannot_be_renamed_into_a_valid_hit(self):
        self.seed()
        other = self.URL.replace("source.c", "other.c")
        self.entry().rename(self.entry(other))
        self.assert_bad_without_network(other)

    def test_cached_response_larger_than_new_limit_fails_bounded(self):
        self.seed(raw=b"x" * 32)
        self.assert_bad_without_network(max_bytes=10)
        self.assertEqual((self.entry() / "source").stat().st_size,
                         source_download.CACHE_HEADER_BYTES + 32)

    def test_oversize_network_response_is_bounded_and_never_cached(self):
        responses = [CacheResponse(b"x" * 100, self.URL) for _ in range(2)]
        with patch.object(source_download.urllib.request, "urlopen", side_effect=responses) as opened, quick_retries() as sleep:
            for _ in range(2):
                self.assertEqual(source_download.read_source(self.URL, max_bytes=10), b"x" * 11)
            self.assertEqual(opened.call_count, 2)
            self.assertTrue(all(r.read_sizes == [11] for r in responses))
            self.assertFalse(self.entry().exists())
            self.assertEqual(list(self.cache.iterdir()), [])
            sleep.assert_not_called()

    def test_short_response_retries_then_caches_only_complete_body(self):
        replies = [CacheResponse(b"partial", self.URL, headers={"Content-Length":"50"}),
                   CacheResponse(b"complete", self.URL)]
        with patch.object(source_download.urllib.request, "urlopen", side_effect=replies) as opened, quick_retries() as sleep:
            self.assertEqual(source_download.read_source(self.URL, max_bytes=100), b"complete")
            self.assertEqual(source_download.read_source(self.URL, max_bytes=100), b"complete")
            self.assertEqual(opened.call_count, 2)
            self.assertEqual([x.args for x in sleep.call_args_list], [(1.0,)])

    def test_incomplete_responses_exhaust_original_three_attempts_without_entry(self):
        replies = [CacheResponse(b"partial", self.URL, headers={"Content-Length":"50"}) for _ in range(3)]
        with patch.object(source_download.urllib.request, "urlopen", side_effect=replies) as opened, quick_retries() as sleep:
            with self.assertRaises(http.client.IncompleteRead):
                source_download.read_source(self.URL, max_bytes=100)
            self.assertEqual(opened.call_count, 3)
            self.assertEqual([x.args for x in sleep.call_args_list], [(1.0,), (2.0,)])
        self.assertFalse(self.entry().exists())
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_redirect_or_partial_status_is_never_read_or_cached(self):
        replies = [CacheResponse(b"body", self.URL.replace("1"*40, "main")),
                   CacheResponse(b"body", self.URL, status=206),
                   CacheResponse(b"body", self.URL, headers={"Content-Range":"bytes0-3/10"})]
        for response in replies:
            with self.subTest(url=response.url, status=response.status), \
                    patch.object(source_download.urllib.request, "urlopen", return_value=response), quick_retries() as sleep:
                with self.assertRaises(ValueError):
                    source_download.read_source(self.URL, max_bytes=100)
                self.assertEqual(response.read_sizes, [])
                sleep.assert_not_called()
        self.assertFalse(self.entry().exists())

    def test_invalid_or_shorter_declared_length_is_not_cached(self):
        for length in ("invalid", "-1", "3"):
            with self.subTest(length=length), patch.object(source_download.urllib.request, "urlopen",
                    return_value=CacheResponse(b"longer", self.URL, headers={"Content-Length":length})), quick_retries() as sleep:
                with self.assertRaises(ValueError):
                    source_download.read_source(self.URL, max_bytes=100)
                sleep.assert_not_called()
        self.assertFalse(self.entry().exists())

    def test_complete_response_without_content_length_can_be_cached(self):
        with patch.object(source_download.urllib.request, "urlopen",
                          return_value=CacheResponse(b"EOF body", self.URL, headers={})) as opened:
            self.assertEqual(source_download.read_source(self.URL, max_bytes=100), b"EOF body")
            self.assertEqual(source_download.read_source(self.URL, max_bytes=100), b"EOF body")
            self.assertEqual(opened.call_count, 1)

    def test_directory_and_ancestor_symlinks_are_not_followed(self):
        real = self.root / "real"; real.mkdir(mode=0o700)
        self.cache.symlink_to(real, target_is_directory=True)
        self.assert_bad_without_network()
        self.assertEqual(list(real.iterdir()), [])
        ancestor = self.root / "ancestor"; ancestor.symlink_to(real, target_is_directory=True)
        with patch.dict(os.environ, {source_download.CACHE_ENV: str(ancestor / "child")}):
            self.assert_bad_without_network()
        self.assertEqual(list(real.iterdir()), [])

    def test_nonprivate_cache_directory_is_rejected(self):
        self.cache.mkdir(mode=0o755)
        self.assert_bad_without_network()
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o755)

    def test_cache_owned_by_another_uid_is_rejected(self):
        self.cache.mkdir(mode=0o700)
        with patch.object(source_download.os, "geteuid", return_value=os.geteuid() + 1):
            self.assert_bad_without_network()

    def test_relative_or_dot_cache_directory_is_rejected(self):
        for path in ("relative", str(self.root / ".." / "cache"), str(self.root) + "/./cache", str(self.root) + "/"):
            with self.subTest(path=path), patch.dict(os.environ, {source_download.CACHE_ENV:path}):
                self.assert_bad_without_network()

    def test_cache_entry_symlink_is_not_followed(self):
        self.seed(); kept = self.cache / "kept-entry"; self.entry().rename(kept)
        self.entry().symlink_to(kept, target_is_directory=True)
        self.assert_bad_without_network()

    def test_cache_body_symlink_is_not_followed(self):
        self.seed(); source = self.entry() / "source"; kept = self.entry() / "kept-source"
        source.rename(kept); source.symlink_to(kept)
        self.assert_bad_without_network()

    def test_nonprivate_entry_or_body_and_hardlink_fail_closed(self):
        self.seed(); source = self.entry() / "source"
        self.entry().chmod(0o755); self.assert_bad_without_network(); self.entry().chmod(0o700)
        source.chmod(0o644); self.assert_bad_without_network(); source.chmod(0o600)
        os.link(source, self.entry() / "hardlinked-source")
        self.assert_bad_without_network()

    def test_incomplete_directory_and_fifo_body_fail_without_blocking(self):
        self.cache.mkdir(mode=0o700); self.entry().mkdir(mode=0o700)
        self.assert_bad_without_network()
        os.mkfifo(self.entry() / "source", mode=0o600)
        self.assert_bad_without_network()

    def test_failed_atomic_publication_remains_staging_and_is_not_a_hit(self):
        response = CacheResponse(b"complete", self.URL)
        with patch.object(source_download.urllib.request, "urlopen", return_value=response), \
                patch.object(source_download, "_cache_rename_noreplace", side_effect=OSError(5,"fixture commit failure")):
            with self.assertRaises(OSError):
                source_download.read_source(self.URL, max_bytes=100)
        self.assertFalse(self.entry().exists())
        pending = list(self.cache.iterdir())
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0].name.startswith(".pending-"))
        self.assertGreater((pending[0] / "source").stat().st_size, source_download.CACHE_HEADER_BYTES)
        self.seed(raw=b"fresh complete")
        self.assertTrue(pending[0].exists())
        self.assertTrue(self.entry().exists())

    def test_concurrent_complete_publication_never_overwrites_existing_bytes(self):
        original = self.seed()
        path = self.entry() / "source"; before = path.read_bytes()
        real_read = source_download._cache_read
        calls = 0
        def miss_then_existing(*args):
            nonlocal calls
            calls += 1
            return None if calls == 1 else real_read(*args)
        with patch.object(source_download, "_cache_read", side_effect=miss_then_existing), \
                patch.object(source_download.urllib.request, "urlopen", return_value=CacheResponse(b"different response", self.URL)):
            with self.assertRaises(ValueError):
                source_download.read_source(self.URL, max_bytes=100)
        self.assertEqual(path.read_bytes(), before)
        with patch.object(source_download.urllib.request, "urlopen") as opened:
            self.assertEqual(source_download.read_source(self.URL, max_bytes=100), original)
            opened.assert_not_called()

    def test_empty_target_appearing_at_publication_is_preserved_and_rejected(self):
        actual = source_download._cache_rename_noreplace
        target_identity = []
        def concurrent_empty(directory_fd, source, target):
            os.mkdir(target, mode=0o700, dir_fd=directory_fd)
            target_identity.append(os.stat(target, dir_fd=directory_fd).st_ino)
            return actual(directory_fd, source, target)
        with patch.object(source_download, "_cache_rename_noreplace", side_effect=concurrent_empty), \
                patch.object(source_download.urllib.request, "urlopen", return_value=CacheResponse(b"complete", self.URL)):
            with self.assertRaisesRegex(ValueError, "entry is incomplete"):
                source_download.read_source(self.URL, max_bytes=100)
        self.assertEqual(self.entry().stat().st_ino, target_identity[0])
        self.assertEqual(list(self.entry().iterdir()), [])
        pending = [p for p in self.cache.iterdir() if p.name.startswith(".pending-")]
        self.assertEqual(len(pending), 1)
        self.assertTrue((pending[0] / "source").is_file())
        self.assert_bad_without_network()

    def test_nonlinux_opt_in_fails_without_fallback(self):
        with patch.object(source_download.sys, "platform", "darwin"), \
                patch.object(source_download.urllib.request, "urlopen") as opened:
            with self.assertRaises(OSError) as caught:
                source_download.read_source(self.URL, max_bytes=100)
            self.assertEqual(caught.exception.errno, errno.ENOSYS)
            opened.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_missing_renameat2_fails_without_replace_fallback(self):
        with patch.object(ctypes, "CDLL", return_value=object()), \
                patch.object(source_download.urllib.request, "urlopen", return_value=CacheResponse(b"complete", self.URL)) as opened:
            with self.assertRaises(OSError) as caught:
                source_download.read_source(self.URL, max_bytes=100)
            self.assertEqual(caught.exception.errno, errno.ENOSYS)
            self.assertEqual(opened.call_count, 1)
        self.assertFalse(self.entry().exists())
        pending = list(self.cache.iterdir())
        self.assertEqual(len(pending), 1)
        self.assertTrue((pending[0] / "source").is_file())


if __name__ == "__main__":
    unittest.main()
