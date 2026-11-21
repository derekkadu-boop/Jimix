import logging
import os
from urllib.parse import parse_qsl, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, Request, async_playwright

from .fetch import USER_AGENT, Blocked, Fetcher, block_reason, check_public, wait_turn

log = logging.getLogger("watchtower.browser")
BROWSER_WS = os.environ.get("WATCHTOWER_BROWSER_WS")
RENDER_TIMEOUT_MS = int(float(os.environ.get("WATCHTOWER_RENDER_TIMEOUT", "240")) * 1000)
CACHED = {"script", "stylesheet"}
SKIPPED = {"image", "font", "media"}
FILE_TREE_TIMEOUT_MS = 25_000
NETSERVER_DIALOG = "#detailModal"
NETSERVER_FILE_LINK = "a.filenameEllipsis[href]"


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


async def render(url: str, fetcher: Fetcher, *, netserver_files: bool = False) -> tuple[str, list[dict], str]:
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

    async def route(route):
        nonlocal file_tree_denied, file_tree_requested
        request = route.request
        if request.resource_type in SKIPPED:
            await route.abort()
            return
        if netserver_files:
            parts = urlsplit(request.url)
            if (parts.scheme, parts.netloc) != (origin.scheme, origin.netloc):
                await route.abort()
                return
            if request.method not in ("GET", "HEAD"):
                if file_tree_requested or not _netserver_file_tree(request, expected_tree):
                    await route.abort()
                    return
                file_tree_requested = True
            if not await fetcher.allowed(request.url):
                if request.method == "POST":
                    file_tree_denied = True
                    await route.fulfill(status=403)
                else:
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
                if netserver_files and request.method not in ("GET", "HEAD"):
                    raise
                await wait_turn(request.url)
                response = await route.fetch(max_redirects=0)
        except (ValueError, OSError, PlaywrightError) as exc:
            log.info("browser %s %s aborted: %s", request.method, request.url, str(exc).splitlines()[0][:150])
            await route.abort()
            return
        log.info("browser %s %s -> %s", request.method, request.url, response.status)
        if netserver_files and 300 <= response.status < 400 and response.headers.get("location"):
            await route.abort()
            return
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

    options = {"service_workers": "block", "accept_downloads": False} if netserver_files else {}
    page = await fetcher.browser.new_page(user_agent=USER_AGENT, **options)
    try:
        if netserver_files:
            await page.context.route("**/*", route)
            await page.context.route_web_socket("**/*", lambda socket: socket.close())
        else:
            await page.route("**/*", route)
            page.on("response", on_response)
        response = await page.goto(url, wait_until="load", timeout=RENDER_TIMEOUT_MS)
        await page.wait_for_timeout(2_000)
        html, final = await page.content(), page.url
        status = response.status if response else 200
        if reason := block_reason(status, response.headers if response else {}, url, final, html):
            raise fetcher.blocked(reason)
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
        await page.close()
    return html, calls, final
