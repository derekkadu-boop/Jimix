from bs4 import BeautifulSoup

from ..browser import inspect_rendered_page
from ..context import ExplorationContext, ToolError
from ..models import URLInput
from ..observations import PAGE_ROLES, SCRIPT_STATE, bounded_json, describe_page, json_shape
from ..urls import candidate_url, normalize_url, sanitize
from .extraction import probe_document


async def inspect_page(args: URLInput, context: ExplorationContext) -> dict:
    ctx = context
    url = normalize_url(args.url)
    response = await ctx.transport.fetch(url, roles=PAGE_ROLES)
    require_accessible(response)
    if not response.is_html:
        raise ToolError("Expected an HTML page")
    return response.summary() | describe_page(response.body, response.final_url, ctx)


async def inspect_network(args: URLInput, context: ExplorationContext) -> dict:
    ctx = context
    url = normalize_url(args.url)
    observed = await inspect_rendered_page(url, ctx, blocking="initial" in ctx.candidates.get(url, set()))
    page = describe_page(observed.html, observed.final_url, ctx)
    ctx.log("Browser observed %d structured endpoint(s)", len(observed.endpoints))
    return {
        "url": observed.final_url,
        "endpoints": observed.endpoints,
        "denied_requests": observed.denied,
        "truncated": observed.truncated,
        **page,
    }


async def inspect_scripts(args: URLInput, context: ExplorationContext) -> dict:
    ctx = context
    url = normalize_url(args.url)
    response = await ctx.transport.fetch(url, roles=PAGE_ROLES)
    require_accessible(response)
    if not response.is_html:
        raise ToolError("Expected an HTML page with embedded application state")
    soup = BeautifulSoup(response.body, "html.parser")
    indicators = []
    endpoints = set()
    for script in soup.find_all("script", limit=80):
        key = str(script.get("id", ""))
        kind = str(script.get("type", "")).lower()
        text = script.string or ""
        if len(text) > 100_000:
            continue
        if "json" not in kind and key.lower() not in SCRIPT_STATE:
            marker = next((m for m in SCRIPT_STATE if m in text[:120].lower()), None)
            if not marker or "=" not in text:
                continue
            text = text.partition("=")[2].strip().removesuffix(";")
            key = marker
        try:
            data = bounded_json(text, ctx.config.max_json_depth)
        except ToolError:
            continue
        indicators.append({"id": key[:50], "type": kind[:40], "sample": json_shape(data)})
        endpoints.update(_script_endpoints(data, response.final_url, ctx))
        if len(indicators) >= 8:
            break
    page = describe_page(soup, response.final_url, ctx)
    return {
        "url": response.final_url,
        "embedded_json": indicators,
        "endpoints": sorted(endpoints)[:12],
        "scripts": page.get("scripts", []),
        "js_rendering_signals": page.get("js_rendering_signals", []),
    }


def _script_endpoints(value, base: str, ctx: ExplorationContext) -> set[str]:
    pending = [(value, False)]
    targets = set()
    for _ in range(160):
        if not pending or len(targets) >= 12:
            break
        item, endpoint = pending.pop()
        if isinstance(item, dict):
            pending.extend(
                (
                    entry,
                    key.lower().replace("_", "")
                    in {
                        "api",
                        "apiurl",
                        "apiendpoint",
                        "endpoint",
                        "endpoints",
                        "tendersurl",
                        "noticesurl",
                    },
                )
                for key, entry in list(item.items())[:20]
            )
        elif isinstance(item, list):
            pending.extend((entry, endpoint) for entry in item[:8])
        elif endpoint and isinstance(item, str) and item.startswith(("/", "https://", "http://")):
            target = candidate_url(base, item)
            if target and ctx.add_candidate(target, "api"):
                targets.add(target)
    return targets


async def inspect_tender_details(args: URLInput, context: ExplorationContext) -> dict:
    ctx = context
    url = normalize_url(args.url)
    if "detail" not in ctx.candidates.get(url, set()):
        raise ToolError("Tender detail URL was not observed")
    response = await ctx.transport.fetch(url, roles={"detail"})
    require_accessible(response)
    if not response.is_html:
        data = bounded_json(response.body, ctx.config.max_json_depth)
        ctx.capabilities.tender_detail_access = True
        return {"url": response.final_url, "sample": json_shape(data)}
    soup = BeautifulSoup(response.body, "html.parser")
    fields = [
        tag.get_text(" ", strip=True)[:100] for tag in soup.find_all(["dt", "th", "h1", "h2"], limit=18)
    ]
    page = describe_page(soup, response.final_url, ctx)
    ctx.capabilities.tender_detail_access = True
    return {"url": response.final_url, "fields": sanitize(fields), **page}


async def inspect_documents(args: URLInput, context: ExplorationContext) -> dict:
    result = await probe_document(normalize_url(args.url), context)
    if result["public_download_verified"]:
        context.capabilities.document_access = True
    return result


def require_accessible(response):
    if response.restriction.is_blocking:
        raise ToolError(response.restriction.restriction_type)
    if not 200 <= response.status < 300:
        raise ToolError(f"Resource returned HTTP {response.status}")
