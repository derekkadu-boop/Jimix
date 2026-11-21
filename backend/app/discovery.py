import json
import logging
import re
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .fetch import Blocked, Fetcher, site

log = logging.getLogger("watchtower.discovery")
DOC_PATHS = ("/sitemap.xml", "/openapi.json", "/swagger.json", "/v3/api-docs", "/api-docs", "/api", "/swagger-ui/index.html")
LITERAL = re.compile(r"""["'`](https?://[^"'`\s]{3,200}|/?[A-Za-z0-9_.-]+/[^"'`\s]{1,200})["'`]""")
API_HINT = re.compile(
    r"api|rest|odata|graphql|search|suche|tender|notice|lots?\b|vergabe|bekannt|ausschreib|annonce|"
    r"licitac|aanbested|przetarg|/v\d",
    re.IGNORECASE,
)
ASSET = re.compile(r"\.(css|png|svg|jpe?g|gif|woff2?|ico|map|html?)(\?|$)", re.IGNORECASE)
CHUNK = re.compile(r"""["'`]((?:\./|/)?[\w./-]*?[\w-]+\.js)["'`]""")
MAX_SCRIPTS = 25
MAX_PATHS = 60


def _collect(text: str, source: str, found: dict) -> None:
    for match in LITERAL.finditer(text):
        path = match[1]
        if len(found) >= MAX_PATHS or path in found or ASSET.search(path) or path.endswith(".js"):
            continue
        if API_HINT.search(path):
            context = text[max(0, match.start() - 60) : match.end() + 60]
            found[path] = {"in": source.rsplit("/", 1)[-1], "context": " ".join(context.split())}


async def scan_scripts(url: str, fetcher: Fetcher) -> list[dict]:
    resp = await fetcher.get(url)
    base = str(resp.url)
    soup = BeautifulSoup(resp.text, "html.parser")
    found: dict = {}
    _collect(" ".join(s.string or "" for s in soup("script") if not s.get("src")), "inline", found)
    queue = [urljoin(base, s["src"]) for s in soup("script", src=True)]
    seen: set[str] = set()
    while queue and len(seen) < MAX_SCRIPTS:
        src = queue.pop(0)
        if src in seen or site(src) != site(base):
            continue
        seen.add(src)
        try:
            text = await fetcher.script_text(src, base)
        except (Blocked, httpx.HTTPError, ValueError, OSError):
            continue
        _collect(text, src, found)
        for chunk in CHUNK.findall(text):
            queue.append(urljoin(src if chunk.startswith("./") else base, chunk))
    return [{"path": path} | info for path, info in found.items()]


async def probe_docs(url: str, fetcher: Fetcher) -> list[dict]:
    parts = urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}"
    found = []
    for path in DOC_PATHS:
        try:
            resp = await fetcher.get(root + path)
        except (Blocked, httpx.HTTPError, ValueError, OSError):
            continue
        kind = resp.headers.get("content-type", "")
        if "html" in kind and not re.search(r"swagger|openapi|redoc", resp.text[:5000], re.IGNORECASE):
            continue
        found.append({"url": str(resp.url), "contentType": kind, "start": resp.text[:300]})
    return found


async def discover(url: str, fetcher: Fetcher) -> str:
    report = {}
    for name, step in (("apiDocumentation", probe_docs), ("pathsInScripts", scan_scripts)):
        try:
            report[name] = await step(url, fetcher)
        except Exception as exc:  # noqa: BLE001
            report[name] = f"failed: {type(exc).__name__}: {exc}"[:300]
    log.info("discovery for %s: %d doc hits, %s script paths", url, len(report["apiDocumentation"]),
             len(report["pathsInScripts"]) if isinstance(report["pathsInScripts"], list) else "no")
    return json.dumps(report, ensure_ascii=False)[:8000]
