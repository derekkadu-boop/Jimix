import asyncio
from dataclasses import dataclass, field

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from .access import HTTPObservation, detect_access_restrictions
from .context import IN_FLIGHT_LIMITS, ExplorationContext, StopExploration, ToolError
from .models import StopReason
from .observations import PAGE_ROLES, bounded_json, json_shape, public_json_sample
from .urls import UnsafeURL, normalize_url, origin

# Every successful site request is fulfilled by the controlled transport. A
# deny proxy covers Chromium traffic outside the route API (e.g. speculative I/O).
DENY_PROXY = {"server": "http://127.0.0.1:1", "bypass": "<-loopback>"}
ALLOWED_TYPES = {"document", "script", "stylesheet", "xhr", "fetch"}


@dataclass
class BrowserObservation:
    html: str
    final_url: str
    endpoints: list[dict] = field(default_factory=list)
    denied: int = 0
    truncated: bool = False


async def inspect_rendered_page(
    url: str, ctx: ExplorationContext, *, blocking: bool = False
) -> BrowserObservation:
    if not ctx.browser:
        raise ToolError("Browser inspection is unavailable")
    observed = await ctx.browser(url, ctx)
    response = HTTPObservation(
        url, observed.final_url, 200, {"content-type": "text/html"}, observed.html.encode("utf-8")
    )
    restriction = detect_access_restrictions(response)
    if restriction.is_blocking:
        reason = StopReason(restriction.restriction_type)
        ctx.block(reason, url, observed.final_url)
        if blocking:
            raise StopExploration(reason)
        raise ToolError(reason.value)
    return observed


async def observe_browser(url: str, context: ExplorationContext) -> BrowserObservation:
    ctx = context
    cfg = ctx.config
    if url in ctx.browser_evidence:
        async with ctx.step("cached_browser_observation", {"url": url}) as trace:
            ctx.usage.cache_hits += 1
            trace["url"] = url
        return ctx.browser_evidence[url]
    if ctx.usage.browser_navigations >= cfg.max_browser_navigations:
        ctx.browser_limit_encountered = True
        raise ToolError("Browser navigation budget exhausted")
    if ctx.browser_unavailable:
        raise ToolError("Chromium is unavailable for this run")
    if not (ctx.candidates.get(url, set()) & PAGE_ROLES):
        raise ToolError("Browser URL was not observed as a public page")
    ctx.require(ignoring=IN_FLIGHT_LIMITS, duration=cfg.browser_timeout_seconds)
    async with ctx.step("browser_navigation", {"url": url, "client": "playwright"}) as trace:
        try:
            async with asyncio.timeout(cfg.browser_timeout_seconds):
                result = await _render(url, ctx)
        except PlaywrightError as exc:
            if "Executable doesn't exist" in str(exc):
                ctx.browser_unavailable = True
                raise ToolError(
                    "Chromium is unavailable. Install it with playwright install chromium"
                ) from None
            if ctx.stop:
                raise StopExploration(ctx.stop) from None
            raise ToolError("Playwright could not render this page") from None
        except TimeoutError:
            raise ToolError("Browser navigation timed out") from None
        ctx.require(ignoring=IN_FLIGHT_LIMITS + (StopReason.HTTP_LIMIT, StopReason.PAGE_LIMIT))
        ctx.browser_evidence[url] = result
        trace.update(
            {
                "url": result.final_url,
                "endpoints": result.endpoints,
                "denied_requests": result.denied,
                "truncated": result.truncated,
            }
        )
        return result


async def _render(url: str, ctx: ExplorationContext) -> BrowserObservation:
    result = BrowserObservation("", url)
    pending: set[asyncio.Task] = set()
    route_errors: list[StopExploration | ToolError | UnsafeURL] = []
    last_activity = asyncio.get_running_loop().time()
    main_seen = False

    async with async_playwright() as manager:
        browser = await manager.chromium.launch(
            headless=True,
            proxy=DENY_PROXY,
            args=[
                "--disable-background-networking",
                "--disable-quic",
                "--disable-dns-prefetch",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ],
        )
        try:
            session = await browser.new_context(service_workers="block", accept_downloads=False)

            async def deny_socket(socket):
                await socket.close()

            await session.route_web_socket("**/*", deny_socket)
            page = await session.new_page()

            async def serve(route):
                nonlocal last_activity, main_seen
                task = asyncio.current_task()
                pending.add(task)
                request = route.request
                resource = request.resource_type
                is_main = resource == "document" and request.frame == page.main_frame and not main_seen
                if is_main:
                    main_seen = True
                try:
                    target = normalize_url(request.url)
                    roles = ctx.candidates.get(target, set())
                    same_origin = origin(target) == origin(result.final_url)
                    if (
                        resource not in ALLOWED_TYPES
                        or request.method not in {"GET", "HEAD"}
                        or (resource == "document" and not is_main)
                        or (not same_origin and not roles.intersection({"script", "api"}))
                    ):
                        result.denied += 1
                        await route.abort()
                        return
                    user_agent = request.headers.get("user-agent") or await page.evaluate(
                        "navigator.userAgent"
                    )
                    fetched = await ctx.transport.fetch(
                        target,
                        blocking=is_main,
                        client="playwright",
                        user_agent=user_agent,
                        method=request.method,
                        browser_scope=True,
                        page_kind=None if is_main else False,
                    )
                    if fetched.restriction.is_blocking:
                        if is_main:
                            ctx.stop = StopReason(fetched.restriction.restriction_type)
                            raise StopExploration(ctx.stop)
                        result.denied += 1
                        await route.abort()
                        return
                    if is_main:
                        result.final_url = fetched.final_url
                        result.truncated = fetched.truncated
                        if not fetched.is_html or fetched.status != 200:
                            raise ToolError("Browser navigation did not return accessible HTML")
                    if (
                        resource in {"xhr", "fetch"}
                        and not fetched.truncated
                        and 200 <= fetched.status < 300
                        and len(result.endpoints) < min(ctx.config.max_candidate_urls, 10)
                    ):
                        try:
                            data = bounded_json(fetched.body, ctx.config.max_json_depth)
                        except ToolError:
                            pass
                        else:
                            if ctx.add_candidate(fetched.final_url, "api"):
                                ctx.capabilities.public_api_observed = True
                                result.endpoints.append(
                                    {
                                        "url": fetched.final_url,
                                        "status": fetched.status,
                                        "shape": json_shape(data),
                                        "sample": public_json_sample(data),
                                    }
                                )
                    headers = {
                        key: value
                        for key, value in fetched.headers.items()
                        if key
                        in {
                            "content-type",
                            "access-control-allow-origin",
                            "access-control-allow-headers",
                            "access-control-expose-headers",
                        }
                    }
                    body = fetched.body
                    if is_main and target != fetched.final_url:
                        # Keep relative HTML links anchored at the validated redirect destination.
                        from html import escape

                        body = f'<base href="{escape(fetched.final_url, quote=True)}">'.encode() + body
                    await route.fulfill(status=fetched.status, headers=headers, body=body)
                except (StopExploration, UnsafeURL, ToolError) as exc:
                    if is_main or isinstance(exc, StopExploration):
                        route_errors.append(exc)
                    result.denied += 1
                    await route.abort()
                finally:
                    pending.discard(task)
                    last_activity = asyncio.get_running_loop().time()

            await session.route("**/*", serve)
            try:
                ctx.require(
                    ignoring=IN_FLIGHT_LIMITS,
                    duration=min(ctx.config.http_timeout_seconds, ctx.config.browser_timeout_seconds),
                )
                ctx.usage.browser_navigations += 1
                await ctx.checkpoint()
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=int(ctx.config.browser_timeout_seconds * 1000)
                )
                while pending or asyncio.get_running_loop().time() - last_activity < 0.35:
                    await asyncio.sleep(0.04)
                if route_errors:
                    raise route_errors[0]
                # This fixed script reads the current DOM. Tool arguments cannot supply scripts.
                limit = ctx.config.max_response_bytes
                result.html = await page.evaluate(
                    "limit => document.documentElement.outerHTML.slice(0, limit + 1)", limit
                )
                if len(result.html.encode("utf-8")) > limit:
                    result.truncated = True
                    result.html = result.html.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
                return result
            except PlaywrightError:
                if route_errors:
                    raise route_errors[0] from None
                raise
            finally:
                for task in list(pending):
                    task.cancel()
                if pending:
                    await asyncio.gather(*tuple(pending), return_exceptions=True)
                await session.close()
        finally:
            await browser.close()
