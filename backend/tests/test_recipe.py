import asyncio
import io
import json
import re
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup
from playwright.async_api import Error as PlaywrightError

from app import agent, db, documents, fetch, watchtower
from app import recipe as recipes
from app.documents import fetch_documents
from app.fetch import Blocked, Fetcher
from app.recipe import Recipe, Selector, apply

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


def _office_package(main_part: str, *, with_relationships: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")
        zf.writestr(main_part, b"Office document")
        if with_relationships:
            zf.writestr("_rels/.rels", b"<Relationships/>")
    return buffer.getvalue()


@pytest.mark.parametrize("main_part", [
    "word/document.xml", "xl/workbook.xml", "xl/workbook.bin", "ppt/presentation.xml",
])
def test_office_package_is_saved_intact_even_with_a_generic_filename(tmp_path, main_part):
    package = _office_package(main_part)
    part = tmp_path / ".part-office"
    part.write_bytes(package)

    saved, unpacked = documents._save(part, "download", 0)

    assert unpacked == 0
    assert [doc["fileName"] for doc in saved] == ["download"]
    assert (tmp_path / saved[0]["sha256"]).read_bytes() == package
    assert not part.exists()


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
    monkeypatch.setattr(fetch, "RETRY_DELAYS", (0, 0))
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


def test_failed_modal_preserves_previous_files_for_one_tender(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jimix.db")
    db.init()
    previous_url = "https://buyer.example/files/old.pdf"
    previous = {
        "status": "open",
        "documents": [{"fileName": "old.pdf"}],
        "_documentUrls": [previous_url],
        "_documentSkips": {previous_url: "over size limit"},
        "_documentsComplete": True,
    }
    with db.connect() as conn:
        conn.execute("INSERT INTO portal (id, url, created_at) VALUES ('modal', 'https://portal.example/list', ?)",
                     (db.now(),))
        conn.execute(
            "INSERT INTO tender (portal_id, id, data, last_changed_at) VALUES ('modal', '1', ?, ?)",
            (json.dumps(previous), db.now()),
        )

    async def failed_render(url, fetcher, *, netserver_files):
        assert url == "https://buyer.example/notice" and netserver_files
        raise PlaywrightError("file list timed out")

    monkeypatch.setattr(recipes, "render", failed_render)
    recipe = Recipe.model_validate(RECIPE | {
        "listing": RECIPE["listing"] | {"next_page": None},
        "documents_page": {"path": "a.platform"},
        "documents_by_host": {
            "buyer.example": {"modal": "netserver_file_tree", "files": {"path": "a.filenameEllipsis[href]"}},
        },
    })

    def response(request):
        if request.url.path == "/t/1":
            return httpx.Response(200, html='<a class="platform" href="https://buyer.example/notice">Notice</a>')
        if request.url.path == "/t/2":
            return httpx.Response(200, html='<a class="platform" href="/t/file-list">Documents</a>')
        if request.url.path == "/notice":
            return httpx.Response(200, html="<p>Published invitation</p>")
        return portal(request)

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            return await watchtower._deliver("modal", recipe, fetcher)
        finally:
            await fetcher.close()

    assert asyncio.run(run()) == (2, None, "Could not read document pages for 1 of 2 tenders")
    with db.connect() as conn:
        rows = conn.execute("SELECT data FROM tender ORDER BY id").fetchall()
    failed, successful = (json.loads(row["data"]) for row in rows)
    for key in ("documents", "_documentUrls", "_documentSkips"):
        assert failed[key] == previous[key]
    assert not failed["_documentsComplete"]
    assert successful["_documentsComplete"]
    assert [doc["fileName"] for doc in successful["documents"]] == ["notice.pdf", "terms.txt"]


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


def test_recipe_validation_requires_document_links():
    recipe = Recipe.model_validate(RECIPE)
    tenders = [{"id": "1", "_documentUrls": []}, {"id": "2", "_documentUrls": []}]
    problem, _ = asyncio.run(agent._check_documents(recipe, tenders, None))
    assert problem and problem.startswith("no document links in the sampled tenders")
    by_host = Recipe.model_validate(RECIPE | {"documents_by_host": {"buyer.example": {"files": {"path": "a"}}}})
    checked = [{"id": "1", "_documentUrls": [], "_documentChecked": True}]
    problem, warnings = asyncio.run(agent._check_documents(by_host, checked, None))
    assert problem and problem.startswith("no document links in the sampled tenders")
    assert warnings == ["tender 1: no file links"]


@pytest.mark.parametrize("template_status", [200, 404, 410, 503])
def test_validation_uses_listing_detail_when_html_template_is_gone(template_status):
    recipe = Recipe.model_validate(RECIPE | {"detail": RECIPE["detail"] | {"url": "/template/{id}"}})
    paths = []

    def response(request):
        path = request.url.path
        paths.append(path)
        if path.startswith("/template/"):
            if template_status == 200:
                return httpx.Response(200, html=DETAIL.format(n=path.rsplit("/", 1)[1]))
            return httpx.Response(template_status)
        return portal(request)

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            return await agent._test(recipe, fetcher)
        finally:
            await fetcher.close()

    report = asyncio.run(run())
    if template_status == 503:
        assert report["problem"].startswith("no sampled tender detail pages were read")
        assert "/t/2" not in paths
    else:
        assert report["problem"] is None
        assert report["withDocumentLinks"] == 3
        assert "/files/1.zip" in paths
        assert ("/t/2" in paths) == (template_status in (404, 410))


def test_refresh_retries_html_but_keeps_oversize_skip(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jimix.db")
    monkeypatch.setattr(fetch, "MAX_FILE_BYTES", 1024)
    monkeypatch.setattr(watchtower, "MAX_DETAILS", None)
    db.init()
    urls = ["https://portal.example/files/" + name for name in ("archive.zip", "landing.pdf", "large.pdf")]
    old_files = [
        {"sha256": str(i), "fileName": f"cached-{i}.pdf"}
        for i in range(3)
    ]
    legacy = {"status": "open", "documents": old_files, "_documentUrls": urls, "_documentsComplete": True}
    with db.connect() as conn:
        conn.execute("INSERT INTO portal (id, url, created_at) VALUES ('files', 'https://portal.example/list', ?)",
                     (db.now(),))
        conn.execute(
            "INSERT INTO tender (portal_id, id, data, last_changed_at) VALUES ('files', '1', ?, ?)",
            (json.dumps(legacy), db.now()),
        )
    downloads = []

    def response(request):
        path = request.url.path
        if path == "/t/1":
            return httpx.Response(200, html="".join(f'<a class="doc" href="{url}">File</a>' for url in urls))
        if not path.startswith("/files/"):
            return portal(request)
        downloads.append(path)
        if path == "/files/large.pdf":
            return httpx.Response(200, content=b"x" * 1025)
        if path == "/files/archive.zip":
            if downloads.count(path) == 1:
                return httpx.Response(503)
            return httpx.Response(200, content=_zip())
        if downloads.count(path) == 1:
            return httpx.Response(200, html="<p>Open the landing page to download</p>")
        return httpx.Response(200, content=b"%PDF-1.4 fixture",
                              headers={"content-disposition": 'attachment; filename="spec.pdf"'})

    recipe = Recipe.model_validate(RECIPE | {
        "listing": RECIPE["listing"] | {"records": "tr.t:first-child", "next_page": None},
    })

    async def refresh():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            run_id = watchtower._new_run("files", "read")
            count, blocked, note = await watchtower._deliver("files", recipe, fetcher)
            watchtower._finish_delivery("files", run_id, watchtower.Usage(), count, blocked, note)
        finally:
            await fetcher.close()
        with db.connect() as conn:
            record = json.loads(conn.execute("SELECT data FROM tender WHERE id = '1'").fetchone()["data"])
            reason = conn.execute("SELECT reason FROM run WHERE id = ?", (run_id,)).fetchone()["reason"]
        return record, reason

    record, reason = asyncio.run(refresh())
    assert reason == "read 1 tenders. Skipped documents: HTML response (1), over size limit (1)"
    assert not record["_documentsComplete"]
    assert record["documents"] == old_files

    record, reason = asyncio.run(refresh())
    assert reason == "read 1 tenders. Skipped documents: over size limit (1)"
    assert record["_documentsComplete"]
    assert [doc["fileName"] for doc in record["documents"]] == ["notice.pdf", "terms.txt", "spec.pdf"]
    assert asyncio.run(refresh()) == (record, reason)
    assert downloads == [
        "/files/archive.zip", "/files/landing.pdf", "/files/large.pdf",
        "/files/archive.zip", "/files/landing.pdf",
    ]
    assert not list(tmp_path.glob(".part-*"))


@pytest.mark.parametrize("with_relationships", [True, False], ids=["office", "ordinary-zip"])
def test_refresh_rechecks_legacy_office_entries_once(monkeypatch, tmp_path, with_relationships):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jimix.db")
    monkeypatch.setattr(watchtower, "MAX_DETAILS", None)
    db.init()
    url = "https://portal.example/files/download"
    legacy = {
        "status": "open",
        "documents": [{"fileName": name, "sha256": name} for name in ("[Content_Types].xml", "document.xml")],
        "_documentUrls": [url],
        "_documentSkips": {},
        "_documentsComplete": True,
    }
    with db.connect() as conn:
        conn.execute("INSERT INTO portal (id, url, created_at) VALUES ('files', 'https://portal.example/list', ?)",
                     (db.now(),))
        conn.execute(
            "INSERT INTO tender (portal_id, id, data, last_changed_at) VALUES ('files', '1', ?, ?)",
            (json.dumps(legacy), db.now()),
        )
    downloads = []
    package = _office_package("word/document.xml", with_relationships=with_relationships)
    name = "specification.docx" if with_relationships else "specification.zip"

    def response(request):
        if request.url.path == "/t/1":
            return httpx.Response(200, html=f'<a class="doc" href="{url}">File</a>')
        if str(request.url) == url:
            downloads.append(url)
            return httpx.Response(200, content=package,
                                  headers={"content-disposition": f'attachment; filename="{name}"'})
        return portal(request)

    recipe = Recipe.model_validate(RECIPE | {
        "listing": RECIPE["listing"] | {"records": "tr.t:first-child", "next_page": None},
    })

    async def refresh_twice():
        for _ in range(2):
            fetcher = Fetcher(transport=httpx.MockTransport(response))
            try:
                await watchtower._deliver("files", recipe, fetcher)
            finally:
                await fetcher.close()

    asyncio.run(refresh_twice())
    with db.connect() as conn:
        record = json.loads(conn.execute("SELECT data FROM tender WHERE id = '1'").fetchone()["data"])
    assert record["_documentsComplete"]
    assert downloads == [url]
    if with_relationships:
        assert [file["fileName"] for file in record["documents"]] == [name]
        assert (tmp_path / record["documents"][0]["sha256"]).read_bytes() == package
    else:
        assert [file["fileName"] for file in record["documents"]] == ["[Content_Types].xml", "document.xml"]


def test_skipped_legacy_recheck_leaves_last_documents_visible(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jimix.db")
    monkeypatch.setattr(watchtower, "MAX_DETAILS", 1)
    db.init()
    with db.connect() as conn:
        stamp = db.now()
        conn.execute("INSERT INTO portal (id, url, created_at) VALUES ('files', 'https://portal.example/list', ?)",
                     (stamp,))
        for tender_id in ("1", "2"):
            data = {
                "status": "open",
                "documents": [{"sha256": tender_id, "fileName": "previous.pdf"}],
                "_documentUrls": [f"https://portal.example/files/{tender_id}.pdf"],
                "_documentsComplete": True,
            }
            conn.execute(
                "INSERT INTO tender (portal_id, id, data, last_changed_at) VALUES ('files', ?, ?, ?)",
                (tender_id, json.dumps(data), stamp),
            )
    recipe = Recipe.model_validate(RECIPE | {
        "listing": RECIPE["listing"] | {"records": ".t", "next_page": None},
        "detail": None,
    })
    requested = []

    def response(request):
        path = request.url.path
        if path == "/list":
            rows = [
                f'<p class="t"><a href="/t/{i}">Tender {i}</a><a class="doc" href="/files/{i}.pdf">File</a></p>'
                for i in (1, 2)
            ]
            return httpx.Response(200, html="".join(rows))
        if path.startswith("/files/"):
            requested.append(path)
            return httpx.Response(200, content=b"%PDF-1.4 fixture")
        return httpx.Response(404)

    async def refresh():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            await watchtower._deliver("files", recipe, fetcher)
        finally:
            await fetcher.close()
        with db.connect() as conn:
            return json.loads(conn.execute("SELECT data FROM tender WHERE id = '2'").fetchone()["data"])

    deferred = asyncio.run(refresh())
    assert not deferred["_documentsComplete"]
    assert deferred["documents"][0]["fileName"] == "previous.pdf"
    assert requested == ["/files/1.pdf"]
    checked = asyncio.run(refresh())
    assert checked["_documentsComplete"]
    assert checked["documents"][0]["fileName"] == "2.pdf"
    assert requested == ["/files/1.pdf", "/files/2.pdf"]


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
    if match := re.fullmatch(r"/api/pub/(\d+)/(docs|attachments)", path):
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


def test_jsonpath_wildcards_and_indexes():
    data = {"content": [{"a": {"b": [{"h": "x"}, {"h": "y"}]}}, {"a": {"b": [{"h": "z"}]}}], "m": {"k": {"h": "w"}}}
    assert recipes._json_path(data, "$.content[*].a.b[*].h") == ["x", "y", "z"]
    assert recipes._json_path(data, "content[1].a.b[0].h") == "z"
    assert recipes._json_path(data, "$.content[*]") == data["content"]
    assert recipes._json_path(data, "$.m.*.h") == ["w"]
    assert recipes._json_path(data, "$.missing[*].h") is None
    assert recipes._json_path({"a": [{"$id": 1}, None, {"$id": 2}]}, "a[*].$id") == [1, 2]
    assert recipes._json_path({"a": [None, {"b": 1}]}, "$.a[*]") == [{"b": 1}]
    assert recipes._json_path({"*": 3}, "*") == 3
    for bad in ("$..href", "$.a['k']", "$.a[?(@.x)]", "$.a[-1]"):
        assert recipes._json_path(data, bad) is None
        assert recipes.bad_json_path(Recipe.model_validate(API_RECIPE | {"documents": {"path": bad}})) == bad
    assert recipes.bad_json_path(Recipe.model_validate(API_RECIPE)) is None
    assert recipes._json_path({"k": "a" * 5000 + "'"}, "a" * 5000 + "'") is None
    files = Selector(path="$.docs", attribute="links[*].href")
    assert recipes._values({"docs": [{"links": [{"href": "/a"}, {"href": "/b"}]}]}, files, "json") == ["/a", "/b"]
    redundant = Selector(path="$.docs[*].links[*].href", attribute="href")
    assert recipes._values({"docs": [{"links": [{"href": "/a"}]}]}, redundant, "json") == ["/a"]
    recipe = API_RECIPE | {"documents": {"path": "$.documenten[*].links.download.href"},
                           "listing": API_RECIPE["listing"] | {"records": "$.content[*]"}}
    tenders, _, docs = _apply(recipe, api)
    assert tenders[0]["_documentUrls"] == [f"https://api.example/api/pub/7/docs/{i}/content" for i in (1, 2)]
    assert len(docs) == 2


def test_documents_url_without_documents_is_the_file():
    recipe = API_RECIPE | {"detail": None, "documents": None,
                           "documents_url": {"template": "/api/pub/{id}/pdf"}}
    tenders, _, docs = _apply(recipe, api, max_details=0)
    assert [t["_documentUrls"] for t in tenders] == [[f"https://api.example/api/pub/{n}/pdf"] for n in (7, 8)]
    assert len(docs) == 1


def test_single_file_documents_url_suggests_a_sibling_document_list():
    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(api))
        try:
            recipe = Recipe.model_validate(API_RECIPE | {"detail": None, "documents": None,
                                                         "documents_url": {"template": "/api/pub/{id}/pdf"}})
            tenders = [{"id": "7", "_documentUrls": ["https://api.example/api/pub/7/pdf"]}]
            return await agent._document_lists(recipe, tenders, fetcher), await agent._document_lists(
                recipe, tenders, fetcher)
        finally:
            await fetcher.close()

    first, again = asyncio.run(run())
    assert len(first) == 1 and first == again
    assert first[0].startswith("https://api.example/api/pub/7/attachments returns JSON with keys ['documenten']")
    assert '"template": "https://api.example/api/pub/{id}/attachments"' in first[0]


def test_templates_must_use_mapped_fields():
    bad = API_RECIPE | {"documents_url": {"template": "https://api.example/x/{secret}", "kind": "json"}}
    with pytest.raises(ValueError, match="not mapped"):
        Recipe.model_validate(bad)
    with pytest.raises(ValueError, match="absolute"):
        Recipe.model_validate(API_RECIPE | {"documents_url": {"template": "javascript:{id}"}})
    for template in ("//other.example/{id}", "https://{id}.example/x"):
        with pytest.raises(ValueError, match="absolute"):
            Recipe.model_validate(API_RECIPE | {"documents_url": {"template": template}})


@pytest.mark.parametrize("unchecked", ["capped", "no-url", "503", "blocked"])
def test_unchecked_tender_detail_keeps_documents_pending(monkeypatch, tmp_path, unchecked):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jimix.db")
    monkeypatch.setattr(fetch, "MAX_FILE_BYTES", 1024)
    db.init()
    with db.connect() as conn:
        conn.execute("INSERT INTO portal (id, url, created_at) VALUES ('files', 'https://portal.example/list', ?)",
                     (db.now(),))

    recipe = Recipe.model_validate(RECIPE | {
        "listing": RECIPE["listing"] | {
            "records": "article",
            "fields": {
                "id": {"path": "$", "attribute": "data-id"},
                "title": {"path": "span"},
                "url": {"path": "a", "attribute": "href"},
            },
            "next_page": None,
        },
        "detail": {},
    })

    def refresh(detail):
        monkeypatch.setattr(watchtower, "MAX_DETAILS", 1 if detail == "capped" else None)

        def response(request):
            path = request.url.path
            if path == "/list":
                link = "" if detail == "no-url" else '<a href="/t/2">Open</a>'
                return httpx.Response(200, html=(
                    '<article data-id="1"><span>Tender 1</span><a href="/t/1">Open</a></article>'
                    f'<article data-id="2"><span>Tender 2</span>{link}</article>'
                ))
            if path == "/t/2":
                if detail in {"capped", "no-url"}:
                    pytest.fail("unchecked detail was requested")
                if detail == "503":
                    return httpx.Response(503)
                if detail == "blocked":
                    return httpx.Response(403)
                if detail == "files":
                    return httpx.Response(200, html=(
                        '<a class="doc" href="/bid.pdf">Bid</a><a class="doc" href="/large.pdf">Large file</a>'
                    ))
            if path == "/bid.pdf":
                return httpx.Response(200, content=b"%PDF-1.4 fixture")
            if path == "/large.pdf":
                return httpx.Response(200, content=b"x" * 1025)
            return httpx.Response(404 if path == "/robots.txt" else 200, html="<p>No file links</p>")

        async def deliver():
            fetcher = Fetcher(transport=httpx.MockTransport(response))
            try:
                return await watchtower._deliver("files", recipe, fetcher)
            finally:
                await fetcher.close()

        result = asyncio.run(deliver())
        with db.connect() as conn:
            record = json.loads(conn.execute("SELECT data FROM tender WHERE id = '2'").fetchone()["data"])
        return result, record

    result, fresh = refresh(unchecked)
    assert result == (2, None, "No document links found for 1 of 2 tenders")
    assert fresh["documents"] == fresh["_documentUrls"] == []
    assert not fresh["_documentsComplete"]

    _, complete = refresh("files")
    assert complete["_documentsComplete"]
    assert [doc["fileName"] for doc in complete["documents"]] == ["bid.pdf"]
    assert complete["_documentSkips"] == {"https://portal.example/large.pdf": fetch.OVERSIZE_SKIP}

    result, pending = refresh(unchecked)
    assert result == (2, None, "No document links found for 1 of 2 tenders")
    assert not pending["_documentsComplete"]
    assert "_documentDetailPending" not in pending
    for key in ("documents", "_documentUrls", "_documentSkips"):
        assert pending[key] == complete[key]

    result, empty = refresh("empty")
    assert result == (2, None, "No document links found for 2 of 2 tenders")
    assert empty["_documentsComplete"]
    assert empty["documents"] == empty["_documentUrls"] == []
    assert "_documentSkips" not in empty


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


NOTICE_ONLY = """<html><body><nav><a href="/help">Help</a></nav>
<a href="./announcements/1/Bekanntmachung.pdf">download</a>
<a href="/files/all.zip">Documents (ZIP)</a><a href="./documents">Vergabeunterlagen</a></body></html>"""


@pytest.mark.parametrize(("attachment_page", "file_content", "global_link"), [
    (None, b"", False), (404, b"", False), (200, b"%PDF-1.4", False),
    (200, b"%PDF-1.4 spec", False), (200, b"%PDF-1.4 spec", True),
])
def test_notice_only_validation_requires_a_distinct_tender_attachment(attachment_page, file_content, global_link):
    detail = '<a class="doc" href="/Bekanntmachung.pdf">Download</a>'
    if attachment_page:
        link = '<a href="/documents">Tender documents</a>'
        detail += f"<nav>{link}</nav>" if global_link else link

    def response(request):
        if request.url.path.startswith("/t/"):
            return httpx.Response(200, html=detail)
        if request.url.path == "/Bekanntmachung.pdf":
            return httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"})
        if request.url.path == "/documents":
            return httpx.Response(attachment_page, html='<a href="/Specification.pdf">Specification</a>')
        if request.url.path == "/Specification.pdf":
            return httpx.Response(200, content=file_content, headers={"content-type": "application/pdf"})
        return portal(request)

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            return await agent._test(Recipe.model_validate(RECIPE), fetcher)
        finally:
            await fetcher.close()

    report = asyncio.run(run())
    if file_content == b"%PDF-1.4 spec" and not global_link:
        assert "separate documents page" in report["problem"]
        assert any("/documents" in warning for warning in report["warnings"])
    else:
        assert report["problem"] is None


@pytest.mark.parametrize(("page", "hinted"), [(NOTICE_ONLY, True), (DETAIL.format(n=1), False)])
def test_recipe_test_hints_at_linked_documents_page(page, hinted):
    def site(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200, html=page)

    recipe = Recipe.model_validate(RECIPE)
    tenders = [{"id": "1", "url": "https://portal.example/p/1/overview",
                "_documentUrls": ["https://portal.example/p/1/announcements/1/Bekanntmachung.pdf"]}]

    async def run(recipe, *, retry_mapping=False):
        fetcher = Fetcher(transport=httpx.MockTransport(site))
        try:
            return await agent._document_page_hint(recipe, tenders, fetcher, retry_mapping=retry_mapping)
        finally:
            await fetcher.close()

    hint, confirmed = asyncio.run(run(recipe))
    assert not confirmed
    if hinted:
        assert "'vergabeunterlagen' (https://portal.example/p/1/documents)" in hint
    else:
        assert hint is None
    with_page = Recipe.model_validate(RECIPE | {"documents_page": {"path": "a.docs"}})
    assert asyncio.run(run(with_page)) == (None, False)
    retry_hint, confirmed = asyncio.run(run(with_page, retry_mapping=True))
    assert not confirmed
    assert bool(retry_hint) == hinted


@pytest.mark.parametrize("by_host", [False, True])
def test_refresh_preserves_files_after_document_page_error(monkeypatch, tmp_path, by_host):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jimix.db")
    db.init()
    with db.connect() as conn:
        conn.execute("INSERT INTO portal (id, url, created_at) VALUES ('portal', 'https://portal.example/list', ?)",
                     (db.now(),))

    status = 200
    html = '<a class="doc" href="/spec.pdf">Specification</a>'
    downloads = 0

    def response(request):
        nonlocal downloads
        if request.url.path == "/t/3":
            return httpx.Response(200, html='<a class="platform" href="/documents">Documents</a>')
        if request.url.path == "/documents":
            return httpx.Response(status, html=html)
        if request.url.path == "/spec.pdf":
            downloads += 1
            return httpx.Response(200, content=b"%PDF-1.4 fixture")
        if request.url.path == "/list":
            return httpx.Response(200, html=LIST_2)
        return httpx.Response(404)

    settings = {"documents_page": {"path": "a.platform"}}
    if by_host:
        settings = {
            "documents_by_host": {"portal.example": {"page": {"path": "a.platform"}, "files": {"path": "a.doc"}}},
        }
    recipe = Recipe.model_validate(RECIPE | settings)

    async def deliver():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            outcome = await watchtower._deliver("portal", recipe, fetcher)
        finally:
            await fetcher.close()
        with db.connect() as conn:
            stored = json.loads(conn.execute("SELECT data FROM tender").fetchone()[0])
        return outcome, stored

    _, initial = asyncio.run(deliver())
    assert initial["documents"][0]["fileName"] == "spec.pdf"
    assert initial["_documentsComplete"] is True

    status = 503
    result, failed = asyncio.run(deliver())
    assert result == (1, None, "Could not read document pages for 1 of 1 tenders")
    for key in ("documents", "_documentUrls", "_documentSkips"):
        assert failed[key] == initial[key]
    assert failed["_documentsComplete"] is False
    assert "_documentPageError" not in failed

    status = 200
    _, recovered = asyncio.run(deliver())
    assert recovered["_documentsComplete"] is True
    assert downloads == 2

    html = "<p>No documents</p>"
    result, empty = asyncio.run(deliver())
    assert result == (1, None, "No document links found for 1 of 1 tenders")
    assert empty["documents"] == []
    assert empty["_documentsComplete"] is True


@pytest.mark.parametrize(("attribute", "pattern"), [
    ("onclick", None), ("onclick", "'(/notices/categories[?]table=list&pageIndex=[0-9]+)'"), ("href", None),
])
def test_category_pagination_uses_the_public_get_path(attribute, pattern):
    category = "/notices/categories"
    next_page = category + "?table=list&pageIndex=2"
    control = (
        f"""<a class="browseForward" href="javascript:void(0);" """
        f"""onclick="setTargetAndSubmit('{next_page}'); untrusted()">next</a>"""
    )
    original_next = '<a class="next" href="/list?page=2">next</a>'
    listing = LIST_1.replace(original_next, control)
    simplified, hint, _ = agent.simplify(listing)
    assert "pageIndex=2" in simplified
    assert hint == next_page
    assert "untrusted()" not in simplified
    for key in ("csrf", "csrfToken", "sid", "signature"):
        assert agent.simplify(control.replace("pageIndex=2", f"pageIndex=2&{key}=private"))[1] is None
    assert agent.simplify(control.replace(category, "//outside.example/path"))[1] is None
    assert agent.simplify(control.replace("pageIndex=2", "restart=2"))[1] is None
    assert agent.simplify(control.replace("');", "' + other);"))[1] is None
    assert agent.simplify(control.replace("javascript:void(0);", "/download"))[1] is None
    double_quoted = control.replace(f"'{next_page}'", f"&quot;{next_page}&quot;")
    double_quoted = BeautifulSoup(agent.simplify(double_quoted)[0], "html.parser").select_one("a")
    assert double_quoted["onclick"] == f'setTargetAndSubmit("{next_page}")'
    first_chunk = agent._page_content("<p>" + "x" * agent.PAGE_CHARS + "</p>" + control)
    assert first_chunk.splitlines()[0].endswith(next_page)
    selector = {"path": "a.browseForward", "attribute": attribute, "pattern": pattern}
    if attribute == "href":
        extra = '<a class="browseForward" href="javascript:void(0);" onclick="unsupported()">Other</a>'
        listing = LIST_1.replace(original_next, extra + control)
    recipe = Recipe.model_validate(RECIPE | {
        "listing": RECIPE["listing"] | {
            "url": f"https://portal.example{category}?method=showCategoryOverview",
            "index": {"path": "a[href*='cpvCode=']"},
            "next_page": selector,
        },
        "detail": None,
        "documents": None,
    })
    requested = []
    active_category = None

    def response(request: httpx.Request):
        nonlocal active_category
        requested.append(request.url)
        if request.url.params.get("method") == "showCategoryOverview":
            links = [
                f'<a href="{category}?method=showTable&cpvCode={code}">{code}</a>'
                for code in ("90000000-7", "45000000-7")
            ]
            return httpx.Response(200, html="".join(links))
        page = listing
        if code := request.url.params.get("cpvCode"):
            active_category = code
        elif "pageIndex" in request.url.params:
            page = LIST_2
        if active_category == "45000000-7":
            page = page.replace("/t/1", "/t/4").replace("/t/2", "/t/5").replace("/t/3", "/t/6")
        return httpx.Response(200, html=page)

    async def run(max_pages=None):
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            return await apply(recipe, fetcher, max_pages=max_pages)
        finally:
            await fetcher.close()

    tenders, complete = asyncio.run(run())
    assert [tender["id"] for tender in tenders] == ["1", "2", "3", "4", "5", "6"]
    assert complete
    assert requested.count(httpx.URL(f"https://portal.example{next_page}")) == 2
    requested.clear()
    sampled, complete = asyncio.run(run(max_pages=3))
    assert [tender["id"] for tender in sampled] == ["1", "2", "4", "5"]
    assert not complete
    assert len([url for url in requested if url.path != "/robots.txt"]) == 3


@pytest.mark.parametrize(("handler", "expected"), [
    ("window.location.href='/list?page=3'", ("/list?page=3", False)),
    ("setTargetAndSubmit('/list?page=2'); marker('/list?page=3')", (None, True)),
    ("return setTargetAndSubmit('/list?page=3' + suffix)", (None, True)),
])
def test_patterned_pagination_keeps_its_literal_or_leaves_scan_incomplete(handler, expected):
    page = BeautifulSoup(f'<a class="browseForward" href="#" onclick="{handler}">Next</a>', "html.parser")
    selector = recipes.Selector(path="a", attribute="onclick", pattern=r"(/list\?page=3)")
    assert recipes._next_link(page, selector, "html") == expected


@pytest.mark.parametrize("handler", [
    "setTargetAndSubmit('//outside.example/list?page=2')",
    "setTargetAndSubmit('/list?pageIndex=2&X-Amz-Signature=private')",
    "setTargetAndSubmit('/list?restart=2')",
    "setTargetAndSubmit('/list?pageIndex=2' + other)",
    "unrelatedsetTargetAndSubmit('/list?pageIndex=2')",
])
@pytest.mark.parametrize("href", ["#", "javascript:void(0);"])
@pytest.mark.parametrize("attribute", ["href", "onclick"])
def test_pagination_skips_unsafe_handlers_and_unselected_controls(handler, href, attribute):
    recipe = Recipe.model_validate(RECIPE | {
        "listing": RECIPE["listing"] | {"next_page": {"path": "a.selected", "attribute": attribute}},
        "detail": None,
        "documents": None,
    })
    controls = (
        f'<a class="browseForward selected" href="{href}" onclick="{handler}">Selected</a>'
        """<a class="browseForward" href="#" onclick="setTargetAndSubmit('/list?pageIndex=2')">Other</a>"""
    )
    requests = []

    def response(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        listing = LIST_1.replace('<a class="next" href="/list?page=2">next</a>', controls)
        return httpx.Response(200, html=listing)

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            return await apply(recipe, fetcher)
        finally:
            await fetcher.close()

    tenders, complete = asyncio.run(run())
    assert not complete
    assert [tender["id"] for tender in tenders] == ["1", "2"]
    assert requests == ["https://portal.example/robots.txt", "https://portal.example/list"]


def test_category_index_hint_surfaces_same_site_category_links_before_truncated_markup():
    categories = "".join(
        f'<a href="/notices?language=en&amp;classificationId={index}#access_token=fragment">Category</a>'
        for index in range(4)
    )
    unrelated = "".join(
        f'<a href="//other.example/search?category={index}">Other</a>'
        f'<a href="/notices?category={index}&amp;token=private">Skip</a>'
        f'<a href="/tender?id={index}">Tender</a>'
        for index in range(8)
    )
    html = f"<p>{'x' * agent.PAGE_CHARS}</p>{categories}{unrelated}"
    hint = agent._page_content(html, base="https://portal.example/categories").splitlines()[0]
    assert hint.startswith("Possible category index: 4 links share path '/notices'")
    assert "classificationId" in hint and "listing.index" in hint
    assert "token" not in hint and "fragment" not in hint and "other.example" not in hint
    assert "Selector matching this group" in hint
    hint = agent._page_content(
        html + '<noscript><a href="/other?classificationId=9">Other category</a></noscript>',
        base="https://portal.example/categories",
    ).splitlines()[0]
    assert "Selector matching this group" not in hint


def test_document_page_hint_surfaces_public_links_before_truncated_markup():
    html = f"""
    <p>{'x' * agent.PAGE_CHARS}</p>
    <a href="/files/notice.pdf">Tender documents</a>
    <a href="/portal;jsessionid=hidden/notice/documents#access_token=hidden">Vergabeunterlagen</a>
    <a href="/private?token=hidden">Tender documents</a>
    <a href="/download?X-Amz-Signature=hidden">Tender documents</a>
    <a href="">Tender documents</a>
    """
    lines = agent._page_content(html, base="https://portal.example/tender").splitlines()
    hints = [line for line in lines if line.startswith("Possible document-page link:")]
    assert hints == [
        "Possible document-page link: 'vergabeunterlagen' -> 'https://portal.example/portal/notice/documents'"
    ]


def test_public_netserver_control_surfaces_supported_modal_before_truncated_markup():
    control = '<a class="zipFileContents" data-oid="fixture-oid" data-token="fixture-token">Files</a>'
    html = f"<p>{'x' * agent.PAGE_CHARS}</p>{control}"

    hint = agent._page_content(html, base="https://portal.example/notice").splitlines()[0]

    assert "modal=netserver_file_tree" in hint
    assert "a.filenameEllipsis[href]" in hint
    assert "fixture-oid" not in hint and "fixture-token" not in hint


@pytest.mark.parametrize("indexed_open_count", [30, 20])
def test_agent_compares_open_tender_coverage_of_validated_index_and_plain_list(monkeypatch, indexed_open_count):
    indexed = Recipe.model_validate(RECIPE | {
        "listing": RECIPE["listing"] | {"index": {"path": "a.category"}},
    })
    narrow = Recipe.model_validate(RECIPE | {"listing": RECIPE["listing"] | {"next_page": None}})

    def completion(recipe):
        function = SimpleNamespace(name="submit_recipe", arguments=json.dumps({"recipe": recipe.model_dump()}))
        message = SimpleNamespace(
            tool_calls=[SimpleNamespace(id="call", function=function)],
            model_dump=lambda **_: {"role": "assistant"},
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    async def validate(_, args, _fetcher):
        recipe = Recipe.model_validate(args["recipe"])
        report = {"tenderCount": 20, "openTenderCount": 20, "warnings": ["some details are pending"]}
        if recipe.listing.index:
            report.update(tenderCount=40, openTenderCount=indexed_open_count)
        return json.dumps(report), recipe

    create = AsyncMock(side_effect=[completion(indexed), completion(narrow), completion(indexed)])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(agent, "AsyncOpenAI", lambda **_: client)
    monkeypatch.setattr(agent, "discover", AsyncMock(return_value=""))
    monkeypatch.setattr(agent, "_call", validate)

    found = asyncio.run(agent.explore("https://portal.example/", None, agent.Usage()))

    if indexed_open_count > 20:
        assert found == indexed
        assert create.await_count == 3
        notice = create.call_args.kwargs["messages"][-2]["content"]
        assert "NOT ACCEPTED" in notice and "30" in notice and "20" in notice
    else:
        assert found == narrow
        assert create.await_count == 2


def test_recipe_validation_checks_open_bidding_after_advance_notices():
    advance_notices = "".join(
        f'<tr class="t"><td><a href="/t/{i}">Advance notice {i}</a></td></tr>' for i in (4, 5, 6)
    )
    listing = LIST_1.replace("<table>", f"<table>{advance_notices}", 1)
    recipe = Recipe.model_validate(RECIPE | {
        "listing": RECIPE["listing"] | {"next_page": None},
    })
    checked = []

    def response(request: httpx.Request):
        path = request.url.path
        if path == "/list":
            return httpx.Response(200, html=listing)
        if path.startswith("/t/"):
            checked.append(path)
            if path != "/t/1":
                return httpx.Response(200, html="<p>Documents have not been published yet.</p>")
        return portal(request)

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            return await agent._test(recipe, fetcher)
        finally:
            await fetcher.close()

    result = asyncio.run(run())
    assert checked[:3] == ["/t/1", "/t/4", "/t/5"]
    assert set(checked) == {"/t/1", "/t/4", "/t/5"}
    assert result["problem"] is None
    assert result["withDocumentLinks"] == 1
    assert {tender["id"] for tender in result["sample"]} == {"1", "4", "5"}


def test_capped_detail_sample_uses_first_valid_tender_occurrences():
    listing = """
    <table>
      <tr class="t" data-id="1"><td><a href="/t/1">First occurrence</a></td></tr>
      <tr class="t" data-id="1"><td><a href="/t/1">Duplicate</a></td>
        <td class="due">20.12.2099 10:00</td></tr>
      <tr class="t" data-id="3"><td><a>Missing URL</a></td><td class="due">20.12.2099 10:00</td></tr>
      <tr class="t"><td><a href="/t/4">Missing ID</a></td><td class="due">20.12.2099 10:00</td></tr>
      <tr class="t" data-id="2"><td><a href="/t/2">Current bid</a></td>
        <td class="due">20.12.2099 10:00</td></tr>
    </table>
    """
    fields = RECIPE["listing"]["fields"] | {"id": {"path": "$", "attribute": "data-id"}}
    recipe = Recipe.model_validate(RECIPE | {"listing": RECIPE["listing"] | {"fields": fields}})
    fetched = []

    def response(request: httpx.Request):
        if request.url.path == "/list":
            return httpx.Response(200, html=listing)
        if request.url.path.startswith("/t/"):
            fetched.append(request.url.path)
        return portal(request)

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            return await apply(recipe, fetcher, max_details=1)
        finally:
            await fetcher.close()

    tenders, _ = asyncio.run(run())
    assert fetched == ["/t/2"]
    assert tenders[0]["title"] == "First occurrence"
    assert tenders[0]["_documentDetailPending"]
    assert tenders[2]["_documentUrls"] == ["https://portal.example/files/2.zip"]


@pytest.mark.parametrize("missing_b_deadlines", [False, True])
def test_indexed_validation_samples_details_across_categories(missing_b_deadlines):
    recipe = Recipe.model_validate(RECIPE | {
        "listing": RECIPE["listing"] | {
            "index": {"path": "a.category"},
            "next_page": None,
            "fields": RECIPE["listing"]["fields"] | {"id": {"path": "$", "attribute": "data-id"}},
        },
    })
    future = "20.12.2099 10:00"
    rows = {
        "/a": [("expired", "expired", "20.12.2000 10:00"), ("1", "a1", future),
               ("2", "a2", future), ("3", "a3", future)],
        "/b": [("1", "duplicate", "" if missing_b_deadlines else future),
               ("4", "b1", "" if missing_b_deadlines else future),
               ("5", "b2", "" if missing_b_deadlines else future)],
    }
    checked, downloaded = [], []

    def response(request):
        path = request.url.path
        if path == "/list":
            return httpx.Response(200, html='<a class="category" href="/a">A</a><a class="category" href="/b">B</a>')
        if path in rows:
            table = "".join(
                f'<tr class="t" data-id="{number}"><td><a href="/t/{name}">{name}</a></td>'
                f'<td class="due">{deadline}</td></tr>'
                for number, name, deadline in rows[path]
            )
            return httpx.Response(200, html=f"<table>{table}</table>")
        if path.startswith("/t/"):
            checked.append(path)
            link = '<a class="doc" href="/spec.pdf">Specification</a>' if path == "/t/b1" else ""
            return httpx.Response(200, html=link)
        if path == "/spec.pdf":
            downloaded.append(path)
            return httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    async def run(validation: bool):
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            if validation:
                return await agent._test(recipe, fetcher)
            return await apply(recipe, fetcher, max_details=3)
        finally:
            await fetcher.close()

    report = asyncio.run(run(validation=True))
    assert checked[:3] == ["/t/a1", "/t/b1", "/t/a2"]
    assert report["problem"] is None
    assert report["openTenderCount"] == 5
    assert report["withDocumentLinks"] == 1
    assert downloaded == ["/spec.pdf"]

    checked.clear()
    asyncio.run(run(validation=False))
    assert checked == ["/t/a1", "/t/a2", "/t/a3"]


@pytest.mark.parametrize(("html", "urls"), [
    (
        (
            '<a class="zipFileContents" data-oid="row" data-token="public">Files</a>'
            '<a class="doc" href="/spec.pdf">Specification</a>'
        ),
        ["https://portal.example/spec.pdf"],
    ),
    ('<a class="zipFileContents" data-oid="row">Files</a>', []),
    ('<a class="zipFileContents" data-oid="row" data-token="">Files</a>', []),
])
def test_netserver_fallback_preserves_configured_links_and_requires_complete_control(monkeypatch, html, urls):
    async def unexpected_render(*args, **kwargs):
        raise AssertionError("Expected HTML extraction without a modal")

    monkeypatch.setattr(recipes, "render", unexpected_render)
    recipe = Recipe.model_validate(RECIPE)
    page = BeautifulSoup(html, "html.parser")
    assert asyncio.run(recipes._document_links(recipe, page, "https://portal.example/notice", None)) == urls


def test_blocked_automatic_modal_keeps_other_tenders_accessible(monkeypatch, tmp_path):
    async def blocked_render(url, _fetcher, *, netserver_files):
        assert url == "https://portal.example/t/1" and netserver_files
        raise Blocked("robots.txt disallows the file list")

    def respond(request):
        if request.url.path == "/t/1":
            control = '<a class="zipFileContents" data-oid="row" data-token="public">Files</a>'
            return httpx.Response(200, html=control)
        return portal(request)

    monkeypatch.setattr(recipes, "render", blocked_render)
    recipe = Recipe.model_validate(RECIPE | {"listing": RECIPE["listing"] | {"next_page": None}})

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(respond))
        try:
            tenders, complete = await apply(recipe, fetcher)
            first, second = tenders
            assert complete
            assert first["_documentPageError"] and not first["_documentUrls"]
            saved, downloaded, _ = await fetch_documents(second["_documentUrls"], fetcher)
            assert downloaded
            assert (tmp_path / saved[0]["sha256"]).read_bytes().startswith(b"%PDF")
        finally:
            await fetcher.close()

    asyncio.run(run())


def test_files_also_in_an_archive_are_listed_once():
    def site(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/single/notice.pdf":
            return httpx.Response(200, content=b"%PDF-1.4 notice", headers={"content-type": "application/pdf"})
        return portal(request)

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(site))
        try:
            urls = ["https://portal.example/single/notice.pdf", "https://portal.example/files/all.zip"]
            return await fetch_documents(urls, fetcher)
        finally:
            await fetcher.close()

    docs, complete, _ = asyncio.run(run())
    assert complete
    assert [d["fileName"] for d in docs] == ["notice.pdf", "terms.txt"]


def test_tls_reads_issuer_url_and_detects_incomplete_chains():
    import datetime
    import ssl

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID

    from app import tls

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "portal.example")])
    now = datetime.datetime.now(datetime.UTC)
    aia = x509.AuthorityInformationAccess([x509.AccessDescription(
        AuthorityInformationAccessOID.CA_ISSUERS, x509.UniformResourceIdentifier("http://ca.example/int.crt"))])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(aia, critical=False).sign(key, hashes.SHA256()))
    assert tls.issuer_url(cert) == "http://ca.example/int.crt"
    assert tls._load(cert.public_bytes(Encoding.PEM)) == cert
    assert tls._load(cert.public_bytes(Encoding.DER)) == cert
    assert not tls.verified_path("portal.example", cert, [cert])
    error = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                                            "unable to get local issuer certificate (_ssl.c:1010)")
    assert tls.incomplete_chain(error)
    assert not tls.incomplete_chain(ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED: hostname mismatch"))


@pytest.mark.parametrize(("failure", "earlier"), [(httpx.ConnectError("connection reset"), 0), (503, 0), (503, 2)])
def test_refresh_does_not_repair_after_a_temporary_failure(monkeypatch, tmp_path, failure, earlier):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jimix.db")
    db.init()

    async def no_agent(*args, **kwargs):
        raise AssertionError("a temporary failure must not start a repair")

    monkeypatch.setattr(watchtower, "explore", no_agent)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO portal (id, url, state, created_at) VALUES ('p', 'https://portal.example/list', 'serving', ?)",
            (db.now(),),
        )
    attach = watchtower._new_run("p", "read")
    watchtower._save_recipe("p", attach, Recipe.model_validate(RECIPE))
    watchtower._finish(attach, "ok", "read 3 tenders", agent.Usage())
    for _ in range(earlier):
        watchtower._finish(watchtower._new_run("p", "read"), "failed", "ConnectError: reset", agent.Usage())
    run_id = watchtower._new_run("p", "read")

    def site(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if isinstance(failure, int):
            return httpx.Response(failure)
        raise failure

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(site))
        try:
            await watchtower._read("p", run_id, fetcher)
        finally:
            await fetcher.close()

    asyncio.run(run())
    with db.connect() as conn:
        portal_row = conn.execute("SELECT state, reason FROM portal").fetchone()
        runs = conn.execute("SELECT kind, result FROM run WHERE id = ?", (run_id,)).fetchall()
        repairs = conn.execute("SELECT count(*) FROM run WHERE kind = 'repair'").fetchone()[0]
    if earlier:
        assert portal_row["state"] == "failed"
        assert portal_row["reason"].startswith("Failing on temporary errors for 3 reads in a row")
    else:
        assert portal_row["state"] == "serving"
        assert "temporary error" in portal_row["reason"]
    assert [tuple(r) for r in runs] == [("read", "failed")]
    assert repairs == 0


def test_post_json_listing_pages_inside_the_body():
    import json as jsonlib

    bodies = []
    pages = {0: [{"id": "N-1", "title": "Bridge works", "link": "/n/1"}, {"id": "N-2", "title": "IT services", "link": "/n/2"}],
             1: [{"id": "N-3", "title": "Road supplies", "link": "/n/3"}], 2: []}

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.method == "POST" and request.url.path == "/api/search":
            body = jsonlib.loads(request.content)
            bodies.append(body)
            return httpx.Response(200, json={"hits": pages[body["paging"]["page"]]})
        return httpx.Response(404)

    recipe = Recipe.model_validate({
        "method": "discovered_api",
        "listing": {"kind": "json", "url": "https://portal.example/api/search", "method": "POST",
                    "body": {"query": "", "paging": {"page": 0, "size": 2}}, "records": "hits",
                    "page_param": "paging.page",
                    "fields": {"id": {"path": "id"}, "title": {"path": "title"}, "url": {"path": "link"}}},
    })

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(api))
        try:
            return await apply(recipe, fetcher)
        finally:
            await fetcher.close()

    tenders, complete = asyncio.run(run())
    assert [t["id"] for t in tenders] == ["N-1", "N-2", "N-3"]
    assert [b["paging"]["page"] for b in bodies] == [0, 1, 2]
    assert all(b["query"] == "" and b["paging"]["size"] == 2 for b in bodies)
    assert complete
    assert tenders[0]["url"] == "https://portal.example/n/1"


def test_post_listing_requires_json_body_and_observed_endpoint():
    base = {"method": "discovered_api", "listing": {"kind": "json", "url": "https://portal.example/api/search",
            "method": "POST", "records": "hits", "fields": {"id": {"path": "id"}}}}
    with pytest.raises(ValueError, match="JSON body"):
        Recipe.model_validate(base)
    fetcher = Fetcher()
    posting = base | {"listing": base["listing"] | {"body": {"q": "", "page": 0}, "page_param": "page"}}
    output, recipe = asyncio.run(agent._call("test_recipe", {"recipe": posting}, fetcher))
    assert output.startswith("REFUSED: a POST must repeat") and recipe is None
    fetcher.observed_posts["https://portal.example/api/search/"] = [{"q": "", "page": 3}]
    assert agent._observed_post(Recipe.model_validate(posting), fetcher)
    fetcher.observed_posts["https://portal.example/api/search/"] = [{"q": "x", "page": 0}]
    assert not agent._observed_post(Recipe.model_validate(posting), fetcher)
    fetcher.observed_posts["https://portal.example/api/search/"] = [{"q": "", "page": 0, "extra": "x"}]
    assert not agent._observed_post(Recipe.model_validate(posting), fetcher)
    assert not agent.same_shape({"n": True}, {"n": 5})
    assert not agent.same_shape({"n": 1}, {"n": True})
    assert agent.same_shape({"n": 1, "s": "a"}, {"n": 50.0, "s": "a"})


def test_post_paging_stops_cleanly_on_a_later_client_error():
    import json as jsonlib

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        page = jsonlib.loads(request.content)["page"]
        if page > 2:
            return httpx.Response(400, json={"error": "too deep"})
        return httpx.Response(200, json={"hits": [{"id": f"P{page}", "title": "Works", "link": f"/n/{page}"}]})

    recipe = Recipe.model_validate({
        "method": "discovered_api",
        "listing": {"kind": "json", "url": "https://portal.example/api/search", "method": "POST",
                    "body": {"page": 1}, "records": "hits", "page_param": "page",
                    "fields": {"id": {"path": "id"}, "title": {"path": "title"}, "url": {"path": "link"}}},
    })

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(api))
        try:
            return await apply(recipe, fetcher)
        finally:
            await fetcher.close()

    tenders, complete = asyncio.run(run())
    assert [t["id"] for t in tenders] == ["P1", "P2"]
    assert not complete


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<a href="/documents" aria-label="Tender documents"><i></i></a>', "https://portal.example/documents"),
        ('<a href="/documents" title="Documents"><i></i></a>', "https://portal.example/documents"),
        ('<a href="/specification.pdf">File</a>', "https://portal.example/specification.pdf"),
        ('<a href="/attachment?id=1" download>File</a>', "https://portal.example/attachment?id=1"),
        ('<a href="javascript:void(0)" aria-label="Documents">Files</a>', None),
        ('<template><a href="/documents">Documents</a></template>', None),
        ('<div hidden><a href="/documents">Documents</a></div>', None),
    ],
)
def test_document_hints_find_ordinary_accessible_links(html, expected):
    requests = []

    def response(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        requests.append(request.url.path)
        return httpx.Response(200, html=html)

    recipe = Recipe.model_validate(RECIPE)
    tender = {"url": "https://portal.example/t/1", "_documentUrls": []}

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            return await agent._document_page_hint(recipe, [tender], fetcher)
        finally:
            await fetcher.close()

    hint, confirmed = asyncio.run(run())
    assert not confirmed
    if expected:
        assert expected in hint
    else:
        assert hint is None
    assert requests == ["/t/1"]


def test_page_content_hints_keep_accessible_labels_and_skip_hidden_links():
    page = ('<a href="/hidden" hidden>Documents</a>'
            '<a href="/download" download aria-label="Documents"></a>'
            '<a href="/tender-files" aria-label="Tender documents"></a>')
    hints = agent.simplify(page, "https://portal.example/t/1")[2]
    assert hints == ["Possible document-page link: 'tender documents' -> 'https://portal.example/tender-files'"]


def test_page_cap_is_reported_in_validation_and_delivery(monkeypatch, tmp_path):
    async def covered_documents(*_args, **_kwargs):
        return None, []

    monkeypatch.setattr(agent, "_check_documents", covered_documents)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "jimix.db")
    db.init()
    with db.connect() as conn:
        conn.execute("INSERT INTO portal (id, url, created_at) VALUES ('test', 'https://portal.example/list', ?)",
                     (db.now(),))
        conn.execute(
            "INSERT INTO tender (portal_id, id, data, last_changed_at) VALUES ('test', 'old', ?, ?)",
            ('{"status":"open"}', db.now()),
        )

    async def run(max_pages):
        recipe = Recipe.model_validate(RECIPE | {"max_pages": max_pages, "detail": None, "documents": None})
        fetcher = Fetcher(transport=httpx.MockTransport(portal))
        try:
            report = await agent._test(recipe, fetcher)
            delivered = await watchtower._deliver("test", recipe, fetcher)
            return report["warnings"], delivered
        finally:
            await fetcher.close()

    warnings, (count, _, note) = asyncio.run(run(1))
    assert count == 2 and "Listing coverage incomplete" in note
    assert any("recipe max_pages: 1" in warning for warning in warnings)
    with db.connect() as conn:
        assert conn.execute("SELECT data FROM tender WHERE id='old'").fetchone()[0] == '{"status":"open"}'

    warnings, (count, _, note) = asyncio.run(run(2))
    assert count == 3 and "Listing coverage incomplete" not in note
    assert not any("Listing coverage is incomplete" in warning for warning in warnings)


@pytest.mark.parametrize(
    ("link", "hinted"),
    [
        ('<link rel="next" href="/list/2">', True),
        ('<a href="/list?page=2" aria-label="Nächste Seite">›</a>', True),
        ('<a href="/list?offset=20">2</a>', True),
        ('<a href="/list?offset=20&buyer=42">2</a>', False),
        ('<a href="https://other.example/list?page=2" rel="next">Next</a>', False),
        ('<a href="#next" rel="next">Next</a>', False),
        ('<template><a href="/list?page=2">Next</a></template>', False),
    ],
)
def test_recipe_validation_warns_about_unhandled_pagination(monkeypatch, link, hinted):
    async def covered_documents(*_args, **_kwargs):
        return None, []

    monkeypatch.setattr(agent, "_check_documents", covered_documents)
    listing = {**RECIPE["listing"], "next_page": None}
    recipe = Recipe.model_validate({**RECIPE, "listing": listing, "detail": None, "documents": None})
    requests = []

    def response(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        requests.append(str(request.url))
        return httpx.Response(200, html=LIST_2 + link)

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            return await agent._test(recipe, fetcher)
        finally:
            await fetcher.close()

    report = asyncio.run(run())
    assert any("likely next-page" in warning for warning in report["warnings"]) == hinted
    assert requests == ["https://portal.example/list"]
