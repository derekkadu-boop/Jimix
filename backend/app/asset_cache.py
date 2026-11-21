import time
from collections import OrderedDict
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from urllib.request import parse_http_list

MAX_ASSET_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_ASSETS = 256
MAX_FRESH_SECONDS = 6 * 60 * 60
SCRIPT_TYPES = {"text/javascript", "application/javascript", "application/x-javascript", "text/ecmascript"}
CORS_HEADERS = {"origin", "access-control-request-method", "access-control-request-headers"}
UNSHAREABLE_HEADERS = {
    "authorization", "cookie", "range", "if-range",
    "if-match", "if-none-match", "if-modified-since", "if-unmodified-since",
}


def same_origin(left: str, right: str) -> bool:
    a, b = urlsplit(left), urlsplit(right)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


def shareable_request(url: str, headers) -> bool:
    parts = urlsplit(url)
    return (
        len(url) <= 2048
        and not (parts.query or parts.fragment or parts.username or parts.password)
        and not any(h in headers for h in UNSHAREABLE_HEADERS)
        and not headers.get("cache-control")
        and not headers.get("pragma")
    )


@dataclass(frozen=True)
class Asset:
    body: bytes
    headers: dict[str, str]
    cors_vary: frozenset[str]
    time_stored: float
    age_at_storage: int
    fresh_for: int

    def age(self) -> int:
        return self.age_at_storage + int(time.monotonic() - self.time_stored)

    def response_headers(self) -> dict[str, str]:
        return self.headers | {"age": str(self.age())}


class AssetCache:
    def __init__(self):
        self.entries: OrderedDict[tuple[str, str], Asset] = OrderedDict()
        self.bytes = 0

    def get(self, url: str, kind: str, request_headers=()) -> Asset | None:
        key = url, kind
        asset = self.entries.get(key)
        if asset and asset.age() < asset.fresh_for:
            if any(header in request_headers for header in asset.cors_vary):
                return None
            self.entries.move_to_end(key)
            return asset
        if asset:
            self.bytes -= len(self.entries.pop(key).body)
        return None

    def save(self, url: str, kind: str, headers, body: bytes, request_headers=()) -> None:
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in (SCRIPT_TYPES if kind == "script" else {"text/css"}):
            return
        if ("set-cookie" in headers or len(body) > MAX_ASSET_BYTES
                or headers.get("content-encoding", "identity").strip().lower() != "identity"):
            return
        vary = {part.strip().lower() for part in headers.get("vary", "").split(",") if part.strip()}
        cors_vary = frozenset(vary & CORS_HEADERS)
        if vary - CORS_HEADERS - {"accept-encoding"} or any(header in request_headers for header in cors_vary):
            return
        directives = {}
        for value in parse_http_list(headers.get("cache-control", "").lower()):
            name, _, argument = value.strip().partition("=")
            if name in directives:
                return
            directives[name] = argument.strip().strip('"')
        if {"private", "no-cache", "no-store"} & directives.keys():
            return
        age = headers.get("age", "0")
        lifetime = directives.get("s-maxage", directives.get("max-age", ""))
        if not (age.isascii() and age.isdecimal() and len(age) <= 10 and lifetime.isascii()
                and lifetime.isdecimal() and len(lifetime) <= 10):
            return
        age = int(age)
        if date := headers.get("date"):
            try:
                age = max(age, int(time.time() - parsedate_to_datetime(date).timestamp()))
            except (ValueError, OverflowError, OSError):
                return
        freshness = min(int(lifetime), MAX_FRESH_SECONDS)
        if age >= freshness:
            return
        asset_headers = {
            key: value for key, value in headers.items()
            if key not in {"content-length", "content-encoding", "transfer-encoding", "connection"}
        }
        if sum(len(key) + len(value) for key, value in asset_headers.items()) > 16 * 1024:
            return
        key = url, kind
        if previous := self.entries.pop(key, None):
            self.bytes -= len(previous.body)
        self.entries[key] = Asset(body, asset_headers, cors_vary, time.monotonic(), age, freshness)
        self.bytes += len(body)
        while self.bytes > MAX_CACHE_BYTES or len(self.entries) > MAX_ASSETS:
            _, removed = self.entries.popitem(last=False)
            self.bytes -= len(removed.body)


PUBLIC_ASSETS = AssetCache()
