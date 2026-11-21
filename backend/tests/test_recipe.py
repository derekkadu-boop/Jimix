import asyncio
import io
import re
import zipfile

import httpx
import pytest

from app import documents, fetch
from app.documents import fetch_documents
from app.fetch import Blocked, Fetcher
from app.recipe import Recipe, apply

LIST_1 = """<html><body><table>
<tr class="t"><td class="ref">A-1</td><td><a href="/t/1">Road works</a></td>
<td class="buyer">City of Testville</td><td class="due">20.12.2099 10:00</td></tr>
<tr class="t"><td class="ref">A-2</td><td><a href="/t/2">Cleaning services</a></td>
<td class="buyer">County</td><td class="due">01.01.2000 12:00</td></tr>
</table><a class="next" href="/list?page=2">next</a></body></html>"""
LIST_2 = """<html><body><table>
<tr class="t"><td class="ref">A-3</td><td><a href="/t/3">IT supplies</a></td>
<td class="buyer">Ministry</td><td class="due">soon</td></tr></table></body></html>"""
DETAIL = """<html><body><p class="cpv">45233140-2, 45000000-7</p><p class="kind">Bauleistung</p>
<a class="doc" href="/files/{n}.zip">docs</a></body></html>"""
CAPTCHA = '<html><body><div class="g-recaptcha"></div>Please verify you are human</body></html>'

RECIPE = {
    "method": "html",
    "listing": {
        "url": "https://portal.example/list",
        "records": "tr.t",
        "fields": {
            "id": {"path": "a", "attribute": "href", "pattern": r"/t/(\d+)"},
            "referenceNumber": {"path": ".ref"},
            "title": {"path": "a"},
            "url": {"path": "a", "attribute": "href"},
            "buyerName": {"path": ".buyer"},
            "submissionDeadline": {"path": ".due"},
        },
        "next_page": {"path": "a.next", "attribute": "href"},
    },
    "detail": {"fields": {"cpvCodes": {"path": ".cpv"}, "contractNature": {"path": ".kind"}}},
    "documents": {"path": "a.doc", "attribute": "href"},
    "timezone": "Europe/Berlin",
}


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("notice.pdf", b"%PDF-1.4 notice")
        zf.writestr("terms.txt", b"terms")
    return buffer.getvalue()


def portal(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(404)
    if path == "/list":
        return httpx.Response(200, html=LIST_2 if request.url.params.get("page") == "2" else LIST_1)
    if path.startswith("/t/"):
        return httpx.Response(200, html=DETAIL.format(n=path[3:]))
    if path.endswith(".zip"):
        return httpx.Response(200, content=_zip(), headers={"content-type": "application/zip"})
    if path == "/locked":
        return httpx.Response(200, html=CAPTCHA)
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def fast(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "INTERVAL", 0)
    monkeypatch.setattr(documents, "FILES_DIR", tmp_path)


def test_recipe_reads_listing_details_and_documents():
    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(portal))
        tenders, complete = await apply(Recipe.model_validate(RECIPE), fetcher)
        docs, docs_complete, _ = await fetch_documents(tenders[0]["_documentUrls"], fetcher)
        await fetcher.close()
        assert complete and docs_complete
        return tenders, docs

    tenders, docs = asyncio.run(run())
    assert [t["id"] for t in tenders] == ["1", "2", "3"]
    assert tenders[0]["referenceNumber"] == "A-1"
    first = tenders[0]
    assert first["url"] == "https://portal.example/t/1"
    assert first["buyerName"] == "City of Testville"
    assert first["submissionDeadline"] == "2099-12-20T10:00:00+01:00"
    assert first["status"] == "open"
    assert first["cpvCodes"] == ["45233140", "45000000"]
    assert first["contractNature"] == "works"
    assert tenders[1]["status"] == "closed"
    assert tenders[2]["submissionDeadline"] is None
    assert sorted(d["fileName"] for d in docs) == ["notice.pdf", "terms.txt"]
    assert all(d["path"] == f"/files/{d['sha256']}" for d in docs)


def test_captcha_is_reported_as_blocked():
    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(portal))
        try:
            await fetcher.get("https://portal.example/locked")
        finally:
            await fetcher.close()

    with pytest.raises(Blocked, match="captcha"):
        asyncio.run(run())


def api(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(404)
    if path == "/api/pub" and request.url.params.get("page") == "0":
        rows = [{"publicatieId": n, "naam": f"Tender {n}", "link": {"href": f"/view/{n}"}} for n in (7, 8)]
        return httpx.Response(200, json={"content": rows})
    if path == "/api/pub":
        return httpx.Response(200, json={"content": []})
    if match := re.fullmatch(r"/api/pub/(\d+)", path):
        return httpx.Response(200, json={"sluitingsDatum": "2099-02-15T09:00:00", "cpv": [{"code": "45232420"}]})
    if match := re.fullmatch(r"/api/pub/(\d+)/docs", path):
        n = match[1]
        docs = [{"links": {"download": {"href": f"/api/pub/{n}/docs/{i}/content"}}} for i in (1, 2)]
        return httpx.Response(200, json={"documenten": docs})
    if re.fullmatch(r"/api/pub/\d+/(docs/\d+/content|pdf)", path):
        return httpx.Response(200, content=b"%PDF-1.4 " + path.encode(), headers={"content-type": "application/pdf"})
    return httpx.Response(404)


API_RECIPE = {
    "method": "documented_api",
    "listing": {
        "kind": "json",
        "url": "https://api.example/api/pub?page=0&size=50",
        "records": "content",
        "fields": {
            "id": {"path": "publicatieId"},
            "title": {"path": "naam"},
            "url": {"path": "link.href"},
        },
        "page_param": "page",
    },
    "detail": {
        "kind": "json",
        "url": "https://api.example/api/pub/{id}",
        "fields": {"submissionDeadline": {"path": "sluitingsDatum"}, "cpvCodes": {"path": "cpv", "attribute": "code"}},
    },
    "documents": {"path": "documenten", "attribute": "links.download.href"},
    "documents_url": {"template": "https://api.example/api/pub/{id}/docs", "kind": "json"},
}


def _apply(recipe: dict, handler, **limits):
    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(handler))
        try:
            tenders, complete = await apply(Recipe.model_validate(recipe), fetcher, **limits)
            docs, _, _ = await fetch_documents(tenders[0]["_documentUrls"], fetcher)
            return tenders, complete, docs
        finally:
            await fetcher.close()

    return asyncio.run(run())


def test_url_templates_read_detail_and_document_list():
    tenders, complete, docs = _apply(API_RECIPE, api)
    assert complete and [t["id"] for t in tenders] == ["7", "8"]
    first = tenders[0]
    assert first["url"] == "https://api.example/view/7"
    assert first["submissionDeadline"].startswith("2099-02-15T09:00")
    assert first["cpvCodes"] == ["45232420"]
    assert first["_documentUrls"] == [f"https://api.example/api/pub/7/docs/{i}/content" for i in (1, 2)]
    assert len(docs) == 2


def test_documents_url_without_documents_is_the_file():
    recipe = API_RECIPE | {"detail": None, "documents": None,
                           "documents_url": {"template": "/api/pub/{id}/pdf"}}
    tenders, _, docs = _apply(recipe, api, max_details=0)
    assert [t["_documentUrls"] for t in tenders] == [[f"https://api.example/api/pub/{n}/pdf"] for n in (7, 8)]
    assert len(docs) == 1


def test_templates_must_use_mapped_fields():
    bad = API_RECIPE | {"documents_url": {"template": "https://api.example/x/{secret}", "kind": "json"}}
    with pytest.raises(ValueError, match="not mapped"):
        Recipe.model_validate(bad)
    with pytest.raises(ValueError, match="absolute"):
        Recipe.model_validate(API_RECIPE | {"documents_url": {"template": "javascript:{id}"}})


def test_index_reads_every_linked_list():
    def categories(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cats":
            return httpx.Response(200, html='<a class="cat" href="/list">A</a><a class="cat" href="/list?page=2">B</a>')
        return portal(request)

    recipe = RECIPE | {
        "listing": RECIPE["listing"] | {"url": "https://portal.example/cats", "next_page": None,
                                        "index": {"path": "a.cat", "attribute": "href"}},
        "detail": None,
        "documents": None,
    }

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(categories))
        try:
            return await apply(Recipe.model_validate(recipe), fetcher)
        finally:
            await fetcher.close()

    tenders, complete = asyncio.run(run())
    assert sorted(t["id"] for t in tenders) == ["1", "2", "3"]
    assert complete
