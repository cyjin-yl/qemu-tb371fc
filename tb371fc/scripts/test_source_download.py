#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise real HTTP failures and verifier integrity without public network I/O."""
from argparse import Namespace
from collections import Counter
from contextlib import contextmanager
from email.utils import formatdate
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
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
            return original_open(server.url + "/" + path, **kwargs)
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


if __name__ == "__main__":
    unittest.main()
