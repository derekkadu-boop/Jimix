import asyncio
import io
import zipfile
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import httpx
import pytest
import pytest_asyncio

from app import db, documents, fetch, main, watchtower
from app.recipe import Recipe

PORTAL_URL = "https://portal.example/"
FILE_BYTES = {"notice.pdf": b"%PDF-1.4 test notice", "terms.txt": b"Contract terms"}
OPTIONAL_FIELDS = {
    "referenceNumber", "description", "procedureType", "contractNature", "cpvCodes", "placeOfPerformance",
}
NOW = "2026-11-21T08:00:00+01:00"
LATER = "2026-11-21T08:05:00+01:00"


@pytest.fixture
def fake_portal(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(main, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(documents, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(fetch, "INTERVAL", 0)
    monkeypatch.setattr(watchtower, "MAX_DETAILS", None)
    monkeypatch.setattr(watchtower, "_background", set())
    db.init()

    class Portal:
        title = "Road works"
        listing = "/notices"
        blocked = False

        def __init__(self):
            self.requests = []
            self.started = asyncio.Event()
            self.resume = asyncio.Event()

        async def respond(self, request):
            path = request.url.path
            self.requests.append(path)
            if path == "/robots.txt":
                return httpx.Response(404)
            if path == "/bundle.zip":
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, "w") as bundle:
                    for name, data in FILE_BYTES.items():
                        bundle.writestr(name, data)
                return httpx.Response(
                    200, content=archive.getvalue(), headers={"content-type": "application/zip"},
                )
            if path != self.listing:
                return httpx.Response(404)
            self.started.set()
            await self.resume.wait()
            if self.blocked:
                return httpx.Response(200, html='<div class="g-recaptcha">Please verify you are human</div>')
            return httpx.Response(200, html=f"""
                <article class="tender"><a href="/tender/A1">{self.title}</a>
                <a class="document" href="/bundle.zip">Documents</a></article>
                <article class="tender"><a href="/tender/B2">Tree maintenance</a></article>
                """.strip())

        async def explore(self, url, fetcher, usage, hint=None):
            return Recipe.model_validate({
                "method": "html",
                "listing": {
                    "url": url.rstrip("/") + self.listing,
                    "records": "article.tender",
                    "fields": {
                        "id": {"path": "a", "attribute": "href", "pattern": r"/tender/([^/]+)"},
                        "url": {"path": "a", "attribute": "href"},
                        "title": {"path": "a"},
                    },
                },
                "documents": {"path": "a.document", "attribute": "href"},
            })

        def fetcher(self, portal_id):
            return fetch.Fetcher(portal_id, transport=httpx.MockTransport(self.respond))

    async def no_location(url):
        return None

    portal = Portal()
    monkeypatch.setattr(watchtower, "Fetcher", portal.fetcher)
    monkeypatch.setattr(watchtower, "explore", portal.explore)
    monkeypatch.setattr(main, "lookup_registration", no_location)
    monkeypatch.setattr(db, "now", lambda: NOW)
    return portal


@pytest_asyncio.fixture
async def api():
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def expect_json(client, method, path, status, **kwargs):
    response = await client.request(method, path, **kwargs)
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/json"
    return response.json()


def assert_timestamp(value):
    assert datetime.fromisoformat(value).utcoffset() is not None


async def finish_run(portal):
    tasks = tuple(watchtower._background)
    portal.resume.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)


@pytest.mark.asyncio
async def test_portal_attachment_refresh_and_documents(api, fake_portal, monkeypatch):
    created = await expect_json(api, "POST", "/portals", 201, json={"url": PORTAL_URL})
    portal_id = created["id"]
    assert isinstance(portal_id, str) and portal_id
    assert created == {"id": portal_id}
    assert await expect_json(api, "POST", "/portals", 201, json={"url": PORTAL_URL}) == created
    path = f"/portals/{portal_id}"
    entry = await expect_json(api, "GET", path, 200)
    assert entry == {
        "id": portal_id, "url": PORTAL_URL, "state": "integrating",
        "reason": "", "tenderCount": 0, "lastSuccessAt": None,
    }
    assert await expect_json(api, "GET", "/portals", 200) == [entry]
    assert await expect_json(api, "GET", f"{path}/tenders", 200) == []

    running, = await expect_json(api, "GET", f"{path}/runs", 200)
    assert running["kind"] == "read"
    assert running["finishedAt"] is None
    assert running["result"] is None
    assert_timestamp(running["startedAt"])
    assert await expect_json(api, "POST", f"{path}/refresh", 202) == {"runId": running["id"]}

    await finish_run(fake_portal)
    complete, = await expect_json(api, "GET", f"{path}/runs", 200)
    assert complete["id"] == running["id"]
    assert complete["result"] == "ok", complete["reason"]
    assert_timestamp(complete["finishedAt"])
    serving = await expect_json(api, "GET", path, 200)
    assert serving["state"] == "serving"
    assert serving["tenderCount"] == 2
    assert isinstance(serving["reason"], str)
    assert serving["lastSuccessAt"] == complete["finishedAt"]

    tenders = await expect_json(api, "GET", f"{path}/tenders", 200)
    first, second = sorted(tenders, key=lambda tender: tender["id"])
    assert first["id"] == "A1"
    assert first["url"] == f"{PORTAL_URL}tender/A1"
    assert first["title"] == "Road works"
    assert first["buyerName"] is None
    assert first["publicationDate"] is None
    assert first["submissionDeadline"] is None
    assert first["status"] == "open"
    assert OPTIONAL_FIELDS.isdisjoint(first)
    assert_timestamp(first["lastChangedAt"])
    assert second["documents"] == []

    expected_files = []
    for name, data in FILE_BYTES.items():
        digest = sha256(data).hexdigest()
        file_path = f"/files/{digest}"
        expected_files.append({"fileName": name, "sizeBytes": len(data), "sha256": digest, "path": file_path})
        response = await api.get(file_path)
        assert response.status_code == 200
        assert response.content == data
    assert sorted(first["documents"], key=lambda doc: doc["fileName"]) == expected_files

    fake_portal.title = "Road works, amended"
    monkeypatch.setattr(db, "now", lambda: LATER)
    refreshed = await expect_json(api, "POST", f"{path}/refresh", 202)
    assert refreshed.keys() == {"runId"}
    assert isinstance(refreshed["runId"], str)
    assert refreshed["runId"] != complete["id"]
    await finish_run(fake_portal)
    since = datetime.fromisoformat(first["lastChangedAt"]).astimezone(timezone(-timedelta(hours=2)))
    changed, = await expect_json(
        api, "GET", f"{path}/tenders", 200, params={"changedSince": since.isoformat()},
    )
    assert changed["id"] == first["id"]
    assert changed["title"] == "Road works, amended"
    assert changed["lastChangedAt"] == LATER
    assert await expect_json(
        api, "GET", f"{path}/tenders", 200, params={"changedSince": changed["lastChangedAt"]},
    ) == []


@pytest.mark.asyncio
async def test_delete_stops_requests_and_unknown_ids_return_404(api, fake_portal):
    created = await expect_json(api, "POST", "/portals", 201, json={"url": PORTAL_URL})
    path = f"/portals/{created['id']}"
    await asyncio.wait_for(fake_portal.started.wait(), timeout=2)
    before_delete = fake_portal.requests.copy()
    response = await api.delete(path)
    assert response.status_code == 204
    assert response.content == b""
    await finish_run(fake_portal)
    assert fake_portal.requests == before_delete
    assert await expect_json(api, "GET", "/portals", 200) == []

    for method, endpoint in [
        ("GET", path),
        ("DELETE", path),
        ("POST", f"{path}/refresh"),
        ("GET", f"{path}/tenders"),
        ("GET", f"{path}/runs"),
        ("GET", "/files/" + "0" * 64),
    ]:
        await expect_json(api, method, endpoint, 404)


@pytest.mark.asyncio
async def test_healing_keeps_tenders_and_captcha_marks_portal_failed(api, fake_portal, monkeypatch):
    created = await expect_json(api, "POST", "/portals", 201, json={"url": PORTAL_URL})
    path = f"/portals/{created['id']}"
    await finish_run(fake_portal)
    before = await expect_json(api, "GET", f"{path}/tenders", 200)
    assert len(before) == 2

    fake_portal.listing = "/new-notices"
    fake_portal.started.clear()
    fake_portal.resume.clear()
    monkeypatch.setattr(db, "now", lambda: LATER)
    refresh = await expect_json(api, "POST", f"{path}/refresh", 202)
    await asyncio.wait_for(fake_portal.started.wait(), timeout=3)
    entry = await expect_json(api, "GET", path, 200)
    assert entry["state"] == "healing"
    assert await expect_json(api, "GET", f"{path}/tenders", 200) == before

    runs = await expect_json(api, "GET", f"{path}/runs", 200)
    read = next(run for run in runs if run["id"] == refresh["runId"])
    repair = next(run for run in runs if run["kind"] == "repair")
    assert read["kind"] == "read"
    assert read["result"] == "failed"
    assert_timestamp(read["finishedAt"])
    assert repair["result"] is None
    assert repair["finishedAt"] is None
    assert_timestamp(repair["startedAt"])

    fake_portal.blocked = True
    await finish_run(fake_portal)
    blocked = await expect_json(api, "GET", path, 200)
    assert blocked["state"] == "failed"
    assert "captcha" in blocked["reason"].lower()
    runs = await expect_json(api, "GET", f"{path}/runs", 200)
    finished_repair = next(run for run in runs if run["id"] == repair["id"])
    assert finished_repair["result"] == "failed"
    assert_timestamp(finished_repair["finishedAt"])
