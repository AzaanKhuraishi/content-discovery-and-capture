"""Versioned domain contracts; source observations are data, never instructions."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, unquote
import fnmatch
import json
import re
import uuid

SCHEMA_VERSION = 1


class CaptureError(Exception):
    """Safe, user-readable error. Never wrap unsanitised provider exceptions."""


class AccessRequired(CaptureError):
    pass


class RuntimeActionRequired(AccessRequired):
    pass


class BudgetExceeded(CaptureError):
    pass


class ApprovalRequired(CaptureError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value: Any) -> str:
    return sha256(canonical(value).encode()).hexdigest()


SECRET_KEY = re.compile(r"(?i)(token|password|secret|signature|credential|authorization|cookie|session|api.?key|^sig$|^key$|^auth$|^x-amz-|^x-goog-)")


def safe_url(url: str) -> str:
    p = urlsplit(url)
    host = p.hostname or ""
    if ":" in host:
        host = "[" + host + "]"
    if p.port:
        host += ":" + str(p.port)
    return urlunsplit((p.scheme.lower(), host.lower(), p.path or "/",
                      urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                                 if not SECRET_KEY.search(k)]), ""))


def stable_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise CaptureError("Use an HTTP or HTTPS source location.")
    if p.username or p.password or p.fragment or any(SECRET_KEY.search(k) for k, _ in parse_qsl(p.query)):
        raise CaptureError("Register a stable location without credentials, fragments or signed access parameters.")
    return safe_url(url)


def safe_text(value: str) -> str:
    value = re.sub(r'https?://[^\s<>"\']+', lambda m: safe_url(m[0]), value)
    value = re.sub(r'(?i)\b(bearer)\s+[^\s,;]+', r'\1 [REDACTED]', value)
    return re.sub(r'(?i)\b(password|token|secret|signature|cookie|authorization)\s*[:=]\s*[^\s,;]+',
                  r'\1=[REDACTED]', value)


def safe_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if SECRET_KEY.search(k) else safe_data(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_data(v) for v in value]
    return safe_text(value) if isinstance(value, str) else value


@dataclass(frozen=True)
class Scope:
    roots: tuple[str, ...]
    excludes: tuple[str, ...] = ()
    asset_origins: tuple[str, ...] = ()
    max_depth: int = 20
    allow_private_network: bool = False

    def __post_init__(self):
        if (not self.roots or type(self.max_depth) is not int or not 0 <= self.max_depth <= 100
                or type(self.allow_private_network) is not bool
                or any(type(value) is not str or not value for group in (self.roots, self.excludes, self.asset_origins) for value in group)):
            raise CaptureError("Provide explicit roots and a depth between 0 and 100.")

    @classmethod
    def from_dict(cls, data):
        if (set(data) - {"roots", "excludes", "asset_origins", "max_depth", "allow_private_network"}
                or "roots" not in data
                or any(type(data[key]) is not list for key in ("roots", "excludes", "asset_origins") if key in data)):
            raise CaptureError("Scope requires lists of roots, exclusions and asset origins with supported options only.")
        return cls(tuple(data["roots"]), tuple(data.get("excludes", ())),
                   tuple(data.get("asset_origins", ())), data.get("max_depth", 20),
                   data.get("allow_private_network", False))

    def data(self):
        return json.loads(canonical(asdict(self)))

    def permits(self, location: str, kind: str, asset: bool = False) -> bool:
        if any(fnmatch.fnmatch(location, pattern) for pattern in self.excludes):
            return False
        if kind == "filesystem":
            path = Path(location).resolve()
            return any(path == Path(root).resolve() or path.is_relative_to(Path(root).resolve()) for root in self.roots)
        p = urlsplit(location)
        if p.scheme not in ("https", "http") or p.username or p.password:
            return False
        origin = f"{p.scheme}://{p.netloc}"
        if asset and origin in self.asset_origins:
            return True
        for root in self.roots:
            r = urlsplit(root)
            path, base = unquote(p.path), unquote(r.path).rstrip("/")
            # Do not let encoded traversal or an unrelated collection query widen scope.
            if ".." in path.split("/"):
                continue
            if (p.scheme, p.netloc) == (r.scheme, r.netloc) and (path == base or path.startswith(base + "/")):
                required = set(parse_qsl(r.query, keep_blank_values=True))
                if required.issubset(set(parse_qsl(p.query, keep_blank_values=True))):
                    return True
        return False


@dataclass(frozen=True)
class Budget:
    max_items: int = 10000
    max_bytes: int = 25 * 1024 * 1024
    max_seconds: int = 900
    max_actions: int = 2000
    sample_bytes: int = 256 * 1024

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in asdict(self).values()):
            raise CaptureError("All budget limits must be positive integers.")


@dataclass
class Observation:
    identity: str
    location: str
    title: str
    kind: str = "file"
    media_type: str = "application/octet-stream"
    size: int | None = None
    metadata: dict = field(default_factory=dict)
    content_hash: str | None = None
    parent: str | None = None
    relation: str = "contains"
    external: bool = False
    needs_resolution: bool = False
    observed_at: str = field(default_factory=now)

    def data(self):
        return safe_data(asdict(self))


@dataclass
class DiscoveryBatch:
    observations: list[Observation] = field(default_factory=list)
    frontier: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    bytes_read: int = 0
    actions: int = 1


@dataclass
class CaptureResult:
    path: Path
    media_type: str
    role: str = "original"
    limitations: list[str] = field(default_factory=list)
    accessed_at: str = field(default_factory=now)
