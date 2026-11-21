import asyncio
import hashlib
import json

import httpx
import pytest

from app import agent, db, documents, fetch, watchtower
from app.recipe import Recipe, apply

HOME = "https://portal.example/"
FEED = HOME + "procurement/feeds/feed.atom"
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom" xml:base="/public/">
<link rel="next" href="older.atom"/><entry xml:base="bids/">
<id>100</id><title>Bridge repairs</title><link rel="alternate" href="100"/>
<link rel="enclosure" xml:base="../files/" href="spec.pdf"/></entry></feed>"""
RECIPE = {
    "method": "documented_api",
    "listing": {"kind": "xml", "url": FEED, "records": "entry",
                "fields": {"id": {"path": "id"}, "title": {"path": "title"},
                           "url": {"path": "link[@rel='alternate']", "attribute": "href"}},
                "next_page": {"path": "link[@rel='next']", "attribute": "href"}},
    "documents": {"path": "link[@rel='enclosure']", "attribute": "href"},
}


def test_advertised_atom_feed_validates_and_delivers_files_on_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "INTERVAL", 0)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "store.db")
    monkeypatch.setattr(documents, "FILES_DIR", tmp_path / "files")
    db.init()
    with db.connect() as conn:
        conn.execute("INSERT INTO portal(id,url,created_at) VALUES('p',?,?)", (HOME, db.now()))
    file_reads = []
    payload = b"%PDF-1.4 bridge specifications"

    def response(request):
        path = request.url.path
        if path == "/":
            return httpx.Response(200, html="""<head><base href="/procurement/feeds/">
                <link rel="alternate" type="application/atom+xml" href="feed.atom">
                <link rel="alternate" type="application/rss+xml" href="/private?token=x">
                </head><body>Tenders</body>""")
        if path in ("/procurement/feeds/feed.atom", "/public/older.atom"):
            content = ATOM if path == "/procurement/feeds/feed.atom" else ATOM.replace(
                '<link rel="next" href="older.atom"/>', ""
            ).replace("100", "101")
            return httpx.Response(200, text=content, headers={"content-type": "application/octet-stream"})
        if path == "/public/files/spec.pdf":
            file_reads.append(path)
            return httpx.Response(200, content=payload, headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    async def run():
        fetcher = fetch.Fetcher(transport=httpx.MockTransport(response))
        try:
            page, _ = await agent._call("fetch_page", {"url": HOME}, fetcher)
            assert FEED in page and "/private" not in page
            page, _ = await agent._call("fetch_page", {"url": FEED}, fetcher)
            assert 'xml:base="bids/"' in page
            first_page = RECIPE | {"listing": RECIPE["listing"] | {"next_page": None}}
            preview, _ = await agent._call("test_recipe", {"recipe": first_page}, fetcher)
            assert any("next-page" in warning for warning in json.loads(preview)["warnings"])
            result, recipe = await agent._call("submit_recipe", {"recipe": RECIPE}, fetcher)
            assert json.loads(result)["problem"] is None and recipe is not None
            await watchtower._deliver("p", recipe, fetcher)
            count = len(file_reads)
            await watchtower._deliver("p", recipe, fetcher)
            assert len(file_reads) == count
        finally:
            await fetcher.close()

    asyncio.run(run())
    with db.connect() as conn:
        tenders = [json.loads(row[0]) for row in conn.execute("SELECT data FROM tender ORDER BY id")]
    assert [t["id"] for t in tenders] == ["100", "101"]
    assert [t["url"] for t in tenders] == [HOME + "public/bids/100", HOME + "public/bids/101"]
    for tender in tenders:
        assert tender["documents"][0]["sha256"] == hashlib.sha256(payload).hexdigest()
        assert (documents.FILES_DIR / tender["documents"][0]["sha256"]).read_bytes() == payload


def test_rss_listing_maps_elements_attributes_and_namespaced_fields(monkeypatch):
    monkeypatch.setattr(fetch, "INTERVAL", 0)
    body = """<rss xmlns:dc="urn:example:metadata"><channel><item><guid>contract-8</guid>
    <title>Road maintenance</title><link>/tenders/8</link><dc:creator>City</dc:creator>
    <enclosure url="/documents/8.pdf"/></item></channel></rss>"""
    recipe = Recipe.model_validate({
        "method": "discovered_api",
        "listing": {"kind": "xml", "url": FEED, "records": "channel/item",
                    "fields": {"id": {"path": "guid"}, "url": {"path": "link"}, "title": {"path": "title"},
                               "buyerName": {"path": "{urn:example:metadata}creator"}}},
        "documents": {"path": "enclosure", "attribute": "url"},
    })

    def response(request):
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200, text=body)

    async def run():
        fetcher = fetch.Fetcher(transport=httpx.MockTransport(response))
        try:
            return await apply(recipe, fetcher)
        finally:
            await fetcher.close()

    tenders, complete = asyncio.run(run())
    assert complete and len(tenders) == 1
    assert tenders[0]["buyerName"] == "City" and tenders[0]["url"] == HOME + "tenders/8"
    assert tenders[0]["_documentUrls"] == [HOME + "documents/8.pdf"]


@pytest.mark.parametrize("xml,error", [
    (b"<feed>", "invalid XML"), (b"<!DOCTYPE feed><feed/>", "document type"),
])
def test_invalid_or_unsupported_xml_reports_a_recipe_error(xml, error):
    from app.xml_listing import parse_feed

    with pytest.raises(ValueError, match=error):
        parse_feed(xml, FEED)
