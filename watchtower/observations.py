import json
import math
import re
from itertools import islice

from bs4 import BeautifulSoup, Tag

from .context import ExplorationContext, StopExploration, ToolError
from .models import InitialObservation, StopReason
from .phrases import PHRASES, contains_phrase
from .robots import check_robots
from .urls import SECRET_KEY, candidate_url, clean_text

PAGE_ROLES = {"initial", "page", "pagination", "detail"}
DOC_PATTERNS = tuple(
    (re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)"), weight)
    for signal, weight in (("documentation", 2), ("api_specification", 5))
    for phrase in PHRASES[signal]
)
DOCUMENT_EXT = re.compile(r"\.(pdf|docx?|xlsx?|csv|zip|odt|p7s|rtf)(?:$|\?)", re.I)
DETAIL_PATH = re.compile(rf"(?:{'|'.join(map(re.escape, PHRASES['tender_detail_path']))})[/-][^/?#]+", re.I)
SCRIPT_STATE = ("__next_data__", "__initial_state__", "__nuxt__", "__apollo_state__", "__initial_data__")
BRIEF_KEYS = {
    "documentation",
    "pagination",
    "forms",
    "embedded_json",
    "navigation",
    "tender_content",
    "documents",
    "js_rendering_signals",
    "links",
    "scripts",
    "text",
    "sitemaps",
    "header_links",
    "footer_links",
}


PUBLIC_SAMPLE_KEYS = {
    "id",
    "title",
    "name",
    "status",
    "tenderid",
    "noticeid",
    "referencenumber",
    "noticetitle",
    "cpv",
}


def _bad_constant(_):
    raise ValueError("Non-finite JSON value")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        return _bad_constant(value)
    return number


def bounded_json(data: bytes | str, max_depth: int):
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8-sig")
        except UnicodeError:
            raise ToolError("Invalid JSON encoding") from None
    depth, quoted, escaped = 0, False, False
    for char in data:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{[":
            depth += 1
            if depth > max_depth:
                raise ToolError("JSON exceeds processing depth")
        elif char in "]}":
            depth -= 1
    try:
        return json.loads(data, parse_constant=_bad_constant, parse_float=_finite_float)
    except (ValueError, RecursionError):
        raise ToolError("Malformed JSON") from None


def json_shape(value, depth: int = 0):
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {
            clean_text(k)[:60]: json_shape(entry, depth + 1)
            for k, entry in list(value.items())[:16]
            if isinstance(k, str) and not SECRET_KEY.search(k)
        }
    if isinstance(value, list):
        return [json_shape(value[0], depth + 1)] if value else []
    return type(value).__name__


def public_json_sample(value, depth: int = 0):
    if depth >= 4:
        return None
    if isinstance(value, list):
        return [sample for item in value[:2] if (sample := public_json_sample(item, depth + 1))]
    if not isinstance(value, dict):
        return None
    out = {}
    for key, item in list(value.items())[:14]:
        if SECRET_KEY.search(key):
            continue
        name = clean_text(key)[:60]
        if key.lower() in PUBLIC_SAMPLE_KEYS and isinstance(item, (str, int)) and not isinstance(item, bool):
            text = clean_text(str(item))[:100]
            if text and "[redacted" not in text:
                out[name] = text
        elif sample := public_json_sample(item, depth + 1):
            out[name] = sample
    return out


def compact(value: dict, limit: int) -> dict:
    """Trim lists and text while keeping JSON valid and evidence IDs usable."""
    result = value.copy()

    def size():
        return len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    while size() > limit:
        items = [
            (len(json.dumps(result[key], ensure_ascii=False)), key)
            for key in result
            if (
                key in BRIEF_KEYS
                or key
                in {
                    "sample",
                    "records",
                    "evidence",
                    "endpoints",
                    "fields",
                    "responses",
                    "links",
                    "validation",
                }
            )
            and isinstance(result[key], (list, dict, str))
            and result[key]
        ]
        if not items:
            break
        _, key = max(items)
        item = result[key]
        if isinstance(item, list) and len(item) > 1:
            result[key] = item[: max(1, len(item) // 2)]
        elif isinstance(item, str) and len(item) > 100:
            result[key] = item[: len(item) // 2]
        else:
            result.pop(key)
    if size() > limit:
        safe = {
            key: value
            for key, value in result.items()
            if key in {"evidence_id", "verified", "accepted", "error", "status", "restriction_type"}
        }
        return safe | {"note": "Observation too large for this run's model input limit"}
    return result


def doc_score(text: str) -> int:
    text = re.sub(r"[_/-]+", " ", text.casefold())
    return sum(weight for pattern, weight in DOC_PATTERNS if pattern.search(text))


def useful_selector(element: Tag) -> str:
    tag = element.name
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]*", tag):
        return ""
    name = element.get("id", "")
    if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,50}", name):
        return f"#{name}"
    classes = [c for c in element.get("class", []) if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,40}", c)]
    return tag + (f".{classes[0]}" if classes else "")


def describe_page(
    body: bytes | str | BeautifulSoup,
    url: str,
    ctx: ExplorationContext,
    *,
    documentation_depth: int | None = None,
) -> dict:
    soup = body if isinstance(body, BeautifulSoup) else BeautifulSoup(body, "html.parser")
    cfg = ctx.config
    if documentation_depth is None:
        documentation_depth = ctx.documentation_depth.get(url, 0)
    sections: dict[str, list[dict]] = {
        key: []
        for key in (
            "header_links",
            "footer_links",
            "navigation",
            "documentation",
            "sitemaps",
            "pagination",
            "documents",
            "links",
            "scripts",
            "embedded_json",
            "forms",
            "tender_content",
            "js_rendering_signals",
        )
    }
    seen: set[str] = set()
    raw_links = soup.find_all(["a", "link"], href=True, limit=cfg.max_candidate_urls * 6)
    ranked = []
    for tag in raw_links:
        target = candidate_url(url, tag["href"])
        if not target:
            continue
        label = clean_text(tag.get_text(" ", strip=True))[:100]
        ranked.append((doc_score(label + " " + target.partition("?")[0]), tag, target, label))
    ranked.sort(key=lambda item: -item[0])
    for score, tag, target, label in ranked:
        if target in seen:
            continue
        seen.add(target)
        rel = tag.get("rel", [])
        hint = {"url": target, "label": label}
        roles = {"page"}
        is_document = bool(DOCUMENT_EXT.search(target) or tag.has_attr("download"))
        is_sitemap = "sitemap" in rel or "sitemap" in tag.get("href", "").lower()
        is_tender = bool(DETAIL_PATH.search(target.split("/", 3)[-1])) or contains_phrase(
            label, "tender_label"
        )
        if score and not is_document:
            roles.add("documentation")
            hint["score"] = score
            if re.search(r"/(?:api|rest|odata)(?:/|$)", target.split("?", 1)[0], re.I):
                roles.add("api")
        if is_document:
            roles = {"document"}
        if is_sitemap:
            roles.add("sitemap")
        if is_tender and not is_document:
            roles.add("detail")
        if "next" in rel or label.casefold() in PHRASES["next_page"]:
            roles.add("pagination")
        for role in sorted(roles):
            ctx.add_candidate(target, role)
        if target not in ctx.candidates:
            continue
        if roles.intersection({"documentation", "sitemap"}):
            depth = documentation_depth + 1
            ctx.documentation_depth[target] = min(depth, ctx.documentation_depth.get(target, depth))
        if "documentation" in roles:
            sections["documentation"].append(hint)
        for role, section in (
            ("document", "documents"),
            ("detail", "tender_content"),
            ("pagination", "pagination"),
            ("sitemap", "sitemaps"),
        ):
            if role in roles:
                sections[section].append(hint)
        for parent, section in (
            ("header", "header_links"),
            ("nav", "navigation"),
            ("footer", "footer_links"),
        ):
            if tag.find_parent(parent):
                sections[section].append(hint)
        if tag.name == "a" and not is_document:
            sections["links"].append(hint)
    for script in soup.find_all("script", limit=50):
        kind = str(script.get("type", "")).lower()
        script_id = str(script.get("id", "")).lower()
        source = candidate_url(url, script.get("src", ""))
        if source and ctx.add_candidate(source, "script"):
            sections["scripts"].append({"url": source})
        if "json" in kind or script_id in SCRIPT_STATE:
            sections["embedded_json"].append(
                {"type": clean_text(kind)[:40], "id": clean_text(script_id)[:50]}
            )
    for form in soup.find_all("form", limit=8):
        action = candidate_url(url, form.get("action", "")) or url
        names = [
            clean_text(str(item.get("name", "")))[:40]
            for item in form.find_all(["input", "select", "button"], limit=15)
            if item.get("type") not in {"hidden", "password"}
        ]
        sections["forms"].append(
            {
                "method": str(form.get("method", "get")).upper()[:10],
                "action": action,
                "field_names": [name for name in names if name],
            }
        )
    selectors = (
        "table",
        "table tbody tr",
        "article",
        "[class*=tender i]",
        "[class*=notice i]",
        "[class*=contract i]",
        "a[rel=next]",
    )
    for selector in selectors:
        tag = soup.select_one(selector)
        if tag:
            text = clean_text(tag.get_text(" ", strip=True))[:150]
            if text:
                sections["tender_content"].append({"selector": useful_selector(tag), "text": text})
    for marker in ("#__next", "#app", "#root", "app-root", "script[type=module]"):
        if soup.select_one(marker):
            sections["js_rendering_signals"].append({"marker": marker})
    if sections["pagination"]:
        ctx.capabilities.pagination_available = True
    for hidden in soup.find_all(["script", "style", "template", "noscript"]):
        hidden.decompose()
    title = clean_text(soup.title.get_text(" ", strip=True))[:160] if soup.title else ""
    html = soup.html
    language = clean_text(str(html.get("lang", "")))[:24] if html else ""
    text = ""
    if cfg.max_html_chars:
        remaining = cfg.max_html_chars
        snippets = []
        for chunk in islice(soup.stripped_strings, 300):
            if remaining <= 0:
                break
            value = clean_text(chunk)[:remaining]
            snippets.append(value)
            remaining -= len(value) + 1
        text = " ".join(snippets)
    sections = {name: entries[:8] for name, entries in sections.items() if entries}
    return compact(
        {"title": title, "language": language, **sections, "text": text}, cfg.max_observation_chars
    )


async def initial_fetch(url: str, context: ExplorationContext) -> InitialObservation:
    ctx = context
    ctx.add_candidate(url, "initial")
    ctx.add_candidate(url, "page")
    async with ctx.step("initial_fetch", {"url": url}) as trace:
        robots = await check_robots(url, ctx.config.user_agent, ctx)
        ctx.log("robots.txt checked (%s)", robots.status)
        if not robots.allowed:
            reason = (
                StopReason.ROBOTS_DISALLOWED
                if robots.status == "disallowed"
                else StopReason.ROBOTS_UNAVAILABLE
            )
            ctx.evidence.append(
                {"evidence_id": trace["evidence_id"], "robots": robots.model_dump(mode="json")}
            )
            raise StopExploration(reason)
        response = await ctx.transport.fetch(url, blocking=True)
        restriction = response.restriction
        ctx.log("Homepage fetched (%s)", response.status)
        if restriction.restriction_detected:
            ctx.log("Initial access finding (%s)", restriction.restriction_type)
        else:
            ctx.log("No access challenge detected")
        page = {}
        if response.is_html and not restriction.restriction_detected:
            ctx.add_candidate(response.final_url, "page")
            page = describe_page(response.body, response.final_url, ctx)
        if robots.sitemaps:
            for sitemap in robots.sitemaps:
                if ctx.add_candidate(sitemap, "sitemap"):
                    ctx.documentation_depth.setdefault(sitemap, 1)
            page.setdefault("sitemaps", []).extend({"url": sitemap} for sitemap in robots.sitemaps[:8])
        observation = InitialObservation(
            original_url=url,
            final_url=response.final_url,
            status=response.status,
            content_type=response.content_type,
            access_restriction=restriction,
            robots=robots,
            page=page,
        )
        ctx.initial = observation
        ctx.evidence.append(
            {
                "evidence_id": trace["evidence_id"],
                "tool": "initial_fetch",
                "observation": observation.model_dump(mode="json"),
            }
        )
        trace.update(observation.model_dump(mode="json"))
        if restriction.is_blocking:
            trace["error"] = restriction.restriction_type
            ctx.stop = StopReason(restriction.restriction_type)
        ctx.log("Found %d documentation candidates", len(ctx.documentation_depth))
        return observation
