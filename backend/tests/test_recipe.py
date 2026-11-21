import asyncio
import io
import json
import re
import zipfile

import httpx
import pytest
from playwright.async_api import Error as PlaywrightError

from app import agent, db, documents, fetch, watchtower
from app import recipe as recipes
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
            tenders = [{"id": "7", "_documentUrls": ["https://api.example/api/pub/7/pdf"]}]
            return await agent._document_lists(tenders, fetcher)
        finally:
            await fetcher.close()

    hints = asyncio.run(run())
    assert len(hints) == 1 and hints[0].startswith("https://api.example/api/pub/7/attachments returns JSON")


def test_templates_must_use_mapped_fields():
    bad = API_RECIPE | {"documents_url": {"template": "https://api.example/x/{secret}", "kind": "json"}}
    with pytest.raises(ValueError, match="not mapped"):
        Recipe.model_validate(bad)
    with pytest.raises(ValueError, match="absolute"):
        Recipe.model_validate(API_RECIPE | {"documents_url": {"template": "javascript:{id}"}})


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


def test_cosinex_category_pagination_uses_the_public_get_path():
    category = "/VMPCenter/company/announcements/categoryOverview.do"
    next_page = category + "?method=showTable&fromSearch=1&selectedTablePagePROJECT_RESULT=2"
    control = (
        f"""<a class="browseForward" href="javascript:void(0);" """
        f"""onclick="setTargetAndSubmit('{next_page}'); untrusted()">next</a>"""
    )
    listing = LIST_1.replace('<a class="next" href="/list?page=2">next</a>', control)
    simplified = agent.simplify(listing)
    assert "selectedTablePagePROJECT_RESULT=2" in simplified
    assert "untrusted()" not in simplified
    recipe = Recipe.model_validate(RECIPE | {
        "listing": RECIPE["listing"] | {
            "url": f"https://portal.example{category}?method=showCategoryOverview",
            "index": {"path": "a[href*='cpvCode=']"},
            "next_page": {
                "path": "a.browseForward",
                "attribute": "onclick",
                "pattern": f"'{agent.COSINEX_PAGE_PATH}'",
            },
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
        elif "selectedTablePagePROJECT_RESULT" in request.url.params:
            page = LIST_2
        if active_category == "45000000-7":
            page = page.replace("/t/1", "/t/4").replace("/t/2", "/t/5").replace("/t/3", "/t/6")
        return httpx.Response(200, html=page)

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            return await apply(recipe, fetcher)
        finally:
            await fetcher.close()

    tenders, complete = asyncio.run(run())
    assert [tender["id"] for tender in tenders] == ["1", "2", "3", "4", "5", "6"]
    assert complete
    assert requested.count(httpx.URL(f"https://portal.example{next_page}")) == 2
