"""Bounded HTTP transport; credentials and redirect URLs never enter persisted logs."""
from __future__ import annotations
import importlib.util
import http.client
import os
from pathlib import Path
import ipaddress
import socket
import time
import re
from urllib.parse import urlsplit
from urllib.request import (Request, HTTPRedirectHandler, HTTPHandler, HTTPSHandler,
                            ProxyHandler, build_opener)
from urllib.error import HTTPError, URLError

from .contract import WebResponse
from ..domain import AccessRequired, BudgetExceeded, CaptureError


class HttpFailure(CaptureError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Source returned HTTP {status}.")


def _ambiguous_numeric_host(host: str) -> bool:
    """Reject legacy integer/octal/hex IPv4 spellings with parser-dependent meaning."""
    if not host or not re.fullmatch(r"[0-9a-fA-FxX.]+", host) or ":" in host:
        return False
    try:
        return socket.inet_ntoa(socket.inet_aton(host)) != host
    except OSError:
        return False


def capabilities(browser=None):
    return {"filesystem": True, "http": True, "browser": browser.capabilities() if browser else {},
            "converters": {name: importlib.util.find_spec(module) is not None for name, module in
                           [("docx", "docx"), ("pptx", "pptx"), ("pdf", "pdfplumber"), ("transcript", "faster_whisper")]},
            "transcription_model_available": bool(os.environ.get("CDC_TRANSCRIPTION_MODEL")) and Path(os.environ.get("CDC_TRANSCRIPTION_MODEL", "/nonexistent")).is_dir(),
            "model_levels": {}, "observed": "Actual imports and explicitly supplied runtime provider"}


def _resolve_addresses(url, scope, asset=False):
    if not scope.permits(url, "web", asset=asset):
        raise CaptureError("Web location is outside the approved boundary.")
    try:
        p = urlsplit(url)
        if _ambiguous_numeric_host(p.hostname or ""):
            raise CaptureError("Ambiguous numeric host representations are not allowed.")
        port = p.port if p.port is not None else (443 if p.scheme == "https" else 80)
        addresses = socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM)
    except CaptureError:
        raise
    except (OSError, TypeError, ValueError):
        raise CaptureError("Host could not be resolved.") from None
    if not addresses:
        raise CaptureError("Host could not be resolved.")
    if not scope.allow_private_network:
        try:
            private = any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses)
        except ValueError:
            private = True
        if private:
            raise CaptureError("Private network access is not included in this source scope.")
    return addresses


def check_url(url, scope, asset=False):
    """Validate scope and resolve once for the connection that will be made."""
    return _resolve_addresses(url, scope, asset)


def _connect_pinned(addresses, timeout, source_address=None):
    last_error = None
    for family, socktype, proto, _canonname, sockaddr in addresses:
        sock = socket.socket(family, socktype, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as error:
            last_error = error
            sock.close()
    if last_error:
        raise last_error
    raise OSError("No usable address")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, addresses, **kwargs):
        self._pinned_addresses = addresses
        super().__init__(host, **kwargs)

    def connect(self):
        self.sock = _connect_pinned(self._pinned_addresses, self.timeout, self.source_address)
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, addresses, **kwargs):
        self._pinned_addresses = addresses
        super().__init__(host, **kwargs)

    def connect(self):
        sock = _connect_pinned(self._pinned_addresses, self.timeout, self.source_address)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(HTTPHandler):
    def __init__(self, scope, asset):
        super().__init__()
        self.scope, self.asset = scope, asset

    def http_open(self, req):
        addresses = check_url(req.full_url, self.scope, self.asset)
        return self.do_open(lambda host, **kwargs: _PinnedHTTPConnection(host, addresses, **kwargs), req)


class _PinnedHTTPSHandler(HTTPSHandler):
    def __init__(self, scope, asset):
        super().__init__()
        self.scope, self.asset = scope, asset

    def https_open(self, req):
        addresses = check_url(req.full_url, self.scope, self.asset)
        return self.do_open(lambda host, **kwargs: _PinnedHTTPSConnection(host, addresses, **kwargs), req,
                            context=self._context)


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
            # Ignore ambient HTTP(S)_PROXY settings: a proxy can otherwise reach a
            # private target after the local preflight has passed.
            opener = build_opener(ProxyHandler({}), ScopedRedirect(scope, asset),
                                  _PinnedHTTPHandler(scope, asset), _PinnedHTTPSHandler(scope, asset))
            return opener.open(req, timeout=timeout)
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
