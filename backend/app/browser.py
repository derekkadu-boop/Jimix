import asyncio
import json
import logging
import os
import re
from contextlib import suppress
from urllib.parse import parse_qsl, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, Request, Response, Route, async_playwright

from .asset_cache import PUBLIC_ASSETS, same_origin, shareable_request
from .fetch import USER_AGENT, Blocked, Fetcher, block_reason, check_public, site, wait_turn

log = logging.getLogger("watchtower.browser")
BROWSER_WS = os.environ.get("WATCHTOWER_BROWSER_WS")
RENDER_TIMEOUT_MS = int(float(os.environ.get("WATCHTOWER_RENDER_TIMEOUT", "240")) * 1000)
CACHED = {"script", "stylesheet"}
SKIPPED = {"image", "font", "media"}
TRACKED = CACHED | {"document", "fetch", "xhr"}
REQUEST_TIMEOUT_MS = 60_000
QUIET_SECONDS = 3
EMPTY_SHELL_GRACE_SECONDS = 15
FILE_TREE_TIMEOUT_MS = 25_000
NETSERVER_DIALOG = "#detailModal"
NETSERVER_FILE_CONTROL = 'a.zipFileContents[data-oid][data-token]:not([data-oid=""]):not([data-token=""])'
NETSERVER_FILE_LINK = "a.filenameEllipsis[href]"

DOM_STATE = """() => {
    const body = document.body;
    if (!body) return [0, 0, true];
    const mount = body.querySelector('#root, #app, app-root, #__next, #__nuxt');
    const text = body.innerText.trim();
    const empty = node => !node.innerText.trim() && !node.querySelector('a[href], input, button, table');
    return [body.getElementsByTagName('*').length, text.length, empty(body) || (!!mount && empty(mount))];
}"""


class PageActivity:
    def __init__(self):
        self.clock = asyncio.get_running_loop().time
        self.last_request_at = self.clock()
        self.pending: set[Request] = set()
        self.request_count = 0
        self.readers: set[asyncio.Task] = set()

    def started(self, request: Request) -> None:
        if request.resource_type in TRACKED:
            self.pending.add(request)
            self.last_request_at = self.clock()
            self.request_count += 1

    def finished(self, request: Request) -> None:
        if request in self.pending:
            self.pending.remove(request)
            self.last_request_at = self.clock()

    async def wait_until_stable(self, page: Page) -> None:
        await page.wait_for_load_state("domcontentloaded", timeout=0)
        last_change = self.clock()
        shell_grace_ends = last_change + EMPTY_SHELL_GRACE_SECONDS
        previous = None
        while True:
            if not self.pending and not self.readers:
                try:
                    current = await page.evaluate(DOM_STATE)
                except PlaywrightError:
                    if page.is_closed():
                        raise
                    previous = None
                else:
                    now = self.clock()
                    if current != previous:
                        previous = current
                        last_change = now
                    idle = now - max(last_change, self.last_request_at)
                    if (not self.pending and not self.readers and idle >= QUIET_SECONDS
                            and (not current[2] or now >= shell_grace_ends)):
                        return
            await asyncio.sleep(0.25)


def _netserver_file_tree(request: Request, expected: tuple[str, str] | None) -> bool:
    parts = urlsplit(request.url)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if (
        expected is None or request.method != "POST" or request.resource_type not in ("fetch", "xhr")
        or parts.path != "/NetServer/DataProvider" or parts.query
        or content_type != "application/x-www-form-urlencoded"
    ):
        return False
    body = request.post_data or ""
    if len(body) > 1024:
        return False
    try:
        fields = dict(parse_qsl(body, strict_parsing=True, max_num_fields=3))
    except ValueError:
        return False
    return fields == {"param": "FileTree", "oid": expected[0], "VALIDATION_TOKEN": expected[1]}


async def _latest_netserver_control(page: Page) -> Locator:
    visible = page.locator("a.zipFileContents:visible")
    await visible.first.wait_for(timeout=FILE_TREE_TIMEOUT_MS)
    controls = await visible.all()
    if len(controls) == 1:
        return controls[0]
    versions = []
    for control in controls:
        cells = await control.locator("xpath=ancestor::tr[1]/td[1]").all_text_contents()
        version = cells[0].strip() if cells else ""
        if not version.isdecimal():
            raise PlaywrightError("Could not read NetServer document version")
        versions.append((int(version), control))
    return max(versions, key=lambda item: item[0])[1]


SENSITIVE = re.compile(r"pass(word)?|token|csrf|secret|session|auth|credential", re.IGNORECASE)
MAX_POST_BODY = 8 * 1024


def listing_post(response, sent: str, page_url: str) -> bool:
    if not 200 <= response.status < 300 or len(sent) > MAX_POST_BODY or site(response.url) != site(page_url):
        return False
    try:
        body = json.loads(sent)
    except ValueError:
        return False
    return isinstance(body, dict) and not SENSITIVE.search(json.dumps(list(_keys(body))))


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


async def render(
    url: str, fetcher: Fetcher, *, netserver_files: bool = False, require_stable: bool = False
) -> tuple[str, list[dict], str]:
    if not await fetcher.allowed(url):
        raise Blocked(f"robots.txt disallows {url}")
    if fetcher.browser is None:
        fetcher.playwright = await async_playwright().start()
        chromium = fetcher.playwright.chromium
        fetcher.browser = await (chromium.connect(BROWSER_WS) if BROWSER_WS else chromium.launch())
    calls: list[dict] = []
    origin = urlsplit(url)
    expected_tree: tuple[str, str] | None = None
    file_tree_requested = False
    file_tree_denied = False
    activity = PageActivity()
    started = activity.clock()
    deadline = started + RENDER_TIMEOUT_MS / 1000
    routes: set[asyncio.Task] = set()
    closing = False

    async def fetch_route(route: Route, headers: dict[str, str] | None):
        await wait_turn(route.request.url)
        if netserver_files:
            return await route.fetch(max_redirects=0)
        timeout = max(1, min(REQUEST_TIMEOUT_MS, int((deadline - activity.clock()) * 1000)))
        return await route.fetch(max_redirects=0, timeout=timeout, headers=headers)

    async def handle(route: Route):
        nonlocal file_tree_denied, file_tree_requested
        request = route.request
        if request.resource_type in SKIPPED:
            await route.abort()
            return
        on_portal = same_origin(request.url, url)
        if netserver_files:
            if not on_portal:
                await route.abort()
                return
            if request.method not in ("GET", "HEAD"):
                if file_tree_requested or not _netserver_file_tree(request, expected_tree):
                    await route.abort()
                    return
                file_tree_requested = True
        if (netserver_files or (on_portal and request.resource_type in {"fetch", "xhr"})) and not await (
            fetcher.allowed(request.url)
        ):
            if netserver_files and request.method == "POST":
                file_tree_denied = True
                await route.fulfill(status=403)
            else:
                await route.abort()
            return
        if request.method == "GET" and (cached := fetcher.assets.get(request.url)):
            await route.fulfill(status=cached[0], headers=cached[1], body=cached[2])
            return
        headers = None
        try:
            await check_public(request.url)
            if (not netserver_files and request.method == "GET" and request.resource_type in CACHED
                    and not request.service_worker and request.frame == page.main_frame
                    and same_origin(request.frame.url, request.url)):
                current_headers = await request.all_headers()
                if shareable_request(request.url, current_headers):
                    if not await fetcher.allowed(request.url):
                        await route.abort()
                        return
                    asset = PUBLIC_ASSETS.get(request.url, request.resource_type)
                    if asset:
                        await route.fulfill(status=200, headers=asset.response_headers(), body=asset.body)
                        return
                    # route.fetch otherwise adds cookies from the browser context's current jar.
                    headers = current_headers | {"accept-encoding": "identity", "cookie": ""}
            try:
                response = await fetch_route(route, headers)
            except PlaywrightError:
                if closing or page.is_closed() or (netserver_files and request.method not in ("GET", "HEAD")):
                    raise
                response = await fetch_route(route, headers)
        except (ValueError, OSError, PlaywrightError) as exc:
            reason = str(exc).splitlines() or [type(exc).__name__]
            log.info("browser %s %s aborted: %s", request.method, request.url, reason[0][:150])
            if not page.is_closed():
                await route.abort()
            return
        log.info("browser %s %s -> %s", request.method, request.url, response.status)
        if netserver_files and 300 <= response.status < 400 and response.headers.get("location"):
            await route.abort()
            return
        if request.resource_type in CACHED and response.status == 200:
            try:
                body = await response.body()
                fetcher.assets[request.url] = (200, response.headers, body)
                if headers is not None:
                    PUBLIC_ASSETS.save(request.url, request.resource_type, response.headers, body)
            except PlaywrightError:
                pass
        await route.fulfill(response=response)

    async def route(intercepted: Route):
        if closing:
            with suppress(PlaywrightError):
                await intercepted.abort()
            return
        task = asyncio.current_task()
        assert task is not None
        routes.add(task)
        try:
            if netserver_files:
                await handle(intercepted)
            else:
                async with asyncio.timeout_at(deadline):
                    await handle(intercepted)
        except TimeoutError:
            if not closing:
                with suppress(PlaywrightError):
                    await intercepted.abort()
        except asyncio.CancelledError:
            if closing:
                with suppress(PlaywrightError):
                    await intercepted.abort()
            raise
        finally:
            routes.discard(task)

    async def read_json(response: Response):
        try:
            body = await response.text()
        except PlaywrightError:
            return
        call = {"url": response.url, "method": response.request.method, "sample": body[:1500]}
        if response.request.method == "POST":
            sent = response.request.post_data or ""
            call["requestBody"] = sent[:1500]
            if listing_post(response, sent, url):
                fetcher.observed_posts.setdefault(response.url, []).append(json.loads(sent))
        calls.append(call)

    def on_response(response: Response):
        if "json" in response.headers.get("content-type", "").lower() and len(calls) + len(activity.readers) < 10:
            reader = asyncio.create_task(read_json(response))
            activity.readers.add(reader)
            reader.add_done_callback(activity.readers.discard)

    options = {"service_workers": "block", "accept_downloads": False} if netserver_files else {}
    page = await fetcher.browser.new_page(user_agent=USER_AGENT, **options)
    settle_error = None
    try:
        page.on("request", activity.started)
        page.on("requestfinished", activity.finished)
        page.on("requestfailed", activity.finished)
        if netserver_files:
            await page.context.route("**/*", route)
            await page.context.route_web_socket("**/*", lambda socket: socket.close())
            response = await page.goto(url, wait_until="load", timeout=RENDER_TIMEOUT_MS)
            await page.wait_for_timeout(2_000)
            html = await page.content()
        else:
            await page.route("**/*", route)
            page.on("response", on_response)
            timeout = max(1, int((deadline - activity.clock()) * 1000))
            response = await page.goto(url, wait_until="commit", timeout=timeout)
            if response and (reason := block_reason(response.status, response.headers, url, page.url, None)):
                raise fetcher.blocked(reason)
            try:
                async with asyncio.timeout_at(deadline):
                    await activity.wait_until_stable(page)
            except TimeoutError as exc:
                log.info("render %s reached its time limit with %d pending requests",
                         origin.hostname, len(activity.pending))
                settle_error = exc
            html = await asyncio.wait_for(page.content(), timeout=max(1, deadline - activity.clock()))
        final = page.url
        status = response.status if response else 200
        if reason := block_reason(status, response.headers if response else {}, url, final, html):
            raise fetcher.blocked(reason)
        if require_stable and settle_error is not None:
            raise PlaywrightError("Browser page did not settle before the render timeout") from settle_error
        if netserver_files:
            control = await _latest_netserver_control(page)
            dialog = page.locator(NETSERVER_DIALOG)
            links = dialog.locator(NETSERVER_FILE_LINK)
            if await links.count():
                raise PlaywrightError("NetServer file list was already populated before opening")
            oid = await control.get_attribute("data-oid", timeout=FILE_TREE_TIMEOUT_MS)
            token = await control.get_attribute("data-token", timeout=FILE_TREE_TIMEOUT_MS)
            if not oid or not token:
                raise PlaywrightError("NetServer public file list control is missing its parameters")
            expected_tree = oid, token
            async with page.expect_response(
                lambda result: _netserver_file_tree(result.request, expected_tree), timeout=FILE_TREE_TIMEOUT_MS
            ) as pending:
                await control.click(timeout=FILE_TREE_TIMEOUT_MS)
            file_tree = await pending.value
            if file_tree_denied:
                raise fetcher.blocked(f"robots.txt disallows {file_tree.url}")
            body = (await file_tree.text()).lstrip()
            markup = body if body.startswith("<") else None
            if reason := block_reason(file_tree.status, file_tree.headers, file_tree.url, file_tree.url, markup):
                raise fetcher.blocked(reason)
            if not file_tree.ok:
                raise PlaywrightError(f"NetServer file list returned HTTP {file_tree.status}")
            await links.first.wait_for(timeout=FILE_TREE_TIMEOUT_MS)
            html = await dialog.inner_html(timeout=FILE_TREE_TIMEOUT_MS)
    finally:
        closing = True
        if not netserver_files:
            page.remove_listener("response", on_response)
        tasks = tuple(routes | activity.readers)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await page.close()
        log.info("render %s in %.1fs, %d tracked requests, %d JSON responses",
                 origin.hostname, activity.clock() - started, activity.request_count, len(calls))
    return html, calls, final
