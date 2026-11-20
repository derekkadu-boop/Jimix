import json

import pytest
from conftest import BASE, reply

from watchtower.context import ToolError
from watchtower.models import ExtractionMethod, ExtractionRecipeSpec, FieldMapping
from watchtower.observations import bounded_json, initial_fetch
from watchtower.tools import (
    APIInput,
    RecipeInput,
    URLInput,
    inspect_documentation,
    inspect_documents,
    inspect_scripts,
    inspect_tender_details,
)
from watchtower.tools import (
    test_api as probe_api,
)
from watchtower.tools import (
    test_extraction as probe_extraction,
)
from watchtower.tools.documentation import bounded_yaml

API_URL = BASE + "/api/tenders"
API_BODY = {
    "items": [
        {
            "id": "EU-2026-278",
            "title": "Bridge works tender",
            "document": "/contract.pdf",
            "notice": "/notice/EU-2026-278",
        }
    ]
}
HTML = """<html lang="en"><title>Procurement notices</title>
<main><article class="tender" data-id="EU-2026-278">
  <h2>Bridge works tender</h2><a href="/notice/EU-2026-278">Tender details</a>
</article></main></html>"""
DETAIL = """<title>Tender detail</title><h1>Bridge works tender</h1>
<dl><dt>Buyer</dt><dd class="buyer">City works office</dd></dl>
<a href="/contract.pdf">Specifications</a>"""


def html_recipe(**extras):
    return ExtractionRecipeSpec.model_validate(
        {
            "method": "html",
            "listing": {
                "kind": "html",
                "url": BASE + "/",
                "records": "article.tender",
                "fields": {"id": {"path": "$", "attribute": "data-id"}, "title": {"path": "h2"}},
            },
            **extras,
        }
    )


def api_recipe(method="documented_api", **extras):
    return ExtractionRecipeSpec.model_validate(
        {
            "method": method,
            "listing": {
                "kind": "api",
                "url": API_URL,
                "records": "items",
                "fields": {"id": {"path": "id"}, "title": {"path": "title"}, "url": {"path": "notice"}},
            },
            **extras,
        }
    )


def documented_site(site):
    site.routes.update(
        {
            "/": (200, '<a href="/openapi.json">OpenAPI documentation</a>'),
            "/openapi.json": (
                200,
                json.dumps(
                    {
                        "openapi": "3.1.0",
                        "info": {"title": "Test procurement API", "version": "1"},
                        "paths": {
                            "/api/tenders": {
                                "get": {
                                    "parameters": [{"in": "query", "name": "page", "required": False}],
                                    "responses": {
                                        "200": {
                                            "description": "Tender listings",
                                            "content": {"application/json": {"schema": {"type": "object"}}},
                                        }
                                    },
                                }
                            },
                            "/api/private": {
                                "get": {
                                    "security": [{"key": []}],
                                    "responses": {"401": {"description": "Authentication required"}},
                                }
                            },
                        },
                    }
                ),
                "application/json",
            ),
            "/api/tenders": (200, json.dumps(API_BODY), "application/json"),
            "/notice/EU-2026-278": (200, DETAIL),
        }
    )


@pytest.mark.asyncio
async def test_html_documentation_returns_prose_and_records_an_observed_api(context, site):
    site.routes["/"] = (200, '<a href="/docs">API documentation</a>')
    site.routes["/docs"] = (
        200,
        "<html><title>API documentation</title><p>Use page to browse tender notices.</p>"
        "<pre><code>GET /api/tenders</code></pre></html>",
    )
    await initial_fetch(context.url, context)

    docs = await inspect_documentation(URLInput(url=BASE + "/docs"), context)

    assert "Use page to browse tender notices." in docs["text"]
    assert docs["endpoints"] == [{"url": API_URL, "method": "GET"}]
    assert API_URL in context.documented_endpoints


@pytest.mark.asyncio
async def test_documented_api_is_independently_verified(context, site):
    documented_site(site)
    await initial_fetch(context.url, context)
    assert not context.capabilities.public_api_verified
    docs = await inspect_documentation(URLInput(url=BASE + "/openapi.json"), context)
    assert docs["format"] == "OpenAPI"
    assert docs["endpoints"][0]["urls"] == [API_URL]
    assert docs["endpoints"][1]["api_authentication_required"] is True
    assert BASE + "/api/private" not in context.candidates
    hit = await probe_api(APIInput(url=API_URL), context)
    assert hit["verified"] and hit["api_probe"]["sample_count"] == 1
    result = await probe_extraction(RecipeInput(recipe=api_recipe()), context, "test-step")
    assert result["verified"] and context.best[0].method is ExtractionMethod.DOCUMENTED_API
    assert [req[2] for req in site.requests].count("/api/tenders") == 1


@pytest.mark.asyncio
async def test_swagger_yaml_uses_default_host_and_resolves_local_parameters(context, site):
    site.routes["/"] = (200, '<a href="/swagger.yaml">Swagger documentation</a>')
    site.routes["/swagger.yaml"] = (
        200,
        """swagger: "2.0"
info:
  title: Tender API
  version: "1.0"
basePath: /v2
parameters:
  Page:
    name: page
    in: query
    type: integer
paths:
  /tenders:
    get:
      parameters:
        - $ref: "#/parameters/Page"
      responses:
        "200":
          description: Tender listings
""",
        "application/yaml",
    )
    await initial_fetch(context.url, context)

    docs = await inspect_documentation(URLInput(url=BASE + "/swagger.yaml"), context)

    assert docs["format"] == "Swagger"
    assert docs["endpoints"][0]["urls"] == [BASE + "/v2/tenders"]
    assert docs["endpoints"][0]["parameters"] == [{"name": "page", "required": False}]


@pytest.mark.asyncio
async def test_openapi_external_reference_is_not_retrieved(context, site, monkeypatch):
    requests = []

    def read_uri(uri):
        requests.append(uri)
        raise AssertionError("The parser should only read the supplied specification")

    monkeypatch.setattr("openapi_parser.resolver._read_uri", read_uri)
    documented_site(site)
    status, body, content_type = site.routes["/openapi.json"]
    spec = json.loads(body)
    spec["paths"]["/api/tenders"]["get"]["parameters"] = [
        {"$ref": "https://schemas.example.eu/parameters.yaml#/Page"}
    ]
    site.routes["/openapi.json"] = (status, json.dumps(spec), content_type)
    await initial_fetch(context.url, context)

    with pytest.raises(ToolError, match="unresolved reference"):
        await inspect_documentation(URLInput(url=BASE + "/openapi.json"), context)

    assert requests == []
    assert API_URL not in context.documented_endpoints


@pytest.mark.asyncio
async def test_html_pagination_details_and_document_probe(context, site):
    async def document(request):
        assert request.method == "GET"
        assert request.headers["range"] == "bytes=0-63"
        return reply(200, b"%PDF-" + b"0" * 1_000, "application/pdf")

    context.config.max_document_probe_bytes = 64
    site.routes.update(
        {
            "/": (200, HTML + '<a rel="next" href="/?page=2">Next</a>'),
            "/?page=2": (
                200,
                HTML.replace("2026-278", "2026-279").replace("Bridge works", "Library renovation"),
            ),
            "/notice/EU-2026-278": (200, DETAIL),
            "/contract.pdf": document,
        }
    )
    await initial_fetch(context.url, context)
    detail = await inspect_tender_details(URLInput(url=BASE + "/notice/EU-2026-278"), context)
    assert "Buyer" in detail["fields"]
    packet = await inspect_documents(URLInput(url=BASE + "/contract.pdf"), context)
    assert packet["public_download_verified"] and packet["bytes_read"] == 64 and packet["truncated"]
    recipe = html_recipe(
        pagination={"kind": "next", "path": "a[rel=next]", "attribute": "href"},
        detail={
            "url_field": "url",
            "fields": {
                "buyer": {"path": "dd.buyer"},
                "attachment": {"path": "a", "attribute": "href"},
            },
        },
        document_field="attachment",
    )
    recipe.listing.fields["url"] = FieldMapping(path="a", attribute="href")
    checked = ExtractionRecipeSpec.model_validate(recipe.model_dump())
    result = await probe_extraction(RecipeInput(recipe=checked), context, "html-step")
    assert result["verified"] and result["pagination_verified"] and result["detail_verified"]
    assert result["document_verified"]
    assert [req[2] for req in site.requests].count("/contract.pdf") == 1


@pytest.mark.asyncio
async def test_hybrid_api_and_html(context, site):
    documented_site(site)
    await initial_fetch(context.url, context)
    await inspect_documentation(URLInput(url=BASE + "/openapi.json"), context)
    recipe = api_recipe(
        "hybrid", detail={"url_field": "url", "kind": "html", "fields": {"buyer": {"path": ".buyer"}}}
    )
    result = await probe_extraction(RecipeInput(recipe=recipe), context, "hybrid-step")
    assert result["verified"] and result["detail_verified"]


@pytest.mark.asyncio
async def test_empty_listing_is_unverified(context, site):
    site.routes["/"] = 200, "<title>Public tenders</title><main>No open tender notices</main>"
    await initial_fetch(context.url, context)
    result = await probe_extraction(RecipeInput(recipe=html_recipe()), context, "empty-step")
    assert not result["verified"] and result["sample_count"] == 0
    assert "No live tender records" in result["reason"]


@pytest.mark.asyncio
async def test_script_inspection_surfaces_shapes_without_secrets(context, site):
    site.routes["/"] = (
        200,
        '<script type="application/json" id="__NEXT_DATA__">'
        '{"token":"private-token-value","tenders":[{"id":"EU-1"}]}</script>',
    )
    await initial_fetch(context.url, context)
    output = await inspect_scripts(URLInput(url=context.url), context)
    assert output["embedded_json"][0]["sample"] == {"tenders": [{"id": "str"}]}
    assert "private-token-value" not in str(output)


def test_structured_payload_guards():
    with pytest.raises(ToolError, match="depth"):
        bounded_json(b'[[[["nested"]]]]', 2)
    with pytest.raises(ToolError, match="Malformed JSON"):
        bounded_json(b'{"bad":1e500}', 10)
    with pytest.raises(ToolError, match="aliases"):
        bounded_yaml(b"source: &ref [one]\ncopy: *ref", 10)
    with pytest.raises(ToolError, match="Malformed"):
        bounded_yaml(b"a: [broken", 10)
