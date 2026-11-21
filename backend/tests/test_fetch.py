import asyncio
import socket
import time

import httpx
import pytest

from app import fetch
from app.fetch import Fetcher, _file_name, block_reason, parse_robots, robots_allows


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


APP_SHELL = """<html><body><div id="app"></div><script>window.CONFIG = {RECAPTCHA_PUBLIC_KEY: 'x'};
var t = {"You entered a wrong captcha.": "", tpl: '<input type="password">'};</script></body></html>"""


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (APP_SHELL, None),
        ('<div class="g-recaptcha"></div>', "captcha (div g-recaptcha)"),
        ('<iframe src="https://challenges.example/turnstile/v0"></iframe>', "captcha (iframe)"),
        ("<p>Please solve the CAPTCHA to continue</p>", "captcha (page text)"),
        ('<form><input name="user"><input type="password"></form>', "login required (login form)"),
        (('<div class="grecaptcha-badge"><iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe></div>'
          "<p>Tender list</p>"), None),
        ('<div class="g-recaptcha" data-size="invisible"></div><p>Tenders</p>', None),
        ("<p>Tenders</p><footer>This site is protected by reCAPTCHA and the Google Privacy Policy.</footer>", None),
        ('<div class="h-captcha" data-sitekey="k"></div>', "captcha (div h-captcha)"),
        ('<div id="cf-challenge-running"></div>', "captcha (div cf-challenge-running)"),
        ('<script src="https://ct.captcha-delivery.com/c.js"></script>', "captcha (script)"),
        ("<p>Please verify you are human</p>", "captcha (page text)"),
        (None, None),
    ],
)
def test_block_reason_needs_a_real_captcha_or_login_form(html, expected):
    assert block_reason(200, {}, "https://p.example/a", "https://p.example/a", html) == expected


def test_aws_waf_challenge_header_is_a_block():
    headers = httpx.Headers({"x-amzn-waf-action": "captcha"})
    assert block_reason(405, headers, "https://p.example/a", "https://p.example/a", None) == (
        "bot challenge (AWS WAF captcha)")


ROBOTS = """User-agent: Otherbot
Disallow:

User-agent: *
Disallow: /
Allow: /public
Disallow: /public/private
Disallow: /*/download
Disallow: /*/*.pdf$
"""


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/", False),
        ("/search", False),
        ("/public/list?page=2", True),
        ("/public/private/x", False),
        ("/public/a/download", False),
        ("/public/a.pdf", False),
        ("/public/a.pdf?v=1", True),
    ],
)
def test_robots_longest_match_and_wildcards(path, allowed):
    assert robots_allows(parse_robots(ROBOTS), f"https://p.example{path}") is allowed


def test_robots_own_group_wins_and_ties_allow():
    rules = parse_robots("User-agent: *\nDisallow: /\n\nUser-agent: JimixWatchtower\nDisallow: /x\nAllow: /x\n")
    assert robots_allows(rules, "https://p.example/anything")
    assert robots_allows(rules, "https://p.example/x")
    assert not robots_allows(parse_robots("User-agent: *\nDisallow: /\n"), "https://p.example/a")


def test_robots_parsing_edge_cases():
    assert not robots_allows(parse_robots("﻿User-agent: *\r\nDisallow: /a\r\n"), "https://p.example/a")
    loose = "User-agent: watch\nUser-agent:\nDisallow: /\n\nUser-agent: *\nDisallow: /b\n"
    assert robots_allows(parse_robots(loose), "https://p.example/a")
    grouped = "User-agent: Other\nSitemap: https://p.example/s.xml\nUser-agent: *\nDisallow: /c\n"
    assert not robots_allows(parse_robots(grouped), "https://p.example/c")
    assert not robots_allows(parse_robots("User-agent: *\nDisallow: /q?a%20b\n"), "https://p.example/q?a%20b")
    assert not robots_allows(parse_robots("User-agent: *\nDisallow: /%7euser\n"), "https://p.example/~user/x")
    assert robots_allows(parse_robots("User-agent: *\nDisallow: /a%2Ab\n"), "https://p.example/axxb")


def test_robots_wildcards_stay_linear():
    rules = parse_robots("User-agent: *\nDisallow: /" + "*a" * 30 + "b\n")
    assert robots_allows(rules, "https://p.example/" + "a" * 5000)


@pytest.mark.parametrize(
    ("failures", "ok"),
    [
        ([httpx.ReadTimeout("slow")], True),
        ([socket.gaierror(-2, "Name or service not known"), 503], True),
        ([502, 504], True),
        ([429], True),
        ([httpx.ConnectError("reset")] * 3, False),
        ([503] * 3, False),
    ],
)
def test_get_retries_transient_errors(monkeypatch, failures, ok):
    monkeypatch.setattr(fetch, "INTERVAL", 0)
    monkeypatch.setattr(fetch, "RETRY_DELAYS", (0, 0))
    attempts = []

    def site(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts.append(request.url.path)
        if len(attempts) <= len(failures):
            failure = failures[len(attempts) - 1]
            if isinstance(failure, int):
                return httpx.Response(failure, headers={"retry-after": "0"})
            raise failure
        return httpx.Response(200, html="<p>list</p>")

    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(site))
        try:
            return await fetcher.get("https://p.example/list")
        finally:
            await fetcher.close()

    if ok:
        assert asyncio.run(run()).text == "<p>list</p>"
    else:
        with pytest.raises((httpx.TransportError, httpx.HTTPStatusError)):
            asyncio.run(run())
    assert len(attempts) == min(len(failures) + 1, 3)


@pytest.mark.parametrize(
    ("header", "expected"),
    [("7", 7), ("600", 60), ("", 15), ("²", 15), ("Fri, 21 Nov 2026 05:00:00 GMT", 15)],
)
def test_retry_after_is_bounded(header, expected):
    resp = httpx.Response(429, headers=[(b"retry-after", header.encode("latin-1"))] if header else [])
    assert fetch.retry_after(resp, 2.0) == expected


def test_pause_holds_back_the_next_request_to_that_site():
    fetch.pause("https://slow.example/list", 5)
    assert fetch._gates["slow.example"].last > time.monotonic() + 4


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
