from types import SimpleNamespace

import pytest

from app import asset_cache


@pytest.mark.parametrize("headers", [
    {"cache-control": "max-age=60, no-store"},
    {"cache-control": "max-age=60, no-cache"},
    {"cache-control": "private, max-age=60"},
    {"cache-control": "max-age=60", "set-cookie": "x=1"},
    {"cache-control": "max-age=60", "vary": "Cookie"},
    {"cache-control": "max-age=60", "content-encoding": "gzip"},
    {"cache-control": "max-age=60", "age": "60"},
    {"cache-control": "max-age=60, s-maxage=0"},
    {"cache-control": "max-age=60, max-age=10"},
    {"cache-control": 'community="shared, max-age=60"'},
    {},
])
def test_public_asset_cache_ignores_unshareable_responses(headers):
    cache = asset_cache.AssetCache()
    cache.save("https://portal.example/static.js", "script",
               {"content-type": "application/javascript"} | headers, b"script")
    assert cache.get("https://portal.example/static.js", "script") is None


@pytest.mark.parametrize("field", ["if-match", "if-unmodified-since", "if-range"])
def test_conditional_requests_bypass_the_shared_cache(field):
    assert not asset_cache.shareable_request("https://portal.example/static.js", {field: "validator"})


@pytest.mark.parametrize("field", ["origin", "access-control-request-method", "access-control-request-headers"])
def test_cors_vary_only_reuses_the_absent_header_variant(field):
    cache = asset_cache.AssetCache()
    url = "https://portal.example/static.js"
    headers = {"content-type": "application/javascript", "cache-control": "max-age=60", "vary": field}
    alternate = {field: "example"}
    cache.save(url, "script", headers, b"default", {})
    cache.save(url, "script", headers, b"variant", alternate)
    assert cache.get(url, "script", alternate) is None
    assert cache.get(url, "script", {}).body == b"default"


def test_asset_cache_expires_and_evicts_old_entries(monkeypatch):
    now = 10
    monkeypatch.setattr(asset_cache, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(asset_cache, "MAX_CACHE_BYTES", 6)
    cache = asset_cache.AssetCache()
    a, b = "https://portal.example/a.js", "https://portal.example/b.js"
    headers = {"content-type": "application/javascript", "cache-control": "max-age=3", "age": "1"}
    cache.save(a, "script", headers, b"a.js")
    cache.save(b, "script", headers, b"b.js")
    assert cache.get(a, "script") is None
    now += 1
    assert cache.get(b, "script").response_headers()["age"] == "2"
    now += 1
    assert cache.get(b, "script") is None
    assert cache.bytes == 0
