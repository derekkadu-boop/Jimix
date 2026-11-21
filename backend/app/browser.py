import logging
import os

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from .fetch import USER_AGENT, Blocked, Fetcher, block_reason, check_public, wait_turn

log = logging.getLogger("watchtower.browser")
BROWSER_WS = os.environ.get("WATCHTOWER_BROWSER_WS")
RENDER_TIMEOUT_MS = int(float(os.environ.get("WATCHTOWER_RENDER_TIMEOUT", "240")) * 1000)
CACHED = {"script", "stylesheet"}
SKIPPED = {"image", "font", "media"}


async def render(url: str, fetcher: Fetcher) -> tuple[str, list[dict], str]:
    if not await fetcher.allowed(url):
        raise Blocked(f"robots.txt disallows {url}")
    if fetcher.browser is None:
        fetcher.playwright = await async_playwright().start()
        chromium = fetcher.playwright.chromium
        fetcher.browser = await (chromium.connect(BROWSER_WS) if BROWSER_WS else chromium.launch())
    calls: list[dict] = []

    async def route(route):
        request = route.request
        if request.resource_type in SKIPPED:
            await route.abort()
            return
        if request.method == "GET" and (cached := fetcher.assets.get(request.url)):
            await route.fulfill(status=cached[0], headers=cached[1], body=cached[2])
            return
        try:
            await check_public(request.url)
            try:
                await wait_turn(request.url)
                response = await route.fetch(max_redirects=0)
            except PlaywrightError:
                await wait_turn(request.url)
                response = await route.fetch(max_redirects=0)
        except (ValueError, OSError, PlaywrightError) as exc:
            log.info("browser %s %s aborted: %s", request.method, request.url, str(exc).splitlines()[0][:150])
            await route.abort()
            return
        log.info("browser %s %s -> %s", request.method, request.url, response.status)
        if request.resource_type in CACHED and response.status == 200:
            try:
                fetcher.assets[request.url] = (200, response.headers, await response.body())
            except PlaywrightError:
                pass
        await route.fulfill(response=response)

    async def on_response(response):
        if "json" in response.headers.get("content-type", "") and len(calls) < 10:
            try:
                body = await response.text()
            except PlaywrightError:
                return
            calls.append({"url": response.url, "method": response.request.method, "sample": body[:1500]})

    page = await fetcher.browser.new_page(user_agent=USER_AGENT)
    try:
        await page.route("**/*", route)
        page.on("response", on_response)
        response = await page.goto(url, wait_until="load", timeout=RENDER_TIMEOUT_MS)
        await page.wait_for_timeout(2_000)
        html, final = await page.content(), page.url
    finally:
        await page.close()
    status = response.status if response else 200
    if reason := block_reason(status, response.headers if response else {}, url, final, html):
        raise fetcher.blocked(reason)
    return html, calls, final
