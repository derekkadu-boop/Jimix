import re
from itertools import islice
from urllib.parse import urlsplit
from xml.etree import ElementTree

import yaml
from bs4 import BeautifulSoup
from openapi_parser import ParserError
from openapi_parser import parse as parse_openapi

from ..context import ExplorationContext, ToolError
from ..models import URLInput
from ..observations import bounded_json, describe_page
from ..phrases import contains_phrase
from ..urls import SECRET_KEY, candidate_url, clean_text, normalize_url
from .inspection import require_accessible

GET_PATH = re.compile(r"\bGET\s+((?:https?://[^\s<>]+|/[a-zA-Z0-9._/-]+)(?:\?[^\s<>]+)?)", re.I)


async def inspect_documentation(args: URLInput, context: ExplorationContext) -> dict:
    ctx = context
    url = normalize_url(args.url)
    depth = ctx.documentation_depth.get(url, 1 if "sitemap" in ctx.candidates.get(url, set()) else None)
    if depth is None:
        raise ToolError("Documentation URL was not observed")
    if depth > ctx.config.max_documentation_depth:
        raise ToolError("Documentation depth limit reached")
    response = await ctx.transport.fetch(url, roles={"documentation", "sitemap"}, page_kind=True)
    require_accessible(response)
    report = parse_documentation(response, ctx, depth)
    if report["format"] != "sitemap":
        ctx.capabilities.documentation_available = True
    ctx.log("Documentation inspected (%s)", report["format"])
    return {"url": response.final_url, **report}


def bounded_yaml(data: bytes, max_depth: int):
    if len(data) > 512_000:
        raise ToolError("YAML exceeds the processing limit")
    depth = 0
    try:
        for index, event in enumerate(yaml.parse(data, Loader=yaml.SafeLoader)):
            if index >= 24_000 or getattr(event, "anchor", None) or getattr(event, "tag", None):
                raise ToolError("YAML aliases, tags, or processing limit rejected")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
                if depth > max_depth:
                    raise ToolError("YAML exceeds processing depth")
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
        return yaml.safe_load(data)
    except ToolError:
        raise
    except (yaml.YAMLError, ValueError, TypeError, RecursionError):
        raise ToolError("Malformed YAML") from None


def _documented_gets(texts, response, ctx: ExplorationContext) -> list[dict]:
    endpoints = []
    seen = set()
    for text in texts:
        for match in GET_PATH.finditer(text):
            target = candidate_url(response.final_url, match[1])
            if target and target not in seen and ctx.add_candidate(target, "api"):
                ctx.documented_endpoints.add(target)
                seen.add(target)
                endpoints.append({"url": target, "method": "GET"})
                if len(endpoints) == 12:
                    return endpoints
    return endpoints


def _parse_sitemap(body: bytes, url: str, ctx: ExplorationContext, depth: int) -> dict:
    if len(body) > 512_000 or re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", body, re.I):
        raise ToolError("Sitemap exceeded parsing limits or supplied unsupported XML declarations")
    try:
        tree = ElementTree.fromstring(body.decode("utf-8-sig"))
    except (ElementTree.ParseError, UnicodeError, ValueError):
        raise ToolError("Malformed sitemap XML") from None
    kind = tree.tag.split("}")[-1]
    if kind not in {"urlset", "sitemapindex"}:
        raise ToolError("XML returned no sitemap index or URL set")
    links = []
    for entry in list(tree)[: ctx.config.max_candidate_urls]:
        node = next((child for child in entry if child.tag.split("}")[-1] == "loc"), None)
        target = candidate_url(url, node.text.strip()) if node is not None and node.text else None
        if not target:
            continue
        role = "sitemap" if kind == "sitemapindex" else "page"
        if ctx.add_candidate(target, role):
            if role == "sitemap":
                ctx.documentation_depth[target] = min(
                    depth + 1, ctx.documentation_depth.get(target, depth + 1)
                )
            links.append({"url": target, "role": role})
    return {"format": "sitemap", "links": links[:12]}


def parse_documentation(response, ctx: ExplorationContext, depth: int) -> dict:
    """Collect documented URLs and return a bounded description for the model."""
    cfg = ctx.config
    body = response.body.lstrip()
    if response.truncated:
        raise ToolError("Documentation exceeded the response limit")
    if response.is_html:
        soup = BeautifulSoup(response.body, "html.parser")
        texts = [block.get_text(" ", strip=True)[:4000] for block in soup.find_all(["pre", "code"], limit=24)]
        page = describe_page(soup, response.final_url, ctx, documentation_depth=depth)
        endpoints = _documented_gets(texts, response, ctx)
        return page | {"endpoints": endpoints, "format": "html"}

    if "xml" in response.content_type or body.startswith((b"<?xml", b"<urlset", b"<sitemapindex")):
        return _parse_sitemap(body, response.final_url, ctx, depth)

    if body.startswith((b"{", b"[")) or "json" in response.content_type:
        data = bounded_json(body, cfg.max_json_depth)
    elif response.final_url.partition("?")[0].endswith((".yaml", ".yml")) or "yaml" in response.content_type:
        data = bounded_yaml(body, cfg.max_json_depth)
    elif response.content_type in {"text/plain", "text/markdown"}:
        text = body[:100_000].decode("utf-8", errors="replace")
        return {
            "format": "text",
            "endpoints": _documented_gets([text], response, ctx),
            "text": clean_text(text[: cfg.max_html_chars]),
        }
    else:
        raise ToolError("Unsupported documentation format")
    if not isinstance(data, dict) or not ("openapi" in data or data.get("swagger") == "2.0"):
        raise ToolError("No OpenAPI or Swagger specification was found")

    swagger = data.get("swagger") == "2.0"
    try:
        if swagger and ("host" not in data or "schemes" not in data):
            document = urlsplit(response.final_url)
            data.setdefault("host", document.netloc)
            data.setdefault("schemes", [document.scheme])
            spec_text = yaml.safe_dump(data)
        else:
            spec_text = body.decode("utf-8-sig")
        # The in-memory loader resolves local references without an external retriever.
        spec = parse_openapi(spec_string=spec_text)
    except (ParserError, UnicodeError, ValueError, TypeError, AttributeError, RecursionError):
        raise ToolError("OpenAPI specification is invalid or contains an unresolved reference") from None

    endpoints = []
    for path, path_item in islice(spec.paths.items(), 120):
        operation = path_item.get
        if operation is None or not path.startswith("/"):
            continue
        params = {
            param.name: bool(param.required)
            for param in [*(path_item.parameters or []), *(operation.parameters or [])]
            if param.location == "query" and len(param.name) <= 50 and not SECRET_KEY.search(param.name)
        }
        security = spec.security if operation.security is None else operation.security
        credentials = bool(security) and {} not in security
        targets = []
        if "{" not in path and not credentials:
            servers = operation.servers or path_item.servers or spec.servers
            for server_url in [server.url for server in servers[:4]] or ["/"]:
                if "{" in server_url:
                    continue
                base = candidate_url(response.final_url, server_url)
                target = candidate_url(base, base.rstrip("/") + path) if base else None
                if target and ctx.add_candidate(target, "api"):
                    targets.append(target)
                    ctx.documented_endpoints.add(target)
                    ctx.documented_parameters.setdefault(target, set()).update(params)
        ok = operation.responses.get("200")
        media = (ok.content or {}).get("application/json") if ok else None
        schema = media.schema_object if media else None
        reference = (schema.ref_name or getattr(schema.type, "value", schema.type)) if schema else None
        endpoints.append(
            {
                "path": clean_text(path)[:180],
                "urls": targets,
                "method": "GET",
                "parameters": [
                    {"name": clean_text(name), "required": required}
                    for name, required in islice(params.items(), 16)
                ],
                "api_authentication_required": credentials,
                "response_schema": clean_text(str(reference))[:120] if reference else "",
                "summary": clean_text(operation.summary or operation.description or "")[:130],
            }
        )
    endpoints.sort(
        key=lambda item: (
            not contains_phrase(item["path"], "procurement_api_path"),
            not bool(item["urls"]),
        )
    )
    schemas = spec.components.schemas if spec.components else None
    fields = {
        clean_text(name)[:70]: [
            clean_text(key)[:70] for key in islice(schema.properties, 16) if not SECRET_KEY.search(key)
        ]
        for name, schema in islice((schemas or {}).items(), 20)
        if not SECRET_KEY.search(name) and schema.properties
    }
    return {"format": "Swagger" if swagger else "OpenAPI", "endpoints": endpoints[:12], "fields": fields}
