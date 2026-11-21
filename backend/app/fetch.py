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
from urllib.parse import unquote, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from . import db

log = logging.getLogger("watchtower.fetch")
USER_AGENT = os.environ.get("WATCHTOWER_USER_AGENT", "JimixWatchtower/0.1 (public tender monitoring)")
INTERVAL = 1.0
MB = 1024 * 1024
MAX_FILE_BYTES = int(os.environ.get("WATCHTOWER_MAX_FILE_MB", "250")) * MB
MAX_RUN_DOWNLOAD_BYTES = int(os.environ.get("WATCHTOWER_MAX_RUN_DOWNLOAD_MB", "1000")) * MB
CAPTCHA_MARKERS = ("g-recaptcha", "h-captcha", "cf-turnstile", "captcha")
LOGIN_PATH = re.compile(r"/(login|signin|sign-in|anmelden|sso|oauth|auth)(\b|/)", re.IGNORECASE)


class Blocked(Exception):
    pass


class Deleted(BaseException):
    pass


class BudgetExceeded(Exception):
    pass


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


async def check_public(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not an http(s) URL: {url}")
    infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, None, type=socket.SOCK_STREAM)
    if not all(ipaddress.ip_address(info[4][0]).is_global for info in infos):
        raise ValueError(f"{parts.hostname} is not a public address")


def block_reason(status: int, headers, requested: str, final: str, html: str | None) -> str | None:
    if status in (401, 407):
        return f"login required (HTTP {status})"
    if status == 403:
        return "access denied (HTTP 403)"
    if headers.get("cf-mitigated") == "challenge":
        return "bot challenge"
    if LOGIN_PATH.search(urlsplit(final).path) and not LOGIN_PATH.search(urlsplit(requested).path):
        return "login required (redirected to a login page)"
    if html is None:
        return None
    lower = html.lower()
    if len(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)) > 1500:
        return None
    if any(marker in lower for marker in CAPTCHA_MARKERS):
        return "captcha"
    if 'type="password"' in lower:
        return "login required (login form)"
    return None


def _file_name(resp: httpx.Response) -> str:
    msg = Message()
    msg["content-disposition"] = resp.headers.get("content-disposition", "")
    return msg.get_filename() or unquote(resp.url.path.rsplit("/", 1)[-1]) or "document"


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
        self.robots: dict[str, RobotFileParser] = {}
        self.playwright = None
        self.browser = None
        self.blocks: list[str] = []
        self.downloaded = 0
        self.assets: dict[str, tuple] = {}

    def blocked(self, reason: str) -> Blocked:
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
            parser = RobotFileParser()
            try:
                resp = await self.client.get(root + "/robots.txt")
            except httpx.HTTPError:
                parser.allow_all = True
            else:
                if resp.status_code in (401, 403):
                    parser.disallow_all = True
                elif resp.status_code >= 400:
                    parser.allow_all = True
                else:
                    parser.parse(resp.text.splitlines())
            self.robots[root] = parser
        return self.robots[root].can_fetch(USER_AGENT, url)

    async def get(self, url: str) -> httpx.Response:
        if not await self.allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        try:
            resp = await self.client.get(url)
        except httpx.TransportError:
            resp = await self.client.get(url)
        html = resp.text if "html" in resp.headers.get("content-type", "") else None
        if reason := block_reason(resp.status_code, resp.headers, url, str(resp.url), html):
            raise self.blocked(reason)
        resp.raise_for_status()
        return resp

    async def download(self, url: str, folder: Path) -> tuple[Path, str] | None:
        limit = min(MAX_FILE_BYTES, MAX_RUN_DOWNLOAD_BYTES - self.downloaded)
        if limit <= 0:
            raise BudgetExceeded
        if not await self.allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        part = folder / f".part-{uuid.uuid4().hex}"
        size = 0
        async with self.client.stream("GET", url) as resp:
            html = None
            if "html" in resp.headers.get("content-type", ""):
                html = (await resp.aread()).decode(errors="replace")
            if reason := block_reason(resp.status_code, resp.headers, url, str(resp.url), html):
                raise self.blocked(reason)
            resp.raise_for_status()
            if html is not None:
                return None
            with part.open("wb") as file:
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        break
                    file.write(chunk)
        self.downloaded += min(size, limit)
        if size > limit:
            part.unlink()
            if limit < MAX_FILE_BYTES:
                raise BudgetExceeded
            return None
        return part, _file_name(resp)
