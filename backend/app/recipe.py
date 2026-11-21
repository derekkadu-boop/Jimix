import json
import logging
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

from .browser import render
from .fetch import Blocked, Fetcher

log = logging.getLogger("watchtower.recipe")
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
    method: Literal["GET", "POST"] = "GET"
    body: dict | None = None
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
        if self.listing.method == "POST" and (self.listing.kind != "json" or self.listing.body is None):
            raise ValueError("POST listings need kind json and a JSON body")
        if self.listing.method == "POST" and self.listing.index:
            raise ValueError("index is not supported for POST listings")
        if self.listing.method == "GET" and self.listing.body is not None:
            raise ValueError("listing.body is only for POST listings")
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


JSON_PATH = re.compile(r"(?:\.?[^.\[\]]+)?(?:\[(?:\*|\d+)\])*(?:\.[^.\[\]]+(?:\[(?:\*|\d+)\])*)*")


def _path_ok(path: str) -> bool:
    rest = path.removeprefix("$")
    return bool(JSON_PATH.fullmatch("" if rest == "." else rest))


def _json_path(value, path: str):
    if not _path_ok(path):
        return None
    rest = path.removeprefix("$")
    return _walk(value, [part for part in re.sub(r"\[(\*|\d+)\]", r".\1", rest).split(".") if part])


def bad_json_path(recipe: "Recipe") -> str | None:
    listing, detail = recipe.listing, recipe.detail
    documents_kind = "html" if recipe.documents_page else (detail or listing).kind
    if recipe.documents_url:
        documents_kind = recipe.documents_url.kind
    groups = [(listing.kind, [Selector(path=listing.records), *listing.fields.values(), listing.next_page,
                              listing.index]),
              (detail.kind if detail else "html", list(detail.fields.values()) if detail else []),
              (documents_kind, [recipe.documents])]
    for kind, selectors in groups:
        for selector in selectors if kind == "json" else []:
            for path in (selector.path, selector.attribute) if selector else ():
                if path and not _path_ok(path):
                    return path
    return None


def _walk(value, parts: list[str]):
    for i, part in enumerate(parts):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif part == "*":
            items = list(value.values()) if isinstance(value, dict) else value
            if not isinstance(items, list):
                return None
            found = []
            for item in items:
                result = _walk(item, parts[i + 1:]) if parts[i + 1:] else item
                found.extend(result if isinstance(result, list) and parts[i + 1:] else [result])
            return [v for v in found if v is not None]
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
            picked = []
            for item in items:
                result = _json_path(item, selector.attribute) if isinstance(item, (dict, list)) else item
                picked.extend(result if isinstance(result, list) else [result])
            items = picked
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


async def _load(kind: Kind, url: str, fetcher: Fetcher, body: dict | None = None):
    if body is not None:
        resp = await fetcher.post_json(url, body)
        return resp.json(), str(resp.url)
    if kind == "browser":
        html, _, final = await render(url, fetcher, require_stable=True)
        return BeautifulSoup(html, "html.parser"), final
    resp = await fetcher.get(url)
    if kind == "json":
        return resp.json(), str(resp.url)
    return BeautifulSoup(resp.text, "html.parser"), str(resp.url)


def _with_body_param(body: dict, path: str, step: int) -> dict:
    body = json.loads(json.dumps(body))
    *parents, key = path.removeprefix("$.").split(".")
    node = body
    for part in parents:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            raise TypeError(f"page_param {path!r} does not point into a JSON object")
    current = node.get(key, 1)
    if isinstance(current, bool) or not str(current).lstrip("-").isdigit():
        current = 1
    value = int(current) + step
    node[key] = str(value) if isinstance(current, str) else value
    return body


def _with_param(url: str, name: str, step: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    start = int(query.get(name, "1")) if query.get(name, "1").isdigit() else 1
    query[name] = str(start + step)
    return parts._replace(query=urlencode(query)).geturl()


NEXT_PAGE_LABELS = {"next", "next page", "weiter", "nächste seite", "suivant", "page suivante",
                    "volgende", "volgende pagina", "siguiente", "nästa", "następna", "seuraava"}


def _has_next_page(page, base: str) -> bool:
    current = urlsplit(base)
    original = sorted(parse_qsl(current.query, keep_blank_values=True))
    for link in page.select("a[href], link[rel][href]"):
        if link.find_parent("template") or link.has_attr("hidden") or link.find_parent(attrs={"hidden": True}):
            continue
        try:
            target = urlsplit(urljoin(base, link["href"]))
        except ValueError:
            continue
        if (target.scheme, target.netloc) != (current.scheme, current.netloc):
            continue
        if (target.path, target.query) == (current.path, current.query):
            continue
        if "next" in {part.lower() for part in link.get("rel", [])}:
            return True
        label = link.get("aria-label") or link.get_text(" ", strip=True)
        if " ".join(label.casefold().split()) in NEXT_PAGE_LABELS:
            return True
        if target.path != current.path:
            continue
        query = parse_qsl(target.query, keep_blank_values=True)
        for name, value in query:
            if name.lower() not in {"page", "p", "offset", "start"} or not value.isascii() or not value.isdigit():
                continue
            previous = next((v for k, v in original if k == name), "0")
            if (previous.isascii() and previous.isdigit() and len(previous) <= 9 and len(value) <= 9
                    and int(value) > int(previous)
                    and sorted((k, v) for k, v in query if k != name)
                    == [(k, v) for k, v in original if k != name]):
                return True
    return False


async def apply(
    recipe: Recipe, fetcher: Fetcher, *, max_pages: int | None = None, max_details: int | None = None,
    warnings: list[str] | None = None,
) -> tuple[list[dict], bool]:
    listing, raws = recipe.listing, []
    budget = max_pages or recipe.max_pages * (MAX_INDEX_LISTS if listing.index else 1)
    starts = [listing.url]
    if listing.index:
        page, base = await _load(listing.kind, listing.url, fetcher)
        links = [urljoin(base, u) for u in _values(page, listing.index, listing.kind)]
        starts = list(dict.fromkeys(links))[:MAX_INDEX_LISTS]
        budget -= 1
    complete = bool(starts)
    for start in starts:
        seen: set[str] = set()
        url, finished, body = start, False, listing.body if listing.method == "POST" else None
        for page_number in range(min(recipe.max_pages, budget)):
            budget -= 1
            seen.add(url + json.dumps(body, sort_keys=True))
            try:
                page, base = await _load(listing.kind, url, fetcher, body)
            except httpx.HTTPStatusError as exc:
                if page_number == 0 or not 400 <= exc.response.status_code < 500:
                    raise
                log.info("listing paging stopped at page %d: HTTP %d", page_number + 1, exc.response.status_code)
                break
            rows = _json_path(page, listing.records) if listing.kind == "json" else page.select(listing.records)
            if not isinstance(rows, list):
                raise TypeError(f"records {listing.records!r} did not give a list")
            if not rows:
                finished = True
                break
            if (warnings is not None and listing.kind != "json" and not listing.page_param
                    and not listing.next_page and _has_next_page(page, base)):
                warning = "Listing has a likely next-page link but no next_page or page_param is configured."
                if warning not in warnings:
                    warnings.append(warning)
            for row in rows:
                raw = _extract(row, listing.fields, listing.kind) | {"_base": base}
                if raw.get("url"):
                    raw["url"] = urljoin(base, raw["url"])
                if recipe.documents and not recipe.detail and not recipe.documents_url:
                    raw["_docs"] = [urljoin(base, u) for u in _values(row, recipe.documents, listing.kind)]
                raws.append(raw)
            if listing.page_param and body is not None:
                body = _with_body_param(listing.body, listing.page_param, page_number + 1)
            elif listing.page_param:
                url = _with_param(start, listing.page_param, page_number + 1)
            elif listing.next_page and (links := _values(page, listing.next_page, listing.kind)):
                url = urljoin(base, links[0])
            else:
                finished = True
                break
            if url + json.dumps(body, sort_keys=True) in seen:
                finished = True
                break
        complete = complete and finished
    attempted, blocked = 0, []
    details = raws
    if recipe.detail and max_details is not None and "submissionDeadline" in listing.fields:
        now, tz = datetime.now(UTC), ZoneInfo(recipe.timezone)
        first_rows = {}
        for raw in raws:
            if tender_id := raw.get("id"):
                first_rows.setdefault(tender_id, raw)

        def has_upcoming_deadline(raw):
            deadline = _datetime(raw.get("submissionDeadline"), recipe.dayfirst, tz)
            return deadline is not None and deadline >= now

        candidates = (raw for raw in first_rows.values() if raw.get("url") or recipe.detail.url)
        details = sorted(candidates, key=has_upcoming_deadline, reverse=True)
    for raw in details[:max_details]:
        detail_url = recipe.detail and (_fill(recipe.detail.url, raw) if recipe.detail.url else raw.get("url"))
        if detail_url:
            attempted += 1
            try:
                page, base = await _load(recipe.detail.kind, detail_url, fetcher)
            except Blocked as exc:
                blocked.append(str(exc))
                continue
            except (httpx.HTTPError, ValueError, OSError, PlaywrightError):
                continue
            raw.update(_extract(page, recipe.detail.fields, recipe.detail.kind))
            if (recipe.documents or recipe.documents_by_host) and not recipe.documents_url:
                try:
                    raw["_docs"] = await _document_links(recipe, page, base, fetcher)
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
    sampled_statuses = set()
    for raw in raws:
        if recipe.documents_url and not recipe.documents and (url := _fill(recipe.documents_url.template, raw)):
            raw["_docs"] = [url]
        if (tender := normalize(raw, recipe)) and tender["id"] not in tenders:
            tenders[tender["id"]] = tender
            if warnings is not None:
                sampled_statuses.add((raw.get("status") or "").casefold())
    if (warnings is not None and len(tenders) >= 3 and len(sampled_statuses) == 1
            and "" not in sampled_statuses):
        warnings.append("All sampled tenders map to one status value. Check whether the field describes "
                        "publication rather than procurement, and consider the notice type or stage.")
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
    if source and source.modal == "netserver_file_tree":
        html, _, base = await render(base, fetcher, netserver_files=True)
        page, kind = BeautifulSoup(html, "html.parser"), "html"
    selector = source.files if source else recipe.documents
    return [urljoin(base, u) for u in _link_values(page, selector, kind)] if selector else []


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
