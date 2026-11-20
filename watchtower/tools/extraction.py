import re
from urllib.parse import parse_qsl, urlencode

import httpx
import soupsieve
from bs4 import BeautifulSoup, Tag
from pydantic import Field

from ..browser import inspect_rendered_page
from ..context import ExplorationContext, StopExploration, ToolError
from ..models import (
    REQUIRED_FIELDS,
    ExtractionMethod,
    ExtractionRecipeSpec,
    FieldMapping,
    Model,
    Retrieval,
    Short,
    StopReason,
    URLInput,
)
from ..observations import PAGE_ROLES, bounded_json, describe_page, json_shape
from ..phrases import contains_phrase
from ..urls import SECRET_KEY, UnsafeURL, candidate_url, clean_text, normalize_url, sanitize

MAX_RECORDS = 25
API_ROLES = {"api", "pagination", "detail"}


class APIInput(URLInput):
    records: Short | None = None
    fields: dict[Short, FieldMapping] = Field(default_factory=dict, max_length=16)
    parameters: dict[Short, Short] = Field(default_factory=dict, max_length=10)


class RecipeInput(Model):
    recipe: ExtractionRecipeSpec


async def test_api(args: APIInput, context: ExplorationContext) -> dict:
    ctx = context
    url = normalize_url(args.url)
    target = public_api_url(url, args.parameters, ctx)
    path, fields = args.records, args.fields
    explicit = bool(path and fields)
    if explicit and not set(REQUIRED_FIELDS) <= fields.keys():
        raise ToolError("Extraction requires mappings for id and title")
    data, final_url = await load_source("api", target, ctx, blocking=explicit)
    result = {"url": final_url, "verified": False}
    if not explicit:
        result["sample"] = json_shape(data)
        inferred = _infer_listing(data)
        if inferred:
            path, (identifier, title) = inferred
            fields = {"id": FieldMapping(path=identifier), "title": FieldMapping(path=title)}
    if not explicit and isinstance(data, dict):
        result["pagination_keys"] = [
            key
            for key in (
                "next",
                "nextPage",
                "page",
                "offset",
                "total",
                "count",
                "totalPages",
                "hasMore",
                "pagination",
                "links",
            )
            if key in data
        ]
    if not path or not fields:
        return result
    method = (
        ExtractionMethod.DOCUMENTED_API
        if url in ctx.documented_endpoints
        else ExtractionMethod.DISCOVERED_API
    )
    recipe = ExtractionRecipeSpec(
        method=method,
        listing=Retrieval(kind="api", url=url, records=path, fields=fields, parameters=args.parameters),
    )
    try:
        _, validation = validate_listing(recipe, data, final_url, "api-probe")
    except ToolError:
        if explicit:
            raise
        return result | {"note": "Structured JSON observed. Supply explicit records, id, and title mappings."}
    if validation["verified"]:
        ctx.capabilities.public_api_verified = True
        ctx.remember_candidate(
            recipe,
            validation
            | {
                "verified": False,
                "api_probe_verified": True,
                "reason": "Run test_extraction to verify this reusable recipe",
            },
        )
        ctx.log("API returned %d tender record(s)", validation["sample_count"])
    return result | {"api_probe": validation, "verified": validation["verified"]}


def _infer_listing(data, path: str = "$", depth: int = 0):
    if isinstance(data, list) and data and isinstance(data[0], dict):
        keys = {key.lower(): key for key in data[0]}
        ids = ("id", "tenderid", "noticeid", "reference", "referencenumber", "ocid", "identifier")
        titles = ("title", "tendertitle", "noticetitle", "name", "subject")
        identifier = next((keys[key] for key in ids if key in keys), None)
        title = next((keys[key] for key in titles if key in keys), None)
        if identifier and title:
            return path, (identifier, title)
    if depth < 3 and isinstance(data, dict):
        for key, nested in list(data.items())[:16]:
            if isinstance(key, str) and "." not in key:
                result = _infer_listing(nested, f"{path}.{key}", depth + 1)
                if result:
                    return result
    return None


async def test_extraction(args: RecipeInput, context: ExplorationContext, evidence_id: str) -> dict:
    ctx = context
    recipe = canonical_recipe(args.recipe)
    ctx.remember_candidate(recipe, {"verified": False, "reason": "Validation did not complete"})
    try:
        validation = await validate_recipe(recipe, ctx, evidence_id)
    except ToolError as exc:
        ctx.remember_candidate(recipe, {"verified": False, "reason": str(exc), "evidence_id": evidence_id})
        raise
    if validation["verified"]:
        ctx.validations[evidence_id] = recipe, validation
        ctx.remember_candidate(recipe, validation)
        listing = recipe.listing.kind
        if listing == "api":
            ctx.capabilities.public_api_verified = True
        elif listing == "html":
            ctx.capabilities.server_rendered_tender_listings = True
            ctx.capabilities.javascript_rendering_required = False
        elif listing == "browser":
            # Successful rendering alone does not establish that JavaScript is required.
            cached = ctx.transport.cache.get((recipe.listing.url, ctx.config.user_agent, "GET", False))
            if cached and cached.is_html:
                raw = BeautifulSoup(cached.body, "html.parser")
                if not scoped_select(raw, recipe.listing.records, limit=1):
                    ctx.capabilities.javascript_rendering_required = True
                    ctx.capabilities.server_rendered_tender_listings = False
        if validation["pagination_verified"]:
            ctx.capabilities.pagination_available = True
        if validation["detail_verified"]:
            ctx.capabilities.tender_detail_access = True
        if validation["document_verified"]:
            ctx.capabilities.document_access = True
        ctx.log("Extraction validation passed (%s)", recipe.method.value)
    else:
        ctx.remember_candidate(recipe, validation)
    return validation


def canonical_recipe(recipe: ExtractionRecipeSpec) -> ExtractionRecipeSpec:
    listing = recipe.listing.model_copy(update={"url": normalize_url(recipe.listing.url)})
    # Sanitize every persisted free-text field and reject mappings that would change under redaction.
    clean = sanitize(recipe.model_copy(update={"listing": listing}).model_dump(mode="json"))
    canonical = ExtractionRecipeSpec.model_validate(clean)
    if (
        canonical.listing != listing
        or canonical.detail != recipe.detail
        or canonical.pagination != recipe.pagination
    ):
        raise ToolError("Recipe contains unsupported or sensitive fields")
    return canonical


def _json_path(value, path: str):
    if path == "$":
        return value
    for segment in path.removeprefix("$.").split("."):
        if isinstance(value, dict):
            value = value.get(segment)
        elif isinstance(value, list) and segment.isascii() and segment.isdigit() and len(segment) <= 4:
            index = int(segment)
            value = value[index] if index < len(value) else None
        else:
            return None
    return value


def scoped_select(source, selector: str, *, limit: int = MAX_RECORDS):
    if (
        len(selector) > 250
        or re.search(r":(?!first-child\b|last-child\b|nth-child\([0-9]{1,3}\))", selector)
        or selector.count(">") + selector.count("+") + selector.count("~") > 8
        or selector.count(",") > 3
    ):
        raise ToolError("Selector exceeds supported CSS complexity")
    try:
        return source.select(selector, limit=limit)
    except (soupsieve.SelectorSyntaxError, NotImplementedError, ValueError):
        raise ToolError("Invalid or unsupported CSS selector") from None


def _value(item, mapping: FieldMapping, kind: str):
    if SECRET_KEY.search(mapping.path) or mapping.attribute and SECRET_KEY.search(mapping.attribute):
        raise ToolError("Mappings may not target credential fields")
    if kind == "api":
        if mapping.attribute:
            raise ToolError("JSON field mappings do not take HTML attributes")
        return _json_path(item, mapping.path)
    if mapping.path == "$":
        element = item
    else:
        matched = scoped_select(item, mapping.path, limit=1)
        element = matched[0] if matched else None
    if element is None:
        return None
    return element.get(mapping.attribute) if mapping.attribute else element.get_text(" ", strip=True)


def _mapped(item, fields: dict[str, FieldMapping], kind: str) -> dict:
    result = {}
    for name, mapping in fields.items():
        if SECRET_KEY.search(name):
            raise ToolError("Credential fields cannot be mapped")
        value = _value(item, mapping, kind)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            value = str(value).strip()
            if value.startswith(("https://", "http://")):
                try:
                    value = normalize_url(value)
                except UnsafeURL:
                    continue
            else:
                value = clean_text(value)[:500]
            if value and "[redacted" not in value:
                result[name] = value
    return result


def public_api_url(url: str, parameters: dict[str, str], ctx: ExplorationContext) -> str:
    url = normalize_url(url)
    if url not in ctx.candidates or "api" not in ctx.candidates[url]:
        raise ToolError("The API URL must appear in documentation, a page, or browser traffic")
    if not parameters:
        return url
    if not parameters.keys() <= ctx.documented_parameters.get(url, set()):
        raise ToolError("API parameter names must be documented or included in the observed URL")
    parsed = httpx.URL(url)
    query = dict(parse_qsl(parsed.query.decode("ascii"))) | parameters
    target = normalize_url(str(parsed.copy_with(query=urlencode(query).encode("ascii"))))
    if target != url:
        if not ctx.add_candidate(target, "api"):
            raise ToolError("Candidate URL limit reached")
    return target


async def load_source(kind: str, url: str, ctx: ExplorationContext, *, blocking: bool = False):
    if kind == "browser":
        observed = await inspect_rendered_page(url, ctx, blocking=blocking)
        if observed.truncated:
            raise ToolError("Rendered DOM exceeded the response limit")
        return BeautifulSoup(observed.html, "html.parser"), observed.final_url
    response = await ctx.transport.fetch(
        url,
        roles=API_ROLES if kind == "api" else PAGE_ROLES,
        blocking=blocking,
        page_kind=False if kind == "api" else None,
    )
    if response.restriction.is_blocking:
        if blocking:
            raise StopExploration(StopReason(response.restriction.restriction_type))
        raise ToolError(response.restriction.restriction_type)
    if response.status != 200 or response.truncated:
        raise ToolError("Source is unavailable or exceeds the response limit")
    if kind == "api":
        data = bounded_json(response.body, ctx.config.max_json_depth)
        if isinstance(data, (dict, list)):
            ctx.capabilities.public_api_observed = True
        return data, response.final_url
    if not response.is_html:
        raise ToolError("Expected a public HTML page")
    return BeautifulSoup(response.body, "html.parser"), response.final_url


def _record_rows(source, kind: str, path: str):
    if kind == "api":
        data = _json_path(source, path)
        if not isinstance(data, list):
            raise ToolError("Records path did not resolve to a JSON list")
        return data[:MAX_RECORDS]
    return scoped_select(source, path)


def _validate_records(
    rows, source, kind: str, fields: dict[str, FieldMapping], url: str, path: str
) -> list[dict]:
    records = [_mapped(row, fields, kind) for row in rows]
    if not records:
        return records
    if not all(all(record.get(field) for field in REQUIRED_FIELDS) for record in records):
        raise ToolError("Sample records did not all contain a mapped ID and title")
    identifiers = [record["id"] for record in records]
    if len(identifiers) != len(set(identifiers)) or any(
        value == "0" or value.lower() in {"null", "none", "n/a"} for value in identifiers
    ):
        raise ToolError("Sample IDs are missing, placeholders, or duplicated")
    if any(record["id"] == record["title"] for record in records):
        raise ToolError("ID and title mappings must identify distinct fields")
    headings = (
        [tag.get_text(" ", strip=True)[:250] for tag in source.find_all(["title", "h1"], limit=3)]
        if (isinstance(source, BeautifulSoup))
        else []
    )
    hints = " ".join(
        [url, path, *headings]
        + [
            " ".join(row.keys())
            if isinstance(row, dict)
            else " ".join([str(row.name), str(row.get("class", "")), str(row.get("id", ""))])
            if isinstance(row, Tag)
            else ""
            for row in rows[:5]
        ]
        + [record["title"] for record in records[:5]]
    ).lower()
    if not (contains_phrase(hints, "tender_record") or contains_phrase(hints, "tender_label")):
        raise ToolError("Sample data does not contain recognizable procurement markers")
    return records


def _next_url(source, recipe: ExtractionRecipeSpec, url: str, ctx: ExplorationContext) -> str:
    pagination = recipe.pagination
    kind = recipe.listing.kind
    if pagination.kind == "next":
        value = _value(source, FieldMapping(path=pagination.path, attribute=pagination.attribute), kind)
        next_url = candidate_url(url, str(value)) if isinstance(value, str) else None
        if not next_url or next_url == url or next_url.split("/", 3)[:3] != url.split("/", 3)[:3]:
            raise ToolError("Pagination did not expose a distinct same-origin next page")
    else:
        if kind != "api" or pagination.path not in ctx.documented_parameters.get(recipe.listing.url, set()):
            raise ToolError("Numeric pagination must name a documented API parameter")
        parsed = httpx.URL(url)
        query = dict(parse_qsl(parsed.query.decode("ascii")))
        if pagination.path not in query or not re.fullmatch(r"[0-9]{1,9}", query[pagination.path]):
            raise ToolError("Numeric pagination requires an explicit starting value")
        query[pagination.path] = str(int(query[pagination.path]) + pagination.increment)
        next_url = normalize_url(str(parsed.copy_with(query=urlencode(query).encode("ascii"))))
    if not ctx.add_candidate(next_url, "pagination"):
        raise ToolError("Candidate URL limit reached")
    return next_url


async def probe_document(url: str, ctx: ExplorationContext) -> dict:
    if "document" not in ctx.candidates.get(url, set()):
        raise ToolError("Document URL was not observed in a public page or validated recipe")
    # A range-limited GET establishes actual download behavior even if HEAD is unsupported.
    result = await ctx.transport.fetch(url, roles={"document"}, probe=True, page_kind=False)
    allowed = (
        result.status in {200, 206}
        and bool(result.body)
        and not result.is_html
        and not result.restriction.restriction_detected
    )
    kind = result.content_type
    identified = (
        kind.startswith("application/")
        and kind not in {"application/json", "application/problem+json", "application/xhtml+xml"}
        or kind in {"text/csv", "text/tab-separated-values"}
        or result.body.startswith((b"%PDF-", b"PK\x03\x04"))
    )
    return {
        "url": result.final_url,
        "status": result.status,
        "public_download_verified": allowed and identified,
        "content_type": kind,
        "bytes_read": len(result.body),
        "range_honored": result.status == 206,
        "truncated": result.truncated,
    }


def validate_listing(
    recipe: ExtractionRecipeSpec, source, url: str, evidence_id: str
) -> tuple[list[dict], dict]:
    listing = recipe.listing
    rows = _record_rows(source, listing.kind, listing.records)
    records = _validate_records(rows, source, listing.kind, listing.fields, url, listing.records)
    result = {
        "evidence_id": evidence_id,
        "verified": bool(records),
        "method": recipe.method.value,
        "sample_count": len(records),
        "required_fields": list(REQUIRED_FIELDS),
        "distinct_ids": len({record["id"] for record in records}),
        "listing_url": url,
        "records": [{key: row[key][:150] for key in REQUIRED_FIELDS} for row in records[:3]],
        "pagination_verified": None,
        "detail_verified": None,
        "document_verified": None,
    }
    if not records:
        result["reason"] = "No live tender records were available at the selected records path"
    return records, result


async def validate_recipe(recipe: ExtractionRecipeSpec, ctx: ExplorationContext, evidence_id: str) -> dict:
    listing = recipe.listing
    if not set(REQUIRED_FIELDS) <= listing.fields.keys():
        raise ToolError("Extraction requires mappings for id and title")
    if recipe.method == ExtractionMethod.DOCUMENTED_API and listing.url not in ctx.documented_endpoints:
        raise ToolError("No documentation evidence for this API endpoint")
    url = public_api_url(listing.url, listing.parameters, ctx) if listing.kind == "api" else listing.url
    if listing.kind != "api" and listing.parameters:
        raise ToolError("HTML and browser query parameters must be in the observed URL")
    source, final_url = await load_source(listing.kind, url, ctx, blocking=True)
    if listing.kind != "api":
        describe_page(str(source), final_url, ctx)
    records, result = validate_listing(recipe, source, final_url, evidence_id)
    if not records:
        return result
    if recipe.pagination:
        next_url = _next_url(source, recipe, final_url, ctx)
        next_source, next_final = await load_source(listing.kind, next_url, ctx)
        second = _validate_records(
            _record_rows(next_source, listing.kind, listing.records),
            next_source,
            listing.kind,
            listing.fields,
            next_final,
            listing.records,
        )
        if not second or {row["id"] for row in records} & {row["id"] for row in second}:
            raise ToolError("The next page did not contain distinct live records")
        result["pagination_verified"] = True
    details = {}
    detail_base = final_url
    if recipe.detail:
        value = next(
            (row.get(recipe.detail.url_field) for row in records if row.get(recipe.detail.url_field)), ""
        )
        target = candidate_url(final_url, value)
        if not target or target == final_url:
            raise ToolError("No distinct tender detail URL was extracted from a live record")
        if not ctx.add_candidate(target, "detail"):
            raise ToolError("Candidate URL limit reached")
        detail, detail_base = await load_source(recipe.detail.kind, target, ctx)
        details = _mapped(detail, recipe.detail.fields, recipe.detail.kind)
        if not details or not all(key in details for key in recipe.detail.fields):
            raise ToolError("Declared detail fields were missing on the live tender detail page")
        result["detail_verified"] = True
    if recipe.document_field:
        raw = details.get(recipe.document_field) or next(
            (row[recipe.document_field] for row in records if row.get(recipe.document_field)), ""
        )
        target = candidate_url(detail_base if recipe.document_field in details else final_url, raw)
        if not target or not ctx.add_candidate(target, "document"):
            raise ToolError("No valid document URL was extracted from a live tender")
        doc = await probe_document(target, ctx)
        if not doc["public_download_verified"]:
            raise ToolError("The declared attachment did not return a public document")
        result["document_verified"] = True
    return result
