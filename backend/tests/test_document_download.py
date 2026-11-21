import asyncio

import httpx
import pytest

from app import fetch
from app.fetch import Fetcher


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(fetch, "INTERVAL", 0)
    monkeypatch.setattr(fetch, "RETRY_DELAYS", (0, 0))


@pytest.mark.parametrize(("path", "media", "disposition", "accepted"), [
    ("/notices/123", "application/json; charset=utf-8", "", False),
    ("/notices/123", "application/vnd.api+json", "inline", False),
    ("/files/boundary.geojson", "application/geo+json", "", True),
    ("/files/list.JSON", "application/json", "", True),
    ("/files/redirected.json", "application/json", "", True),
    ("/download/123", "application/json", 'attachment; filename="requirements.json"', True),
    ("/download/123", "application/json", "attachment", True),
    ("/download/123", "application/json", "inline; filename*=UTF-8''requirements.json", True),
])
def test_inline_json_endpoints_and_published_json_files(tmp_path, path, media, disposition, accepted):
    def response(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/files/redirected.json":
            return httpx.Response(302, headers={"location": "/objects/42"})
        return httpx.Response(200, headers={"content-type": media, "content-disposition": disposition},
                              content=b'{"example":true}')

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            if accepted:
                part, _ = await fetcher.download("https://portal.example" + path, tmp_path)
                assert part.read_bytes() == b'{"example":true}'
            else:
                with pytest.raises(fetch.DocumentSkipped, match=fetch.JSON_METADATA_SKIP):
                    await fetcher.download("https://portal.example" + path, tmp_path)
                assert fetcher.downloaded == 0 and not list(tmp_path.iterdir())
        finally:
            await fetcher.close()

    asyncio.run(run())


def test_download_samples_html_only_to_probe_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "DOCUMENT_HTML_PROBE_BYTES", 8)

    class Html(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"<html>"
            yield b"content"
            pytest.fail("HTML probe consumed the rest of the body")

    def response(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=Html())

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            with pytest.raises(fetch.DocumentSkipped, match="HTML response"):
                await fetcher.download("https://portal.example/file", tmp_path)
            assert fetcher.downloaded == len(b"<html>content")
            assert not list(tmp_path.glob(".part-*"))
        finally:
            await fetcher.close()

    asyncio.run(run())


@pytest.mark.parametrize("error", [httpx.ReadError("interrupted"), asyncio.CancelledError()])
def test_download_cleans_partial_files_on_interruption(tmp_path, error):
    class Interrupted(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"%PDF-1.4 fixture"
            assert list(tmp_path.glob(".part-*"))
            raise error

    def response(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, stream=Interrupted())

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(response))
        try:
            with pytest.raises(type(error)):
                await fetcher.download("https://portal.example/file", tmp_path)
            assert fetcher.downloaded == len(b"%PDF-1.4 fixture")
            assert not list(tmp_path.glob(".part-*"))
        finally:
            await fetcher.close()

    asyncio.run(run())
