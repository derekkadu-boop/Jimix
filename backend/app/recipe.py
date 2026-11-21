import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, Field, model_validator

from .browser import render
from .fetch import Blocked, Fetcher

TENDER_FIELDS = (
    "id",
    "url",
    "title",
    "buyerName",
    "publicationDate",
    "submissionDeadline",
    "status",
    "referenceNumber",
    "description",
    "procedureType",
    "contractNature",
    "cpvCodes",
    "postalCode",
    "city",
    "countryCode",
)
KEYWORDS = json.loads((Path(__file__).parent / "keywords.json").read_text(encoding="utf-8"))
PROCEDURES = tuple(KEYWORDS["procedureType"].items())
NATURES = tuple(KEYWORDS["contractNature"].items())
Kind = Literal["html", "json", "browser"]
MAX_INDEX_LISTS = 50


class Selector(BaseModel):
    path: str
    attribute: str | None = None
    pattern: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def check_pattern(self):
        if self.pattern:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"invalid pattern: {exc}") from None
        return self


class Listing(BaseModel):
    kind: Kind = "html"
    url: str
    records: str
    fields: dict[str, Selector]
    next_page: Selector | None = None
    page_param: str | None = None
    index: Selector | None = None


class Detail(BaseModel):
    kind: Kind = "html"
    fields: dict[str, Selector] = {}


class DocumentSource(BaseModel):
    files: Selector
    page: Selector | None = None


class Recipe(BaseModel):
    method: Literal["documented_api", "discovered_api", "html", "browser"]
    listing: Listing
    detail: Detail | None = None
    documents: Selector | None = None
    documents_page: Selector | None = None
    documents_by_host: dict[str, DocumentSource] = Field(default_factory=dict)
    timezone: str = "UTC"
    dayfirst: bool = True
    max_pages: int = Field(20, ge=1, le=100)

    @model_validator(mode="after")
    def check_fields(self):
        detail_fields = self.detail.fields if self.detail else {}
        if unknown := (self.listing.fields.keys() | detail_fields.keys()) - set(TENDER_FIELDS):
            raise ValueError(f"unknown fields {sorted(unknown)}, use only {TENDER_FIELDS}")
        if not re.match(r"https?://[^/]+", self.listing.url):
            raise ValueError("listing.url must be an absolute http(s) URL")
        if "id" not in self.listing.fields:
            raise ValueError("listing.fields needs id")
        if "id" in detail_fields:
            raise ValueError("id must come from the listing, not the detail page")
        if self.documents_page and not (self.detail and (self.documents or self.documents_by_host)):
            raise ValueError("documents_page needs detail and a document selector")
        if self.documents_by_host and not self.detail:
            raise ValueError("documents_by_host needs detail")
        documents_kind = "html" if self.documents_page else (self.detail or self.listing).kind
        links = [(self.listing.next_page, self.listing.kind), (self.listing.index, self.listing.kind),
                 (self.documents_page, self.detail.kind if self.detail else "html"), (self.documents, documents_kind)]
        for host in self.documents_by_host:
            if not re.fullmatch(r"[a-z0-9.-]+", host):
                raise ValueError("documents_by_host keys must be lowercase hostnames")
        for selector, kind in links:
            if selector and selector.attribute is None and kind != "json":
                selector.attribute = "href"
        if self.detail and "url" not in self.listing.fields:
            raise ValueError("detail needs listing.fields.url")
        if self.method.endswith("api") != (self.listing.kind == "json"):
            raise ValueError("API methods use a json listing, html and browser methods do not")
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError):
            raise ValueError(f"unknown timezone {self.timezone}") from None
        return self


def _json_path(value, path: str):
    path = path.removeprefix("$").removeprefix(".")
    for part in path.split(".") if path else []:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit():
            value = value[int(part)] if int(part) < len(value) else None
        else:
            return None
    return value


def _values(node, selector: Selector, kind: Kind) -> list[str]:
    if kind == "json":
        found = _json_path(node, selector.path)
        items = found if isinstance(found, list) else [found]
        if selector.attribute:
            items = [item.get(selector.attribute) if isinstance(item, dict) else None for item in items]
        values = [str(i) for i in items if isinstance(i, (str, int, float)) and not isinstance(i, bool)]
    else:
        elements = [node] if selector.path == "$" else node.select(selector.path)
        values = [e.get(selector.attribute) if selector.attribute else e.get_text(" ") for e in elements]
    values = [" ".join(v.split()) for v in values if isinstance(v, str) and v.strip()]
    if selector.pattern:
        matches = (re.search(selector.pattern, v) for v in values)
        values = [m.group(1) if m.re.groups else m.group(0) for m in matches if m]
    return values


def _extract(node, fields: dict[str, Selector], kind: Kind) -> dict:
    out = {}
    for name, selector in fields.items():
        if values := _values(node, selector, kind):
            out[name] = " ".join(values) if name == "cpvCodes" else values[0]
    return out


async def _load(kind: Kind, url: str, fetcher: Fetcher):
    if kind == "browser":
        html, _, final = await render(url, fetcher)
        return BeautifulSoup(html, "html.parser"), final
    resp = await fetcher.get(url)
    if kind == "json":
        return resp.json(), str(resp.url)
    return BeautifulSoup(resp.text, "html.parser"), str(resp.url)


def _with_param(url: str, name: str, step: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    start = int(query.get(name, "1")) if query.get(name, "1").isdigit() else 1
    query[name] = str(start + step)
    return parts._replace(query=urlencode(query)).geturl()


async def apply(
    recipe: Recipe, fetcher: Fetcher, *, max_pages: int | None = None, max_details: int | None = None
) -> tuple[list[dict], bool]:
    listing, raws, seen = recipe.listing, [], set()
    budget = max_pages or recipe.max_pages * (MAX_INDEX_LISTS if listing.index else 1)
    starts = [listing.url]
    if listing.index:
        page, base = await _load(listing.kind, listing.url, fetcher)
        links = [urljoin(base, u) for u in _values(page, listing.index, listing.kind)]
        starts = list(dict.fromkeys(links))[:MAX_INDEX_LISTS]
        budget -= 1
    complete = bool(starts)
    for start in starts:
        url, finished = start, False
        for page_number in range(min(recipe.max_pages, budget)):
            budget -= 1
            seen.add(url)
            page, base = await _load(listing.kind, url, fetcher)
            rows = _json_path(page, listing.records) if listing.kind == "json" else page.select(listing.records)
            if not isinstance(rows, list):
                raise TypeError(f"records {listing.records!r} did not give a list")
            if not rows:
                finished = True
                break
            for row in rows:
                raw = _extract(row, listing.fields, listing.kind) | {"_base": base}
                if recipe.documents and not recipe.detail:
                    raw["_docs"] = [urljoin(base, u) for u in _values(row, recipe.documents, listing.kind)]
                raws.append(raw)
            if listing.page_param:
                url = _with_param(start, listing.page_param, page_number + 1)
            elif listing.next_page and (links := _values(page, listing.next_page, listing.kind)):
                url = urljoin(base, links[0])
            else:
                finished = True
                break
            if url in seen:
                finished = True
                break
        complete = complete and finished
    attempted, blocked = 0, []
    for raw in raws[:max_details]:
        if not recipe.detail or not raw.get("url"):
            continue
        raw["url"] = urljoin(raw["_base"], raw["url"])
        attempted += 1
        try:
            page, base = await _load(recipe.detail.kind, raw["url"], fetcher)
        except Blocked as exc:
            blocked.append(str(exc))
            continue
        except (httpx.HTTPError, ValueError, OSError, PlaywrightError):
            continue
        raw.update(_extract(page, recipe.detail.fields, recipe.detail.kind))
        if recipe.documents or recipe.documents_by_host:
            try:
                raw["_docs"] = await _document_links(recipe, page, base, fetcher)
            except Blocked as exc:
                if not recipe.documents_by_host:
                    raise
                raw["_docs"] = []
                raw["_documentBlock"] = str(exc)
            except (httpx.HTTPError, ValueError, OSError):
                raw["_documentPageError"] = True
    if attempted and len(blocked) == attempted:
        raise Blocked(f"detail pages: {blocked[0]}")
    tenders = {}
    for raw in raws:
        if (tender := normalize(raw, recipe)) and tender["id"] not in tenders:
            tenders[tender["id"]] = tender
    return list(tenders.values()), complete


def _link_values(node, selector: Selector, kind: Kind) -> list[str]:
    if selector.attribute is None and kind != "json":
        selector = selector.model_copy(update={"attribute": "href"})
    return _values(node, selector, kind)


async def _follow_document_page(selector: Selector, page, base: str, kind: Kind, fetcher: Fetcher):
    if links := _link_values(page, selector, kind):
        return await _load("html", urljoin(base, links[0]), fetcher)
    return None


async def _document_links(recipe: Recipe, page, base: str, fetcher: Fetcher) -> list[str]:
    kind = recipe.detail.kind
    source = recipe.documents_by_host.get(urlsplit(base).hostname)
    if not source and recipe.documents_page:
        result = await _follow_document_page(recipe.documents_page, page, base, kind, fetcher)
        if result is None:
            return []
        page, base = result
        kind = "html"
        source = recipe.documents_by_host.get(urlsplit(base).hostname)
    if source and source.page:
        result = await _follow_document_page(source.page, page, base, kind, fetcher)
        if result is None:
            return []
        page, base = result
        kind = "html"
    selector = source.files if source else recipe.documents
    return [urljoin(base, u) for u in _link_values(page, selector, kind)] if selector else []


def _datetime(text: str | None, dayfirst: bool, tz: ZoneInfo) -> datetime | None:
    if not text or not re.search(r"(19|20)\d\d", text):
        return None
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        try:
            value = dateparser.parse(text, dayfirst=dayfirst, fuzzy=True)
        except (ValueError, OverflowError):
            return None
    return value if value.tzinfo else value.replace(tzinfo=tz)


def _keyword(text: str | None, table) -> str | None:
    text = (text or "").casefold()
    return next((value for value, words in table if any(w in text for w in words)), None) if text else None


def check(tenders: list[dict], previous: int = 0) -> str | None:
    if not tenders:
        return "no tenders found"
    incomplete = sum(not (t["title"] and t["url"]) for t in tenders)
    if incomplete * 2 > len(tenders):
        return f"{incomplete} of {len(tenders)} tenders lack a title or url"
    if previous >= 5 and len(tenders) < previous * 0.3:
        return f"tender count dropped from {previous} to {len(tenders)}"
    return None


def normalize(raw: dict, recipe: Recipe) -> dict | None:
    if not raw.get("id"):
        return None
    tz = ZoneInfo(recipe.timezone)
    published = _datetime(raw.get("publicationDate"), recipe.dayfirst, tz)
    deadline = _datetime(raw.get("submissionDeadline"), recipe.dayfirst, tz)
    has_time = bool(re.search(r"\d:\d\d", raw.get("submissionDeadline", "")))
    status_text = (raw.get("status") or "").casefold()
    if any(word in status_text for word in KEYWORDS["withdrawn"]):
        status = "withdrawn"
    elif any(word in status_text for word in KEYWORDS["closed"]) or (deadline and deadline < datetime.now(UTC)):
        status = "closed"
    else:
        status = "open"
    tender = {
        "id": raw["id"],
        "url": urljoin(raw["_base"], raw["url"]) if raw.get("url") else None,
        "title": raw.get("title"),
        "buyerName": raw.get("buyerName"),
        "publicationDate": published.date().isoformat() if published else None,
        "submissionDeadline": deadline.isoformat() if deadline and has_time else None,
        "status": status,
        "documents": [],
        "_documentUrls": list(dict.fromkeys(raw.get("_docs", []))),
    }
    if recipe.documents_by_host and "_docs" in raw:
        tender["_documentChecked"] = True
    if raw.get("_documentPageError"):
        tender["_documentPageError"] = True
    if "_documentBlock" in raw:
        tender["_documentBlock"] = raw["_documentBlock"]
    for name in ("referenceNumber", "description"):
        if raw.get(name):
            tender[name] = raw[name]
    if procedure := _keyword(raw.get("procedureType"), PROCEDURES):
        tender["procedureType"] = procedure
    if nature := _keyword(raw.get("contractNature"), NATURES):
        tender["contractNature"] = nature
    if codes := re.findall(r"(?<!\d)(\d{8})(?:-\d)?(?!\d)", raw.get("cpvCodes", "")):
        tender["cpvCodes"] = list(dict.fromkeys(codes))
    place = {name: raw[name] for name in ("postalCode", "city") if raw.get(name)}
    if re.fullmatch(r"[A-Z]{2}", country := raw.get("countryCode", "").strip().upper()):
        place["countryCode"] = country
    if place:
        tender["placeOfPerformance"] = place
    return tender
