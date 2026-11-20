import asyncio
import ipaddress
import re
import socket
from urllib.parse import parse_qsl, unquote, urlencode

import httpx

PUBLIC_PARAMETERS = {
    "page",
    "p",
    "offset",
    "limit",
    "size",
    "pagesize",
    "page_size",
    "start",
    "rows",
    "perpage",
    "per_page",
    "$skip",
    "$top",
    "skip",
    "top",
    "first",
    "lang",
    "locale",
    "language",
    "format",
    "status",
    "sort",
    "order",
    "type",
    "id",
    "noticeid",
    "notice_id",
    "tenderid",
    "tender_id",
    "version",
    "year",
    "cpv",
    "country",
    "region",
    "noticetype",
    "notice_type",
    "proceduretype",
    "procedure_type",
    "publicationyear",
    "publication_year",
    "publicationdate",
    "publication_date",
    "deadline",
    "$format",
    "$count",
}
SECRET_KEY = re.compile(
    r"^authorization$|auth(?:entication|orization)?[_-]?(?:key|token)|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"token|password|passwd|cookie|secret|credential|session(?:id|[_-]id)?|"
    r"signature|x-amz|x-goog|csrf|bearer|(?:^|[_-])(?:key|sig)(?:$|[_-])",
    re.I,
)
SECRET_TEXT = re.compile(
    r"(?i)\b(?:bearer\s+\S+|(?:sk|ghp|github_pat|xox[baprs])[-_][\w-]{12,}|"
    r"eyJ[\w-]{8,}\.[\w-]+\.[\w-]+|(?:authorization|password|token|api[_ -]?key|secret|cookie)"
    r"""["']?\s*[:=]\s*["']?[^\s"',;&<>]+)"""
)
URL_IN_TEXT = re.compile(r"""https?://[^\s"'<>\\]+""", re.I)
PUBLIC_VALUE = re.compile(r"[\w.,:+-]{1,64}", re.ASCII)
OPAQUE = re.compile(r"(?=[a-z0-9_-]{26,}$)(?=.*[a-z])(?=.*[0-9])[a-z0-9_-]+", re.I)
PUBLIC_UUID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", re.I)


class UnsafeURL(ValueError):
    pass


def _query(url: httpx.URL) -> list[tuple[str, str]]:
    return parse_qsl(url.query.decode("ascii"), keep_blank_values=True, errors="strict", max_num_fields=32)


def _public_param(key: str, value: str) -> bool:
    return (
        key.lower() in PUBLIC_PARAMETERS
        and (not value or bool(PUBLIC_VALUE.fullmatch(value)))
        and not SECRET_TEXT.search(value)
        and (not OPAQUE.fullmatch(value) or bool(PUBLIC_UUID.fullmatch(value)))
    )


def _public_host(host: str) -> bool:
    try:
        return is_public_ip(host)
    except ValueError:
        return "." in host and not host.endswith(
            (".localhost", ".local", ".internal", ".home.arpa", ".test", ".invalid", ".onion")
        )


def normalize_url(value: str) -> str:
    value = value.strip()
    if "://" not in value and not value.startswith(("/", "\\")):
        value = "https://" + value
    if len(value) > 2048 or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise UnsafeURL("URL contains an unsupported character or is too long")
    try:
        url = httpx.URL(value)
        host = url.raw_host.decode("ascii").lower().rstrip(".")
        if url.scheme not in {"http", "https"} or not host or url.userinfo:
            raise UnsafeURL("Only credential-free HTTP and HTTPS URLs are allowed")
        if url.port not in {None, 80 if url.scheme == "http" else 443}:
            raise UnsafeURL("Only the default port for the URL scheme is allowed")
        if not _public_host(host):
            raise UnsafeURL("Destination is not a public host")
        path = url.raw_path.partition(b"?")[0].decode("ascii")
        if re.search(r"%(?:00|0a|0d|2f|5c|25)", path, re.I):
            raise UnsafeURL("URL path contains ambiguous or unsafe encoding")
        parts = unquote(path).split("/")
        if any(
            part in {".", ".."}
            or SECRET_TEXT.search(part)
            or (OPAQUE.fullmatch(part) and not PUBLIC_UUID.fullmatch(part))
            or (i and SECRET_KEY.fullmatch(parts[i - 1]) and part)
            for i, part in enumerate(parts)
        ):
            raise UnsafeURL("URL path contains ambiguous segments or credentials")
        params = _query(url)
        if any(not _public_param(k, v) for k, v in params):
            raise UnsafeURL("URL has query parameters outside the public parameter allowlist")
        query = urlencode(params).encode("ascii")
        return str(
            url.copy_with(host=host, raw_path=path.encode() + (b"?" + query if query else b""), fragment=None)
        )
    except UnsafeURL:
        raise
    except (ValueError, UnicodeError, httpx.InvalidURL) as exc:
        raise UnsafeURL("Malformed URL") from exc


def origin(url: str) -> str:
    return str(httpx.URL(url).copy_with(path="/", query=None, fragment=None)).rstrip("/")


def is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return is_public_ip(str(ip.ipv4_mapped))
        if ip.sixtofour or ip.teredo or ip in ipaddress.ip_network("64:ff9b::/96"):
            return False
    return ip.is_global and not (
        ip.is_multicast or ip.is_reserved or ip.is_site_local
        if isinstance(ip, ipaddress.IPv6Address)
        else ip.is_multicast or ip.is_reserved
    )


async def resolve_public(host: str, port: int) -> str:
    try:
        ipaddress.ip_address(host)
        addresses = {host}
    except ValueError:
        result = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = {item[4][0] for item in result}
    if not addresses or not all(is_public_ip(ip) for ip in addresses):
        raise UnsafeURL("DNS returned a non-public destination")
    return min(addresses, key=lambda ip: (":" in ip, ip))


def safe_url(value: str) -> str:
    try:
        return normalize_url(value)
    except UnsafeURL:
        try:
            url = httpx.URL(value)
            if not url.userinfo and url.is_absolute_url:
                params = [(k, v) for k, v in _query(url) if _public_param(k, v)]
                return normalize_url(
                    str(url.copy_with(query=urlencode(params).encode() or None, fragment=None))
                )
        except (httpx.InvalidURL, ValueError, UnicodeError):
            pass
    return "[redacted URL]"


def clean_text(text: str) -> str:
    text = URL_IN_TEXT.sub(lambda match: safe_url(match.group()), text)
    text = SECRET_TEXT.sub("[redacted]", text)
    return re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()


def sanitize(value, depth: int = 0):
    if depth > 24:
        return None
    if isinstance(value, dict):
        return {
            clean_text(str(key))[:250]: sanitize(item, depth + 1)
            for key, item in value.items()
            if not SECRET_KEY.search(str(key))
            or key
            in {
                "input_tokens",
                "output_tokens",
                "max_input_tokens",
                "max_output_tokens",
                "captcha_detected",
                "authentication_required",
                "api_authentication_required",
            }
        }
    if isinstance(value, list):
        return [sanitize(item, depth + 1) for item in value]
    if isinstance(value, str):
        return clean_text(value)
    return value


def candidate_url(base: str, value: str) -> str | None:
    if not value or value.startswith("#"):
        return None
    try:
        return normalize_url(str(httpx.URL(base).join(value)))
    except (UnsafeURL, httpx.InvalidURL, ValueError):
        return None
