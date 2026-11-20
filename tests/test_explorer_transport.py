import asyncio
import gzip
from unittest.mock import AsyncMock

import pytest
from conftest import BASE, reply

from watchtower.context import StopExploration
from watchtower.models import StopReason
from watchtower.observations import initial_fetch
from watchtower.robots import parse_robots
from watchtower.urls import UnsafeURL, normalize_url, resolve_public


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (
            200,
            "<title>CAPTCHA</title><form><p>Verify you are human</p><div class='g-recaptcha'></div></form>",
            StopReason.CAPTCHA,
        ),
        (503, "<title>Just a moment...</title><form id='challenge-form'></form>", StopReason.BOT_PROTECTION),
        (401, "Login required", StopReason.AUTHENTICATION_REQUIRED),
        (403, "Forbidden", StopReason.ACCESS_BLOCKED),
        (200, '<meta charset="utf-8"><title>Accès refusé</title>', StopReason.ACCESS_BLOCKED),
        (200, "<title>Log in</title><form><input type=password></form>", StopReason.AUTHENTICATION_REQUIRED),
    ],
)
async def test_initial_access_is_detected(context, site, status, body, reason):
    site.routes["/"] = status, body
    observation = await initial_fetch(context.url, context)
    assert observation.access_restriction.restriction_type == reason
    assert context.stop == reason
    assert context.usage.http_requests == 2


@pytest.mark.asyncio
async def test_redirect_to_login_stops_before_following(context, site):
    async def redirect(_):
        return reply(302, Location="https://accounts.example.eu/auth/login?token=secret")

    site.routes["/"] = redirect
    result = await initial_fetch(context.url, context)
    assert result.access_restriction.restriction_type == StopReason.AUTHENTICATION_REQUIRED
    assert len(site.requests) == 2
    progress = await context.store.get_exploration_progress(context.run_id)
    assert "secret" not in str(progress)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("robots", "reason"),
    [
        ((200, "User-agent: *\nDisallow: /\n", "text/plain"), StopReason.ROBOTS_DISALLOWED),
        ((503, "temporarily unavailable", "text/plain"), StopReason.ROBOTS_UNAVAILABLE),
        ((200, "<html>WAF error</html>"), StopReason.ROBOTS_UNAVAILABLE),
        ((200, "Service temporarily unavailable", "text/plain"), StopReason.ROBOTS_UNAVAILABLE),
    ],
)
async def test_disallowed_unavailable_or_malformed_robots(context, site, robots, reason):
    site.routes["/robots.txt"] = robots
    with pytest.raises(StopExploration) as exc:
        await initial_fetch(context.url, context)
    assert exc.value.reason == reason
    assert [request[2] for request in site.requests] == ["/robots.txt"]


@pytest.mark.asyncio
async def test_absent_robots_and_budgeted_redirect(context, site):
    async def redirect(_):
        return reply(301, Location="/notices")

    site.routes["/"] = redirect
    site.routes["/notices"] = (
        200,
        "<html lang='pl'><title>Public Notices</title>"
        "<header><a href='/docs'>Dokumentacja</a></header></html>",
    )
    result = await initial_fetch(context.url, context)
    assert result.robots.status == "absent"
    assert result.status == 200 and result.final_url == BASE + "/notices"
    assert result.page["documentation"][0]["url"] == BASE + "/docs"
    assert result.page["language"] == "pl"
    assert context.usage.http_requests == 3
    await context.transport.fetch(BASE + "/notices")
    assert context.usage.http_requests == 3
    assert context.usage.cache_hits >= 2


@pytest.mark.asyncio
async def test_french_navigation_labels_are_recognized(context, site):
    site.routes["/"] = (
        200,
        '<meta charset="utf-8"><a href="/catalogue">Données ouvertes</a>'
        '<a href="/entry">Appel d\'offres</a><a href="/?page=2">Suivant</a>',
    )
    observation = await initial_fetch(context.url, context)
    assert observation.page["documentation"][0]["url"] == BASE + "/catalogue"
    assert observation.page["pagination"][0]["url"] == BASE + "/?page=2"
    assert "detail" in context.candidates[BASE + "/entry"]


def test_robots_wildcards_precedence_and_delay():
    body = (
        b"User-agent: *\nDisallow: /\n"
        b"User-agent: WatchtowerExplorer\nDisallow: /*.pdf$\nAllow: /safe.pdf$\nCrawl-delay: 1.5\n"
        b"User-agent: WatchtowerExplorer\nDisallow: /private\n"
    )
    policy = parse_robots(BASE + "/robots.txt", body, False)
    assert policy.evaluate(BASE + "/safe.pdf", "WatchtowerExplorer/0.1", False).allowed
    assert not policy.evaluate(BASE + "/file.pdf", "WatchtowerExplorer/0.1", False).allowed
    assert not policy.evaluate(BASE + "/private?id=1", "WatchtowerExplorer/0.1", False).allowed
    assert policy.evaluate(BASE + "/safe.pdf", "WatchtowerExplorer/0.1", False).crawl_delay_seconds == 1.5


def test_robots_request_rate_can_require_more_spacing_than_crawl_delay():
    body = b"User-agent: *\nCrawl-delay: 1.5\nRequest-rate: 2/9\nSitemap: /sitemap.xml"
    decision = parse_robots(BASE + "/robots.txt", body, False).evaluate(
        BASE + "/", "WatchtowerExplorer", False
    )
    assert decision.allowed and decision.crawl_delay_seconds == 4.5
    assert decision.sitemaps == [BASE + "/sitemap.xml"]


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["0", "3600"])
async def test_rate_limit_is_honored(context, site, retry_after):
    async def limited(_):
        if len([r for r in site.requests if r[2] == "/"]) == 1:
            return reply(429, Retry_After="wrong", **{"Retry-After": retry_after})
        return reply(200, "<title>Notices</title>")

    site.routes["/"] = limited
    result = await initial_fetch(context.url, context)
    assert context.capabilities.rate_limiting_observed is True
    if retry_after == "0":
        assert result.status == 200 and len(site.requests) == 3
    else:
        assert result.status == 429 and context.stop == StopReason.RATE_LIMITED
        assert result.access_restriction.retry_after_seconds == 3600
        assert len(site.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "http://169.254.169.254/latest",
        "https://127.0.0.1/",
        "https://example.eu:8443/",
        "https://foo.example.eu/",
    ],
)
async def test_unsafe_redirect_and_private_network_are_blocked(context, site, target):
    async def redirect(_):
        return reply(302, Location=target)

    site.routes["/"] = redirect
    with pytest.raises(UnsafeURL):
        await initial_fetch(context.url, context)
    assert len(site.requests) == 2


@pytest.mark.asyncio
async def test_dns_with_any_private_answer_is_blocked(monkeypatch):
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(
            return_value=[
                (2, 1, 6, "", ("93.184.216.34", 443)),
                (2, 1, 6, "", ("127.0.0.1", 443)),
            ]
        ),
    )
    with pytest.raises(UnsafeURL):
        await resolve_public("rebind.example.eu", 443)
    with pytest.raises(UnsafeURL):
        normalize_url("https://[::ffff:127.0.0.1]")


@pytest.mark.asyncio
async def test_compressed_response_has_bounded_decoded_size(context, site):
    context.config.max_response_bytes = 512

    async def compressed(_):
        return reply(200, gzip.compress(b"x" * 1_000_000), kind="text/plain", **{"Content-Encoding": "gzip"})

    site.routes["/"] = compressed
    result = await context.transport.fetch(BASE + "/")
    assert result.truncated and len(result.body) == 512
