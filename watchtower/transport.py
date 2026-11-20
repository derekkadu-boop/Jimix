import asyncio
import socket
import ssl
import weakref
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import certifi
import httpx

from .access import HTTPObservation, detect_access_restrictions
from .context import IN_FLIGHT_LIMITS, ExplorationContext, StopExploration, ToolError
from .models import RobotsDecision, StopReason
from .robots import RobotsPolicy, parse_robots
from .urls import UnsafeURL, candidate_url, normalize_url, origin, resolve_public, safe_url

MIN_REQUEST_INTERVAL = 1.0
REDIRECTS = {301, 302, 303, 307, 308}
SAFE_RESPONSE_HEADERS = {
    "content-type",
    "content-encoding",
    "content-length",
    "content-range",
    "accept-ranges",
    "location",
    "retry-after",
    "cf-mitigated",
    "access-control-allow-origin",
    "access-control-allow-headers",
    "access-control-expose-headers",
}
MAX_ROBOTS_BYTES = 512_000


@dataclass
class RateGate:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_start: float = float("-inf")
    paused_until: float = 0
    crawl_delay: float = 0


_gates = weakref.WeakKeyDictionary()


def _gate(url: str) -> RateGate:
    gates = _gates.setdefault(asyncio.get_running_loop(), {})
    return gates.setdefault(_canonical_host(url), RateGate())


def _canonical_host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def verified_tls_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # Loading the PEM in memory also works with standalone CPython builds on Windows.
    context.load_verify_locations(cadata=Path(certifi.where()).read_text(encoding="ascii"))
    return context


class ControlledTransport:
    def __init__(self, context: ExplorationContext, *, http_transport=None, resolver=resolve_public):
        self.context = context
        self.resolver = resolver
        self.client = httpx.AsyncClient(
            transport=http_transport,
            trust_env=False,
            follow_redirects=False,
            verify=verified_tls_context() if http_transport is None else True,
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        self.cache: dict[tuple[str, str, str, bool], HTTPObservation] = {}
        self.fetch_locks: dict[tuple[str, str, str, bool], asyncio.Lock] = {}
        self.robots: dict[str, RobotsPolicy] = {}
        self.robots_locks: dict[str, asyncio.Lock] = {}

    async def close(self):
        await self.client.aclose()

    async def _rate_limit(self, url: str, timeout: float) -> RateGate:
        ctx, gate = self.context, _gate(url)
        async with gate.lock:
            wait = max(
                0,
                gate.last_start
                + max(MIN_REQUEST_INTERVAL, ctx.config.request_interval_seconds, gate.crawl_delay)
                - asyncio.get_running_loop().time(),
            )
            paused = max(0, gate.paused_until - asyncio.get_running_loop().time())
            if paused and max(wait, paused) + timeout >= ctx.remaining:
                raise StopExploration(StopReason.RATE_LIMITED)
            ctx.require(ignoring=IN_FLIGHT_LIMITS, duration=max(wait, paused) + timeout)
            if wait or paused:
                await asyncio.sleep(max(wait, paused))
            ctx.require(ignoring=IN_FLIGHT_LIMITS, duration=timeout)
            gate.last_start = asyncio.get_running_loop().time()
        return gate

    async def _send(
        self,
        url: str,
        *,
        client: str,
        user_agent: str,
        method: str,
        limit: int,
        probe: bool,
        robots: bool,
        page_kind: bool | None,
    ) -> HTTPObservation:
        ctx = self.context
        args = {"url": url, "client": client, "method": method, "user_agent": user_agent, "probe": probe}
        async with ctx.step("robots_http" if robots else "http_request", args) as trace:
            timeout = ctx.config.http_timeout_seconds
            # Resolve before waiting for a rate slot. The validated address is pinned
            # for this request, including certificate verification for the original host.
            ctx.require(ignoring=IN_FLIGHT_LIMITS, duration=timeout)
            parsed = httpx.URL(url)
            port = 443 if parsed.scheme == "https" else 80
            try:
                async with asyncio.timeout(timeout):
                    address = await self.resolver(parsed.host, port)
                gate = await self._rate_limit(url, timeout)
                async with asyncio.timeout(timeout):
                    headers = {
                        "Host": parsed.netloc.decode("ascii"),
                        "User-Agent": user_agent,
                        "Accept": ctx.config.accept,
                        "Accept-Language": ctx.config.accept_language,
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    }
                    if probe and method == "GET":
                        headers["Range"] = f"bytes=0-{limit - 1}"
                    request = httpx.Request(
                        method,
                        parsed.copy_with(host=address),
                        headers=headers,
                        extensions={"sni_hostname": parsed.raw_host.decode("ascii")},
                    )
                    ctx.usage.http_requests += 1
                    response = await self.client.send(request, stream=True, follow_redirects=False)
                    try:
                        info = {
                            key: value
                            for key, value in response.headers.items()
                            if key in SAFE_RESPONSE_HEADERS
                        }
                        if response.status_code in REDIRECTS or method == "HEAD":
                            body, truncated = b"", False
                        else:
                            body, truncated = await _read(response, limit)
                    finally:
                        await response.aclose()
            except (TimeoutError, httpx.HTTPError, socket.gaierror, zlib.error) as exc:
                raise ToolError(f"HTTP request failed ({type(exc).__name__})") from None
            result = HTTPObservation(
                url, url, response.status_code, info, body, truncated, client=client, user_agent=user_agent
            )
            result.restriction = detect_access_restrictions(result)
            if not robots:
                if result.status not in REDIRECTS and (
                    page_kind is True or page_kind is None and result.is_html
                ):
                    ctx.add_page(url)
                ctx.last_state = result.summary()
            if result.status == 429:
                delay = result.restriction.retry_after_seconds
                gate.paused_until = max(
                    gate.paused_until, asyncio.get_running_loop().time() + (delay if delay is not None else 0)
                )
                ctx.capabilities.rate_limiting_observed = True
            trace.update(result.summary())
            if not 200 <= result.status < 400:
                trace["error"] = f"HTTP {result.status}"
            ctx.require(ignoring=IN_FLIGHT_LIMITS + (StopReason.HTTP_LIMIT, StopReason.PAGE_LIMIT))
            return result

    async def check_robots(self, url: str, user_agent: str) -> RobotsDecision:
        ctx, root = self.context, origin(url)
        lock = self.robots_locks.setdefault(root, asyncio.Lock())
        async with lock:
            async with ctx.step("check_robots", {"url": url, "user_agent": user_agent}) as trace:
                policy = self.robots.get(root)
                if policy is not None:
                    ctx.usage.cache_hits += 1
                else:
                    policy_url = root + "/robots.txt"
                    try:
                        response = await self._get_chain(
                            policy_url,
                            client="httpx",
                            user_agent=ctx.config.user_agent,
                            method="GET",
                            probe=False,
                            robots=True,
                            page_kind=False,
                            blocking=False,
                            browser_scope=False,
                        )
                    except ToolError:
                        policy = RobotsPolicy(policy_url, "unavailable", ["robots.txt request failed"])
                    else:
                        if response.status == 404:
                            policy = RobotsPolicy(policy_url, "absent", ["robots.txt returned HTTP 404"])
                        elif (
                            response.status == 200
                            and not response.restriction.is_blocking
                            and not response.is_html
                        ):
                            policy = parse_robots(policy_url, response.body, response.truncated)
                        elif response.status == 200 and not response.restriction.is_blocking:
                            policy = RobotsPolicy(policy_url, "malformed", ["robots.txt returned HTML"])
                        else:
                            policy = RobotsPolicy(
                                policy_url, "unavailable", [f"robots.txt returned HTTP {response.status}"]
                            )
                    self.robots[root] = policy
                allow = ctx.config.ambiguous_robots == "allow"
                decision = policy.evaluate(url, user_agent, allow)
                if user_agent != ctx.config.user_agent:
                    crawler = policy.evaluate(url, ctx.config.user_agent, allow)
                    if not crawler.allowed:
                        decision = crawler
                    elif crawler.crawl_delay_seconds is not None:
                        decision.crawl_delay_seconds = max(
                            decision.crawl_delay_seconds or 0, crawler.crawl_delay_seconds
                        )
                if decision.crawl_delay_seconds is not None:
                    gate = _gate(url)
                    gate.crawl_delay = max(gate.crawl_delay, decision.crawl_delay_seconds)
                if url == ctx.url:
                    ctx.capabilities.robots_restrictions = (
                        True
                        if decision.status == "disallowed"
                        else False
                        if decision.status in {"allowed", "absent"}
                        else None
                    )
                trace.update(decision.model_dump(mode="json"))
                if not decision.allowed:
                    trace["error"] = decision.status
                return decision

    async def fetch(
        self,
        url: str,
        *,
        roles: set[str] | None = None,
        blocking: bool = False,
        page_kind: bool | None = None,
        probe: bool = False,
        method: str = "GET",
        client: str = "httpx",
        user_agent: str | None = None,
        browser_scope: bool = False,
    ) -> HTTPObservation:
        url = normalize_url(url)
        ctx = self.context
        if method not in {"GET", "HEAD"}:
            raise ToolError("Only public GET and HEAD requests are permitted")
        if roles is not None and not (ctx.candidates.get(url, set()) & roles):
            raise ToolError("This URL was not observed for the requested purpose")
        if ctx.is_blocked(url):
            raise ToolError("This resource was previously blocked")
        user_agent = user_agent or ctx.config.user_agent
        async with self.fetch_locks.setdefault((url, user_agent, method, probe), asyncio.Lock()):
            return await self._get_chain(
                url,
                client=client,
                user_agent=user_agent,
                method=method,
                probe=probe,
                robots=False,
                page_kind=page_kind,
                blocking=blocking,
                browser_scope=browser_scope,
            )

    async def _get_chain(
        self,
        url: str,
        *,
        client: str,
        user_agent: str,
        method: str,
        probe: bool,
        robots: bool,
        page_kind: bool | None,
        blocking: bool,
        browser_scope: bool,
    ) -> HTTPObservation:
        ctx, start = self.context, url
        redirects: list[dict] = []
        visited = {url}
        retried = False
        cache_key = (start, user_agent, method, probe)
        cached = self.cache.get(cache_key) if not robots else None
        if cached is not None:
            async with ctx.step("cached_observation", {"url": start, "client": client}) as trace:
                ctx.usage.cache_hits += 1
                if page_kind is True or page_kind is None and cached.is_html:
                    ctx.add_page(cached.final_url)
                trace.update(cached.summary())
                ctx.require(ignoring=IN_FLIGHT_LIMITS + (StopReason.HTTP_LIMIT, StopReason.PAGE_LIMIT))
            return cached
        limit = (
            min(MAX_ROBOTS_BYTES, ctx.config.max_response_bytes)
            if robots
            else ctx.config.max_document_probe_bytes
            if probe
            else ctx.config.max_response_bytes
        )
        while True:
            if ctx.is_blocked(url):
                raise ToolError("This resource was previously blocked")
            if not robots and ctx.documentation_depth.get(url, 0) > ctx.config.max_documentation_depth:
                raise ToolError("Documentation depth limit reached")
            if not robots:
                decision = await self.check_robots(url, user_agent)
                if not decision.allowed:
                    reason = (
                        StopReason.ROBOTS_DISALLOWED
                        if decision.status == "disallowed"
                        else StopReason.ROBOTS_UNAVAILABLE
                    )
                    ctx.block(reason, url)
                    if reason is StopReason.ROBOTS_DISALLOWED:
                        ctx.capabilities.robots_restrictions = True
                    elif blocking:
                        ctx.capabilities.robots_restrictions = None
                    if blocking:
                        raise StopExploration(reason)
                    raise ToolError(f"{reason.value} for this resource")
            result = await self._send(
                url,
                client=client,
                user_agent=user_agent,
                method=method,
                limit=limit,
                probe=probe,
                robots=robots,
                page_kind=page_kind,
            )
            result.original_url = start
            result.redirects = list(redirects)
            restriction = result.restriction
            if restriction.is_blocking:
                if not robots:
                    ctx.block(StopReason(restriction.restriction_type), url, start)
                return result
            if result.status == 429:
                if robots:
                    return result
                if (
                    not retried
                    and restriction.retry_after_seconds is not None
                    and restriction.retry_after_seconds + ctx.config.http_timeout_seconds < ctx.remaining
                ):
                    retried = True
                    try:
                        ctx.require(ignoring=IN_FLIGHT_LIMITS)
                    except StopExploration as exc:
                        if exc.reason != StopReason.HTTP_LIMIT:
                            raise
                    else:
                        continue
                ctx.stop = StopReason.RATE_LIMITED
                return result
            if result.status not in REDIRECTS:
                if not robots and 200 <= result.status < 400:
                    self.cache[cache_key] = result
                    self.cache.setdefault((url, user_agent, method, probe), result)
                return result

            target = candidate_url(url, result.headers.get("location", ""))
            if not target or (url.startswith("https:") and target.startswith("http:")):
                raise UnsafeURL("Redirect destination is unsafe")
            if target in visited or len(redirects) >= ctx.config.max_redirects:
                raise ToolError("Redirect loop or redirect limit reached")
            if (
                origin(target) != origin(url)
                and _canonical_host(target) != _canonical_host(url)
                and (robots or browser_scope or target not in ctx.candidates)
            ):
                raise UnsafeURL("Redirect left the permitted host or observed scope")
            redirects.append({"url": safe_url(url), "status": result.status, "target": safe_url(target)})
            visited.add(target)
            url = target


async def _read(response: httpx.Response, limit: int) -> tuple[bytes, bool]:
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"identity", "", "gzip", "deflate"}:
        raise ToolError("Unsupported response compression")
    decoder = (
        zlib.decompressobj(31 if encoding == "gzip" else 15) if encoding in {"gzip", "deflate"} else None
    )
    data = bytearray()
    raw_size = 0
    async for chunk in response.aiter_raw():
        raw_size += len(chunk)
        if decoder:
            data.extend(decoder.decompress(chunk, limit + 1 - len(data)))
            if decoder.unused_data:
                raise ToolError("Unexpected trailing data in compressed response")
        else:
            data.extend(chunk[: limit + 1 - len(data)])
        if len(data) > limit:
            return bytes(data[:limit]), True
        if raw_size > limit + 65_536:
            raise ToolError("Compressed response exceeded its transfer allowance")
    if decoder and not decoder.eof:
        raise ToolError("Incomplete compressed response")
    return bytes(data), False
