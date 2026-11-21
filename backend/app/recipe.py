import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, Field, model_validator

from .browser import NETSERVER_FILE_CONTROL, NETSERVER_FILE_LINK, render
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
PLACEHOLDER = re.compile(r"\{(\w+)\}")
PAGINATION_HANDLER = re.compile(
    r"""\s*(?:return\s+)?setTargetAndSubmit\(\s*(?P<quote>['"])"""
    r"(?P<path>/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*[?][A-Za-z0-9_=&.+-]+)"
    r"(?P=quote)\s*(?=[,)])"
)
PAGINATION_KEY = re.compile(
    r"page(?:index|number|num|no)?|offset|cursor|start|selectedTablePage\w+", re.IGNORECASE
)
SENSITIVE_KEY = re.compile(
    r"token|session|secret|auth|key|password|csrf|signature|credential|^(sid|sig)$", re.IGNORECASE
)
PAGINATION_PLACEHOLDERS = {"", "#", "javascript:void(0)"}


class _AutomaticModalBlocked(Blocked):
    pass


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
    url: str | None = None
    fields: dict[str, Selector] = {}


class DocumentsUrl(BaseModel):
    template: str
    kind: Kind = "html"


class DocumentSource(BaseModel):
    files: Selector
    page: Selector | None = None
    modal: Literal["netserver_file_tree"] | None = None


class Recipe(BaseModel):
    method: Literal["documented_api", "discovered_api", "html", "browser"]
    listing: Listing
    detail: Detail | None = None
    documents: Selector | None = None
    documents_page: Selector | None = None
    documents_url: DocumentsUrl | None = None
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
        if (self.documents_page or self.documents_by_host) and self.documents_url:
            raise ValueError("use documents_url without documents_page and documents_by_host")
        if self.detail and self.detail.url:
            _check_template("detail.url", self.detail.url, self.listing.fields.keys())
        if self.documents_url:
            _check_template("documents_url.template", self.documents_url.template,
                            self.listing.fields.keys() | detail_fields.keys())
        documents_kind = "html" if self.documents_page else (self.detail or self.listing).kind
        if self.documents_url:
            documents_kind = self.documents_url.kind
        links = [(self.listing.next_page, self.listing.kind), (self.listing.index, self.listing.kind),
                 (self.documents_page, self.detail.kind if self.detail else "html"), (self.documents, documents_kind)]
        for host in self.documents_by_host:
            if not re.fullmatch(r"[a-z0-9.-]+", host):
                raise ValueError("documents_by_host keys must be lowercase hostnames")
        for selector, kind in links:
            if selector and selector.attribute is None and kind != "json":
                selector.attribute = "href"
        if self.detail and not self.detail.url and "url" not in self.listing.fields:
            raise ValueError("detail needs listing.fields.url or detail.url")
        if self.method.endswith("api") != (self.listing.kind == "json"):
            raise ValueError("API methods use a json listing, html and browser methods do not")
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError):
            raise ValueError(f"unknown timezone {self.timezone}") from None
        return self


def _check_template(name: str, template: str, fields) -> None:
    if not re.match(r"(https?://[^/{}]+(/|$)|/(?!/))", template):
        raise ValueError(f"{name} must be an absolute http(s) URL or start with /")
    if missing := set(PLACEHOLDER.findall(template)) - set(fields):
        raise ValueError(f"{name} uses {sorted(missing)}, which are not mapped fields")


def _fill(template: str, raw: dict) -> str | None:
    names = PLACEHOLDER.findall(template)
    if not all(raw.get(name) for name in names):
        return None
    url = PLACEHOLDER.sub(lambda m: quote(raw[m[1]], safe=""), template)
    return urljoin(raw["_base"], url)


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
            items = [_json_path(item, selector.attribute) if isinstance(item, dict) else None for item in items]
        values = [str(i) for i in items if isinstance(i, (str, int, float)) and not isinstance(i, bool)]
    else:
        elements = [node] if selector.path == "$" else node.select(selector.path)
        values = [e.get(selector.attribute) if selector.attribute else e.get_text(" ") for e in elements]
    values = [" ".join(v.split()) for v in values if isinstance(v, str) and v.strip()]
    if selector.pattern:
        matches = (re.search(selector.pattern, v) for v in values)
        values = [m.group(1) if m.re.groups else m.group(0) for m in matches if m]
    return values


def pagination_handler(tag) -> re.Match[str] | None:
    if tag.name != "a" or "browseForward" not in tag.get("class", ()):
        return None
    placeholder = tag.get("href", "").strip().casefold().rstrip(";")
    if placeholder not in PAGINATION_PLACEHOLDERS:
        return None
    match = PAGINATION_HANDLER.match(tag.get("onclick", ""))
    if match and len(match["path"]) <= 300:
        params = parse_qsl(urlsplit(match["path"]).query, keep_blank_values=True)
        if not any(SENSITIVE_KEY.search(key) for key, _ in params) and any(
            PAGINATION_KEY.fullmatch(key) and value.isdecimal() for key, value in params
        ):
            return match
    return None


def _next_link(page, selector: Selector, kind: Kind) -> tuple[str | None, bool]:
    if kind == "json" or selector.attribute != "href" or selector.pattern:
        return next(iter(_values(page, selector, kind)), None), False
    unresolved = False
    for tag in [page] if selector.path == "$" else page.select(selector.path):
        if match := pagination_handler(tag):
            return match["path"], False
        href = tag.get("href")
        href = " ".join(href.split()) if isinstance(href, str) else ""
        normalized = href.casefold().rstrip(";")
        if normalized not in PAGINATION_PLACEHOLDERS and not normalized.startswith("javascript:"):
            return href, False
        unresolved |= bool(tag.get("onclick")) or normalized not in PAGINATION_PLACEHOLDERS
    return None, unresolved


def _extract(node, fields: dict[str, Selector], kind: Kind) -> dict:
    out = {}
    for name, selector in fields.items():
        if values := _values(node, selector, kind):
            out[name] = " ".join(values) if name == "cpvCodes" else values[0]
    return out


async def _load(kind: Kind, url: str, fetcher: Fetcher):
    if kind == "browser":
        html, _, final = await render(url, fetcher, require_stable=True)
        return BeautifulSoup(html, "html.parser"), final
    resp = await fetcher.get(url)
    if kind == "json":
        return resp.json(), str(resp.url)
    return BeautifulSoup(resp.text, "html.parser"), str(resp.url)


async def _load_detail(detail: Detail, url: str, listing_url: str | None, fetcher: Fetcher):
    try:
        return await _load(detail.kind, url, fetcher)
    except httpx.HTTPStatusError as exc:
        if (detail.kind != "html" or not detail.url or not listing_url or listing_url == url
                or exc.response.status_code not in (404, 410)):
            raise
    return await _load(detail.kind, listing_url, fetcher)


def _with_param(url: str, name: str, step: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    start = int(query.get(name, "1")) if query.get(name, "1").isdigit() else 1
    query[name] = str(start + step)
    return parts._replace(query=urlencode(query)).geturl()


async def apply(
    recipe: Recipe, fetcher: Fetcher, *, max_pages: int | None = None, max_details: int | None = None
) -> tuple[list[dict], bool]:
    listing, raws = recipe.listing, []
    indexed_sample = listing.index is not None and max_pages is not None and max_details is not None
    budget = max_pages or recipe.max_pages * (MAX_INDEX_LISTS if listing.index else 1)
    starts = [listing.url]
    if listing.index:
        page, base = await _load(listing.kind, listing.url, fetcher)
        links = [urljoin(base, u) for u in _values(page, listing.index, listing.kind)]
        starts = list(dict.fromkeys(links))[:MAX_INDEX_LISTS]
        budget -= 1
    complete = bool(starts)
    for index, start in enumerate(starts):
        seen: set[str] = set()
        url, finished = start, False
        reserved = min(len(starts) - index - 1, max(0, budget - 1)) if max_pages is not None else 0
        for page_number in range(min(recipe.max_pages, budget - reserved)):
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
                if indexed_sample:
                    raw["_categoryIndex"] = index
                if raw.get("url"):
                    raw["url"] = urljoin(base, raw["url"])
                if recipe.documents and not recipe.detail and not recipe.documents_url:
                    raw["_docs"] = [urljoin(base, u) for u in _values(row, recipe.documents, listing.kind)]
                raws.append(raw)
            if listing.page_param:
                url = _with_param(start, listing.page_param, page_number + 1)
            elif listing.next_page:
                link, unresolved = _next_link(page, listing.next_page, listing.kind)
                if not link:
                    finished = not unresolved
                    break
                url = urljoin(base, link)
            else:
                finished = True
                break
            if url in seen:
                finished = True
                break
        complete = complete and finished
    attempted, blocked = 0, []
    details = raws
    if recipe.detail and max_details is not None and (indexed_sample or "submissionDeadline" in listing.fields):
        now, tz = datetime.now(UTC), ZoneInfo(recipe.timezone)
        first_rows = {}
        for raw in raws:
            if tender_id := raw.get("id"):
                first_rows.setdefault(tender_id, raw)

        def detail_priority(raw):
            deadline = _datetime(raw.get("submissionDeadline"), recipe.dayfirst, tz)
            return deadline is None or deadline < now

        candidates = (raw for raw in first_rows.values() if raw.get("url") or recipe.detail.url)
        if indexed_sample:
            categories = {}
            for raw in candidates:
                categories.setdefault(raw["_categoryIndex"], []).append(raw)
            for category in categories.values():
                category.sort(key=detail_priority)
            details = [
                category[position]
                for position in range(max(map(len, categories.values()), default=0))
                for category in categories.values()
                if position < len(category)
            ]
        else:
            details = sorted(candidates, key=detail_priority)
    for raw in details[:max_details]:
        detail_url = recipe.detail and (_fill(recipe.detail.url, raw) if recipe.detail.url else raw.get("url"))
        if detail_url:
            attempted += 1
            try:
                page, base = await _load_detail(recipe.detail, detail_url, raw.get("url"), fetcher)
            except Blocked as exc:
                blocked.append(str(exc))
                continue
            except (httpx.HTTPError, ValueError, OSError, PlaywrightError):
                continue
            raw.update(_extract(page, recipe.detail.fields, recipe.detail.kind))
            if (recipe.documents or recipe.documents_by_host) and not recipe.documents_url:
                try:
                    raw["_docs"] = await _document_links(recipe, page, base, fetcher)
                except _AutomaticModalBlocked:
                    raw["_documentPageError"] = True
                except Blocked as exc:
                    if not recipe.documents_by_host:
                        raise
                    raw["_docs"] = []
                    raw["_documentBlock"] = str(exc)
                except (httpx.HTTPError, ValueError, OSError, PlaywrightError):
                    raw["_documentPageError"] = True
        if recipe.documents_url and recipe.documents:
            await _listed_documents(recipe, raw, fetcher)
    if attempted and len(blocked) == attempted:
        raise Blocked(f"detail pages: {blocked[0]}")
    tenders = {}
    for raw in raws:
        if recipe.documents_url and not recipe.documents and (url := _fill(recipe.documents_url.template, raw)):
            raw["_docs"] = [url]
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
    explicit_modal = source is not None and source.modal == "netserver_file_tree"
    links = _link_values(page, selector, kind) if selector and not explicit_modal else []
    if explicit_modal or (not links and kind != "json" and page.select_one(NETSERVER_FILE_CONTROL)):
        try:
            html, _, base = await render(base, fetcher, netserver_files=True)
        except Blocked as exc:
            if explicit_modal:
                raise
            raise _AutomaticModalBlocked(str(exc)) from exc
        if not explicit_modal:
            selector = Selector(path=NETSERVER_FILE_LINK, attribute="href")
        links = _link_values(BeautifulSoup(html, "html.parser"), selector, "html")
    return [urljoin(base, link) for link in links]


async def _listed_documents(recipe: Recipe, raw: dict, fetcher: Fetcher) -> None:
    if not (url := _fill(recipe.documents_url.template, raw)):
        return
    try:
        page, base = await _load(recipe.documents_url.kind, url, fetcher)
    except Blocked as exc:
        raw["_docs"], raw["_documentBlock"] = [], str(exc)
        return
    except (httpx.HTTPError, ValueError, OSError, PlaywrightError):
        raw["_documentPageError"] = True
        return
    raw["_docs"] = [urljoin(base, u) for u in _values(page, recipe.documents, recipe.documents_url.kind)]


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
    if "_documentBlock" in raw:
        tender["_documentBlock"] = raw["_documentBlock"]
    elif raw.get("_documentPageError"):
        tender["_documentPageError"] = True
    elif "_docs" not in raw and (recipe.documents_url or (recipe.detail and (recipe.documents or recipe.documents_by_host))):
        tender["_documentDetailPending"] = True
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
