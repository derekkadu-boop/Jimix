import asyncio
import io
import zipfile

import httpx
import pytest

from app import agent, db, documents, fetch, watchtower
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


def test_recipe_reads_documents_from_multiple_buyer_platforms(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jimix.db")
    db.init()
    with db.connect() as conn:
        conn.execute("INSERT INTO portal (id, url, created_at) VALUES ('mixed', 'https://portal.example/list', ?)",
                     (db.now(),))
        conn.execute(
            "INSERT INTO tender (portal_id, id, data, last_changed_at) VALUES ('mixed', '4', ?, ?)",
            ('{"status":"closed","documents":[{"fileName":"previous.pdf"}]}', db.now()),
        )
    pages = {
        "https://portal.example/list": LIST_1.replace('<a class="next" href="/list?page=2">next</a>', "")
        .replace("</table>", '<tr class="t"><td><a href="/t/3">IT supplies</a></td></tr>'
                 '<tr class="t"><td><a href="/t/4">Blocked buyer</a></td></tr></table>'),
        "https://portal.example/t/1": '<a class="platform" href="https://buyer-one.example/notice">Notice</a>',
        "https://portal.example/t/3": '<a class="platform" href="/documents/3">Documents</a>',
        "https://portal.example/t/4": '<a class="platform" href="https://buyer-four.example/notice">Notice</a>',
        "https://buyer-one.example/notice": '<a class="tab" href="/published/documents">Public documents</a>',
        "https://buyer-one.example/published/documents": '<a class="file" href="spec.pdf">Specification</a>',
        "https://buyer-two.example/notice": '<a class="attachment" href="/downloads/spec.pdf">Attachments</a>',
        "https://buyer-four.example/notice": '<a class="tab" href="/downloads">Documents</a>',
        "https://portal.example/documents/3": '<a class="doc" href="/files/local.pdf">Local documents</a>',
    }

    def response(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if str(request.url) == "https://portal.example/t/2":
            return httpx.Response(302, headers={"location": "https://buyer-two.example/notice"})
        if str(request.url) in pages:
            return httpx.Response(200, html=pages[str(request.url)])
        if str(request.url) == "https://buyer-four.example/downloads":
            return httpx.Response(403)
        if request.url.path.endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF-1.4 fixture")
        return httpx.Response(404)

    recipe = Recipe.model_validate(RECIPE | {
        "documents_page": {"path": "a.platform"},
        "documents_by_host": {
            "buyer-one.example": {"page": {"path": "a.tab"}, "files": {"path": "a.file"}},
            "buyer-two.example": {"files": {"path": "a.attachment"}},
            "buyer-four.example": {"page": {"path": "a.tab"}, "files": {"path": "a.file"}},
        },
    })

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            tenders, complete = await apply(recipe, fetcher)
            assert complete and tenders[-1]["_documentBlock"] == "access denied (HTTP 403)"
            return [url for tender in tenders for url in tender["_documentUrls"]]
        finally:
            await fetcher.close()

    assert asyncio.run(run()) == [
        "https://buyer-one.example/published/spec.pdf",
        "https://buyer-two.example/downloads/spec.pdf",
        "https://portal.example/files/local.pdf",
    ]

    async def deliver():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            for _ in range(2):
                assert await watchtower._deliver("mixed", recipe, fetcher) == (
                    4, None, "Document pages blocked for 1 of 4 tenders",
                )
        finally:
            await fetcher.close()

    asyncio.run(deliver())
    with db.connect() as conn:
        rows = conn.execute("SELECT json_extract(data, '$.documents[0].fileName') FROM tender ORDER BY id")
        assert [row[0] for row in rows] == ["spec.pdf", "spec.pdf", "local.pdf", "previous.pdf"]


def test_recipe_validation_samples_each_document_host(monkeypatch):
    tried = []

    async def download(urls, fetcher):
        tried.extend(urls)
        return ([{"fileName": "spec.pdf"}], True, None) if "buyer-one" in urls[0] else ([], False, None)

    monkeypatch.setattr(agent, "fetch_documents", download)
    recipe = Recipe.model_validate(RECIPE | {
        "documents_by_host": {"buyer-one.example": {"files": {"path": "a"}}},
    })
    tenders = [
        {"id": "1", "_documentUrls": [f"https://buyer-one.example/{i}.pdf" for i in range(3)]},
        {"id": "2", "_documentUrls": ["https://buyer-two.example/file.pdf"]},
        {"id": "3", "_documentUrls": [], "_documentChecked": True},
    ]
    problem, warnings = asyncio.run(agent._check_documents(recipe, tenders, None))
    assert not problem
    assert tried == ["https://buyer-one.example/0.pdf", "https://buyer-two.example/file.pdf"]
    assert warnings == ["tender 3: no file links", "could not download sampled file from buyer-two.example"]


def test_captcha_is_reported_as_blocked():
    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(portal))
        try:
            await fetcher.get("https://portal.example/locked")
        finally:
            await fetcher.close()

    with pytest.raises(Blocked, match="captcha"):
        asyncio.run(run())


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
