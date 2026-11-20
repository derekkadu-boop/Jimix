import json

import pytest
from conftest import BASE
from test_explorer_agent import Model, final_action, run_with
from test_explorer_tools import API_BODY, API_URL, api_recipe, html_recipe

from watchtower import ExtractionMethod, StopReason
from watchtower.browser import observe_browser
from watchtower.observations import initial_fetch

APP = """<html><head><title>Procurement notices</title></head>
<body><main id="app"></main><script src="/app.js"></script></body></html>"""

FETCH_SCRIPT = """fetch('/api/tenders').then(response => response.json()).then(body => {
  const list = document.querySelector('#app');
  for (const item of body.items) {
    const row = document.createElement('article');
    row.className = 'tender';
    row.dataset.id = item.id;
    const title = document.createElement('h2');
    title.textContent = item.title;
    row.append(title);
    list.append(row);
  }
});"""


def browser_site(site, script=FETCH_SCRIPT):
    site.routes.update(
        {
            "/": (200, APP),
            "/app.js": (200, script, "application/javascript"),
            "/api/tenders": (200, json.dumps(API_BODY), "application/json"),
        }
    )


@pytest.mark.asyncio
async def test_undocumented_api_discovered_from_actual_browser_traffic(store, site, config):
    browser_site(site)
    recipe = api_recipe("discovered_api")

    async def after_browser(request):
        evidence = json.loads(request["input"][-1]["output"])
        assert len(evidence["endpoints"]) == 1, evidence
        assert evidence["endpoints"][0]["url"] == API_URL
        assert evidence["endpoints"][0]["sample"]["items"][0]["id"] == "EU-2026-278"
        return "test_extraction", {"recipe": recipe.model_dump(mode="json")}

    llm = Model(("inspect_network", {"url": BASE}), after_browser, final_action(recipe))
    result = await run_with(store, site, llm).explore(BASE, config)
    assert result.stop_reason is StopReason.VERIFIED, result.model_dump()
    assert result.best_candidate_method is ExtractionMethod.DISCOVERED_API
    assert result.usage.browser_navigations == 1 and result.usage.http_requests == 6
    agents = {row[3] for row in site.requests if row[2] == "/api/tenders"}
    assert len(agents) == 2 and any("HeadlessChrome/" in agent for agent in agents)
    assert result.classification.public_api_observed and result.classification.public_api_verified


@pytest.mark.asyncio
async def test_js_rendered_portal_validates_browser_recipe(store, site, config):
    browser_site(
        site,
        """
       document.querySelector('#app').innerHTML =
          '<article class="tender" data-id="EU-2026-278"><h2>Bridge works tender</h2></article>';
    """,
    )
    spec = html_recipe().model_dump(mode="json")
    spec["method"], spec["listing"]["kind"] = "browser", "browser"
    from watchtower.models import ExtractionRecipeSpec

    recipe = ExtractionRecipeSpec.model_validate(spec)
    llm = Model(
        ("inspect_network", {"url": BASE}), ("test_extraction", {"recipe": spec}), final_action(recipe)
    )
    result = await run_with(store, site, llm).explore(BASE, config)
    assert result.stop_reason is StopReason.VERIFIED, result.model_dump()
    assert result.classification.javascript_rendering_required is True
    assert result.classification.server_rendered_tender_listings is False
    assert result.usage.browser_navigations == 1


@pytest.mark.asyncio
async def test_browser_requests_use_shared_http_budget(store, site, config):
    browser_site(site)
    cfg = config.model_copy(update={"max_http_requests": 4})
    llm = Model(("inspect_network", {"url": BASE}))
    result = await run_with(store, site, llm).explore(BASE, cfg)
    assert result.stop_reason is StopReason.HTTP_LIMIT, result.model_dump()
    assert result.usage.http_requests == result.configuration.max_http_requests == 4
    assert result.usage.browser_navigations == 1
    assert [row[2] for row in site.requests] == ["/robots.txt", "/", "/", "/app.js"]


@pytest.mark.asyncio
async def test_browser_denies_private_cross_scope_and_non_get_requests(context, site):
    browser_site(
        site,
        FETCH_SCRIPT
        + """
      fetch('http://127.0.0.1/private').catch(() => {});
      fetch('https://unobserved.example.eu/tenders').catch(() => {});
      fetch('/write', {method: 'POST', body: 'deny'}).catch(() => {});
      new WebSocket('wss://unobserved.example.eu/socket');
    """,
    )
    context.browser = observe_browser
    await initial_fetch(context.url, context)
    result = await observe_browser(context.url, context)
    assert result.denied >= 2
    assert all(
        row[0] == "GET" and row[2] in {"/", "/robots.txt", "/api/tenders", "/app.js"} for row in site.requests
    )
    assert context.usage.http_requests == len(site.requests)


@pytest.mark.asyncio
async def test_optional_browser_budget_does_not_prevent_html_verification(store, site, config):
    from test_explorer_tools import HTML

    site.routes["/"] = (200, HTML)
    recipe = html_recipe()
    llm = Model(
        ("inspect_network", {"url": BASE}),
        ("test_extraction", {"recipe": recipe.model_dump(mode="json")}),
        final_action(recipe),
    )
    cfg = config.model_copy(update={"max_browser_navigations": 0})
    result = await run_with(store, site, llm).explore(BASE, cfg)
    assert result.stop_reason is StopReason.VERIFIED and result.usage.browser_navigations == 0


@pytest.mark.asyncio
async def test_browser_limit_stops_when_only_js_shell_remains(store, site, config):
    browser_site(site)
    llm = Model(("inspect_scripts", {"url": BASE}), ("inspect_network", {"url": BASE}))
    cfg = config.model_copy(update={"max_browser_navigations": 0})
    result = await run_with(store, site, llm).explore(BASE, cfg)
    assert result.stop_reason is StopReason.BROWSER_LIMIT
