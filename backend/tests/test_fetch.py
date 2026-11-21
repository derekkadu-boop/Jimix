import httpx
import pytest

from app.fetch import _file_name


@pytest.mark.parametrize(
    ("disposition", "url", "expected"),
    [
        ('attachment; filename="Hinweis%20E-Vergabe.pdf"; filename*=UTF-8\'\'Hinweis%20E-Vergabe.pdf',
         "https://p.example/d/x", "Hinweis E-Vergabe.pdf"),
        ('attachment; filename="a.pdf"; filename*=UTF-8\'\'Zus%C3%A4tzlich.pdf', "https://p.example/d/x",
         "Zusätzlich.pdf"),
        ("attachment; filename*=iso-8859-1'de'Gr%FC%DFe.pdf", "https://p.example/d/x", "Grüße.pdf"),
        ('attachment; filename="plain name.pdf"', "https://p.example/d/x", "plain name.pdf"),
        ("", "https://p.example/d/LV%20Komplett.pdf", "LV Komplett.pdf"),
        ("", "https://p.example/", "document"),
    ],
)
def test_file_name_prefers_extended_parameter_and_decodes(disposition, url, expected):
    resp = httpx.Response(200, headers={"content-disposition": disposition}, request=httpx.Request("GET", url))
    assert _file_name(resp) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("refresh", "robots", "expected", "blocked"),
    [
        ("0; URL='/listing?open=1'", "", ["/start", "/listing?open=1"], None),
        ("0; url=/listing", "Disallow: /listing", ["/start"], "robots.txt disallows"),
        ("0; url=/login", "", ["/start"], "login required"),
        ("0; url=/start#again", "", ["/start"], None),
        ("0; url=file:///etc/passwd", "", ["/start"], None),
        ("0; url=https://user:pass@procurement.example/listing", "", ["/start"], None),
        ("900; url=/listing", "", ["/start"], None),
    ],
)
async def test_get_handles_html_refresh(monkeypatch, refresh, robots, expected, blocked):
    from app import fetch

    monkeypatch.setattr(fetch, "INTERVAL", 0)
    seen = []

    def respond(request):
        path = request.url.raw_path.decode()
        if path == "/robots.txt":
            return httpx.Response(200, text=f"User-agent: *\n{robots}")
        seen.append(path)
        if path == "/start":
            return httpx.Response(200, headers={"content-type": "TEXT/HTML"},
                                  text=f'<meta http-equiv="Refresh" content="{refresh}">')
        return httpx.Response(200, json={"results": []})

    fetcher = fetch.Fetcher(transport=httpx.MockTransport(respond))
    try:
        if blocked:
            with pytest.raises(fetch.Blocked, match=blocked):
                await fetcher.get("https://procurement.example/start")
        else:
            response = await fetcher.get("https://procurement.example/start")
            assert response.url.raw_path.decode() == expected[-1]
        assert seen == expected
    finally:
        await fetcher.close()


@pytest.mark.asyncio
async def test_get_limits_html_refresh_chain(monkeypatch):
    from app import fetch

    monkeypatch.setattr(fetch, "INTERVAL", 0)
    seen = []

    def respond(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        number = int(request.url.path[1:])
        seen.append(number)
        return httpx.Response(200, html=f'<meta http-equiv="refresh" content="0;url=/{number+1}">')

    fetcher = fetch.Fetcher(transport=httpx.MockTransport(respond))
    try:
        response = await fetcher.get("https://procurement.example/0")
        assert seen == list(range(fetch.MAX_META_REFRESH_HOPS+1))
        assert response.url.path == f"/{fetch.MAX_META_REFRESH_HOPS}"
    finally:
        await fetcher.close()


@pytest.mark.parametrize("parent", ["template", "noscript"])
def test_html_refresh_ignores_inactive_metadata(parent):
    from app.fetch import _html_refresh

    html = f'<{parent}><meta http-equiv="refresh" content="0;url=/other"></{parent}>'
    assert _html_refresh(html, "https://procurement.example/") is None
