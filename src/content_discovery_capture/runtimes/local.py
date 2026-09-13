"""Bounded HTTP transport; credentials and redirect URLs never enter persisted logs."""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import ipaddress
import socket
import time
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError

from .contract import WebResponse
from ..domain import AccessRequired, BudgetExceeded, CaptureError


class HttpFailure(CaptureError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Source returned HTTP {status}.")


def capabilities(browser=None):
    return {"filesystem": True, "http": True, "browser": browser.capabilities() if browser else {},
            "converters": {name: importlib.util.find_spec(module) is not None for name, module in
                           [("docx", "docx"), ("pptx", "pptx"), ("pdf", "pdfplumber"), ("transcript", "faster_whisper")]},
            "transcription_model_available": bool(os.environ.get("CDC_TRANSCRIPTION_MODEL")) and Path(os.environ.get("CDC_TRANSCRIPTION_MODEL", "/nonexistent")).is_dir(),
            "model_levels": {}, "observed": "Actual imports and explicitly supplied runtime provider"}


def check_url(url, scope, asset=False):
    if not scope.permits(url, "web", asset=asset):
        raise CaptureError("Web location is outside the approved boundary.")
    p = urlsplit(url)
    if not scope.allow_private_network:
        try:
            addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
        except OSError:
            raise CaptureError("Host could not be resolved.") from None
        if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise CaptureError("Private network access is not included in this source scope.")


class ScopedRedirect(HTTPRedirectHandler):
    def __init__(self, scope, asset):
        self.scope, self.asset = scope, asset

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.scope, self.asset)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpProvider:
    def open(self, url, scope, method="GET", headers=None, asset=False, timeout=30):
        check_url(url, scope, asset)
        req = Request(url, method=method, headers={"User-Agent": "ContentDiscoveryCapture/0.1", **(headers or {})})
        try:
            return build_opener(ScopedRedirect(scope, asset)).open(req, timeout=timeout)
        except HTTPError as error:
            if error.code in (401, 403):
                raise AccessRequired("Authentication or additional source access is required.") from None
            raise HttpFailure(error.code) from None
        except (URLError, TimeoutError, OSError):
            raise CaptureError("Web request failed or timed out.") from None

    def inspect(self, url, scope, limit, asset=False):
        # Binary discovery uses HEAD; only HTML navigation consumes bounded body samples.
        try:
            with self.open(url, scope, "HEAD", asset=asset) as response:
                headers = dict(response.headers.items())
                final = response.geturl()
        except HttpFailure as error:
            if error.status not in (405, 501):
                raise
            with self.open(url, scope, asset=asset) as response:
                headers = dict(response.headers.items())
                media = headers.get("Content-Type", "").split(";")[0]
                sample = limit if media in ("text/html", "application/xhtml+xml") else min(limit, 4096)
                body = response.read(sample + 1)
                return WebResponse(body[:sample], response.geturl(), headers,
                    complete=len(body) <= sample if "html" in media else True,
                    limitations=["HEAD unsupported; used bounded GET inspection"])
        media = headers.get("Content-Type", "").split(";")[0]
        previous = getattr(self, "previous", {}).get(url)
        etag = headers.get("ETag")
        if (previous and etag and not etag.startswith("W/") and etag == previous["metadata"].get("etag")
                and not previous["metadata"].get("dynamic") and "navigation" in previous["metadata"]):
            headers["X-CDC-Reused"] = "true"
            return WebResponse(b"", final, headers)
        if media not in ("text/html", "application/xhtml+xml"):
            return WebResponse(b"", final, headers)
        with self.open(url, scope, asset=asset) as response:
            body = response.read(limit + 1)
            return WebResponse(body[:limit], response.geturl(), dict(response.headers.items()), len(body) <= limit)

    def download(self, url, scope, destination, max_bytes, timeout, asset=False):
        start, size = time.monotonic(), 0
        with self.open(url, scope, asset=asset, timeout=min(timeout, 30)) as response, destination.open("xb") as stream:
            while chunk := response.read(min(1024 * 1024, max_bytes - size + 1)):
                size += len(chunk)
                if size > max_bytes or time.monotonic() - start > timeout:
                    raise BudgetExceeded("Download reached its approved budget.")
                stream.write(chunk)
            headers = dict(response.headers.items())
            if headers.get("Content-Length") and int(headers["Content-Length"]) != size:
                raise CaptureError("Download length does not match the response.")
            return headers, response.geturl()
