import asyncio
import ipaddress
import logging
import os
import re
import socket
import time
import uuid
from email.message import Message
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import httpx
from bs4 import BeautifulSoup

from . import db

log = logging.getLogger("watchtower.fetch")
USER_AGENT = os.environ.get("WATCHTOWER_USER_AGENT", "JimixWatchtower/0.1 (public tender monitoring)")
INTERVAL = 1.0
MB = 1024 * 1024
MAX_FILE_BYTES = int(os.environ.get("WATCHTOWER_MAX_FILE_MB", "250")) * MB
MAX_RUN_DOWNLOAD_BYTES = int(os.environ.get("WATCHTOWER_MAX_RUN_DOWNLOAD_MB", "1000")) * MB
OVERSIZE_SKIP = "over size limit"
DOCUMENT_HTML_PROBE_BYTES = 64 * 1024
CAPTCHA_WIDGETS = (
    ".g-recaptcha:not([data-size=invisible]), .h-captcha:not([data-size=invisible]), .cf-turnstile,"
    " #challenge-form, #challenge-running, #cf-challenge-running, .cf-browser-verification,"
    " iframe[src*=hcaptcha], iframe[src*='recaptcha/api2/bframe'], iframe[src*=turnstile],"
    " script[src*=captcha-delivery], iframe[src*=captcha-delivery]"
)
CHALLENGE_TEXT = re.compile(
    r"captcha|verify (that )?you are (a )?human|checking your browser|are you a robot|not a robot"
)
CAPTCHA_NOTICE = re.compile(r"protected by (re)?captcha|recaptcha privacy|recaptcha terms")
LOGIN_PATH = re.compile(r"/(login|signin|sign-in|anmelden|sso|oauth|auth)(\b|/)", re.IGNORECASE)


class Blocked(Exception):
    pass


class Deleted(BaseException):
    pass


class BudgetExceeded(Exception):
    pass


class DocumentSkipped(Exception):
    def __init__(self, reason: str, *, retryable: bool):
        super().__init__(reason)
        self.retryable = retryable


class _Gate:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.last = 0.0


_gates: dict[str, _Gate] = {}
SECOND_LEVEL = {"co", "com", "gov", "org", "net", "ac", "edu", "gob", "gouv"}


def site(url: str) -> str:
    labels = (urlsplit(url).hostname or "").split(".")
    keep = 3 if len(labels) >= 3 and labels[-2] in SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


async def wait_turn(url: str) -> None:
    gate = _gates.setdefault(site(url), _Gate())
    async with gate.lock:
        delay = gate.last + INTERVAL - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        gate.last = time.monotonic()


async def resolve_public(host: str, port: int) -> str:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError(f"{host} is not a public address")
    return min(addresses, key=lambda a: (":" in a, a))


async def check_public(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not an http(s) URL: {url}")
    await resolve_public(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))


def block_reason(status: int, headers, requested: str, final: str, html: str | None) -> str | None:
    if status in (401, 407):
        return f"login required (HTTP {status})"
    if status == 403:
        return "access denied (HTTP 403)"
    if headers.get("cf-mitigated") == "challenge":
        return "bot challenge"
    if (waf := headers.get("x-amzn-waf-action")) in ("challenge", "captcha"):
        return f"bot challenge (AWS WAF {waf})"
    if LOGIN_PATH.search(urlsplit(final).path) and not LOGIN_PATH.search(urlsplit(requested).path):
        return "login required (redirected to a login page)"
    if html is None:
        return None
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    if len(text) > 1500:
        return None
    for badge in soup.select(".grecaptcha-badge"):
        badge.decompose()
    if widget := soup.select_one(CAPTCHA_WIDGETS):
        label = " ".join([widget.name, *(widget.get("class") or [widget.get("id") or ""])]).strip()
        return f"captcha ({label[:60]})"
    if CHALLENGE_TEXT.search(CAPTCHA_NOTICE.sub("", text.lower())):
        return "captcha (page text)"
    if soup.select_one("input[type=password]"):
        return "login required (login form)"
    return None


UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _canon(text: str) -> str:
    def decode(m):
        char = chr(int(m[0][1:], 16))
        return char if char in UNRESERVED else m[0].upper()

    return re.sub(r"%[0-9a-fA-F]{2}", decode, quote(text, safe="!#$%&'()*+,/:;=?@[]~"))


def parse_robots(text: str, agent: str = USER_AGENT) -> list[tuple[str, bool]]:
    groups, current, in_agents = [], None, False
    for line in text.lstrip("\ufeff").split("\n"):
        key, _, value = line.split("#", 1)[0].partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if not in_agents:
                current = ([], [])
                groups.append(current)
            if value:
                current[0].append(value.lower())
            in_agents = True
        elif key in ("allow", "disallow") and current is not None:
            in_agents = False
            if value:
                current[1].append((_canon(value), key == "allow"))
    name = agent.split("/")[0].lower()
    own = [rules for agents, rules in groups if name in agents]
    chosen = own or [rules for agents, rules in groups if "*" in agents]
    return [rule for rules in chosen for rule in rules]


def _rule_matches(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    parts = pattern.removesuffix("$").split("*")
    if not path.startswith(parts[0]):
        return False
    pos = len(parts[0])
    if len(parts) == 1:
        return not anchored or pos == len(path)
    for part in parts[1:-1]:
        pos = path.find(part, pos)
        if pos < 0:
            return False
        pos += len(part)
    if anchored:
        return path.endswith(parts[-1]) and len(path) - len(parts[-1]) >= pos
    return path.find(parts[-1], pos) >= 0


def robots_allows(rules: list[tuple[str, bool]], url: str) -> bool:
    parts = urlsplit(url)
    path = _canon((parts.path or "/") + (f"?{parts.query}" if parts.query else ""))
    best = None
    for pattern, allow in rules:
        if _rule_matches(pattern, path) and (best is None or (len(pattern), allow) > best):
            best = (len(pattern), allow)
    return best is None or best[1]


def _file_name(resp: httpx.Response) -> str:
    disposition = resp.headers.get("content-disposition", "")
    if extended := re.search(r"filename\*\s*=\s*([\w-]+)'[^']*'([^;\s]+)", disposition, re.IGNORECASE):
        try:
            return unquote(extended.group(2), encoding=extended.group(1), errors="replace")
        except LookupError:
            pass
    msg = Message()
    msg["content-disposition"] = disposition
    return unquote(msg.get_filename() or resp.url.path.rsplit("/", 1)[-1]) or "document"


class Fetcher:
    def __init__(self, portal_id: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.portal_id = portal_id
        self.client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=True,
            timeout=30,
            headers={"User-Agent": USER_AGENT},
            event_hooks={"request": [self._before_request], "response": [self._after_response]},
        )
        self.check_hosts = transport is None
        self.robots: dict[str, list[tuple[str, bool]]] = {}
        self.playwright = None
        self.browser = None
        self.blocks: list[str] = []
        self.downloaded = 0
        self.assets: dict[str, tuple] = {}

    def blocked(self, reason: str, url: str = "") -> Blocked:
        log.info("blocked %s: %s", url, reason)
        self.blocks.append(reason)
        return Blocked(reason)

    async def _before_request(self, request: httpx.Request) -> None:
        if self.portal_id:
            with db.connect() as conn:
                if not conn.execute("SELECT 1 FROM portal WHERE id = ?", (self.portal_id,)).fetchone():
                    raise Deleted(self.portal_id)
        if self.check_hosts:
            await check_public(str(request.url))
        await wait_turn(str(request.url))

    async def _after_response(self, response: httpx.Response) -> None:
        log.info("%s %s -> %s", response.request.method, response.request.url, response.status_code)

    async def close(self) -> None:
        await self.client.aclose()
        if self.browser:
            await self.browser.close()
            await self.playwright.stop()

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(f"not an absolute http(s) URL: {url!r}")
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self.robots:
            try:
                resp = await self.client.get(root + "/robots.txt")
            except httpx.HTTPError:
                rules = []
            else:
                if resp.status_code in (401, 403):
                    rules = [("/", False)]
                elif resp.status_code >= 400 or resp.text.lstrip("\ufeff \t\r\n").startswith("<"):
                    rules = []
                else:
                    rules = parse_robots(resp.text)
            self.robots[root] = rules
        if not robots_allows(self.robots[root], url):
            log.info("robots.txt of %s disallows %s", root, url)
            return False
        return True

    async def get(self, url: str) -> httpx.Response:
        if not await self.allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        try:
            resp = await self.client.get(url)
        except httpx.TransportError:
            resp = await self.client.get(url)
        html = resp.text if "html" in resp.headers.get("content-type", "") else None
        if reason := block_reason(resp.status_code, resp.headers, url, str(resp.url), html):
            raise self.blocked(reason, url)
        resp.raise_for_status()
        return resp

    async def download(self, url: str, folder: Path) -> tuple[Path, str]:
        limit = min(MAX_FILE_BYTES, MAX_RUN_DOWNLOAD_BYTES - self.downloaded)
        if limit <= 0:
            raise BudgetExceeded
        if not await self.allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        part = folder / f".part-{uuid.uuid4().hex}"
        size = 0
        saved = False
        try:
            async with self.client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as resp:
                is_html = "html" in resp.headers.get("content-type", "").lower()
                if reason := block_reason(resp.status_code, resp.headers, url, str(resp.url), None):
                    raise self.blocked(reason)
                resp.raise_for_status()
                if is_html:
                    # Bound the wire prefix and avoid decoding compressed HTML into unbounded text.
                    if resp.headers.get("content-encoding", "identity").lower() == "identity":
                        prefix = bytearray()
                        probe_limit = min(limit, DOCUMENT_HTML_PROBE_BYTES)
                        if resp.is_stream_consumed:
                            prefix.extend(resp.content[:probe_limit])
                            size = len(resp.content)
                        else:
                            async for chunk in resp.aiter_raw():
                                size += len(chunk)
                                prefix.extend(chunk[:probe_limit - len(prefix)])
                                if len(prefix) == probe_limit:
                                    break
                        if reason := block_reason(
                            resp.status_code, resp.headers, url, str(resp.url), prefix.decode(errors="replace")
                        ):
                            raise self.blocked(reason)
                    raise DocumentSkipped("HTML response", retryable=True)
                with part.open("wb") as file:
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            break
                        file.write(chunk)
            if size > limit:
                if limit < MAX_FILE_BYTES:
                    raise BudgetExceeded
                raise DocumentSkipped(OVERSIZE_SKIP, retryable=False)
            name = _file_name(resp)
            saved = True
            return part, name
        finally:
            self.downloaded += min(size, limit)
            if not saved:
                part.unlink(missing_ok=True)
