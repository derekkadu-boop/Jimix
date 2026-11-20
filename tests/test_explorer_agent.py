import asyncio
import json
from types import SimpleNamespace

import aiosqlite
import pytest
from conftest import BASE, reply
from test_explorer_tools import API_URL, HTML, api_recipe, documented_site, html_recipe

from explore import parser, run
from watchtower import ExtractionMethod, StopReason
from watchtower.service import Explorer, _active


class Model:
    def __init__(self, *actions, tokens=(100, 50)):
        self.actions = list(actions)
        self.tokens = tokens
        self.requests = []
        self.responses = SimpleNamespace(create=self.create)

    async def create(self, **request):
        assert request["parallel_tool_calls"] is False
        assert request["store"] is False
        request["input"] = request["input"].copy()
        self.requests.append(request)
        index = len(self.requests)
        action = self.actions.pop(0)
        if callable(action):
            action = await action(request)
        output = []
        if action is not None:
            name, args = action
            output = [
                SimpleNamespace(
                    type="function_call",
                    call_id=f"call-{index}",
                    name=name,
                    arguments=args if isinstance(args, str) else json.dumps(args),
                )
            ]
        return SimpleNamespace(
            output=output, usage=SimpleNamespace(input_tokens=self.tokens[0], output_tokens=self.tokens[1])
        )


def final_payload(recipe=None, evidence_id=None, **changes):
    return {
        "selected_method": recipe.method.value if recipe else "unknown",
        "classification": {"public_api_verified": True},
        "recipe": recipe.model_dump(mode="json") if recipe else None,
        "supporting_evidence": [evidence_id] if evidence_id else [],
        "validation_references": [evidence_id] if evidence_id else [],
        "known_limitations": [],
        "confidence": 0.9,
        "reasons": ["Observed live tender notice"],
        "alternatives": [],
        "additional_exploration_required": False,
    } | changes


def final_action(recipe):
    async def finalize(request):
        output = request["input"][-1]
        assert output["type"] == "function_call_output"
        assert output["call_id"].startswith("call-")
        evidence = json.loads(output["output"])
        assert evidence["verified"]
        return "finalize_exploration", final_payload(recipe, evidence["evidence_id"])

    return finalize


def run_with(store, site, model, **kwargs):
    return Explorer(store, llm=model, http_transport=site.transport, resolver=site.resolve, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("save_history", [False, True])
async def test_cli_recommends_a_method_and_only_saves_history_on_request(
    tmp_path, monkeypatch, site, config, capsys, save_history
):
    site.routes["/"] = (200, HTML)
    recipe = html_recipe()
    model = Model(("test_extraction", {"recipe": recipe.model_dump(mode="json")}), final_action(recipe))
    monkeypatch.setattr("watchtower.service.Explorer", lambda store=None: run_with(store, site, model))
    database = tmp_path / "watchtower.db"
    monkeypatch.setenv("WATCHTOWER_DB", str(database))
    args = parser().parse_args([BASE, "--db", str(database)] if save_history else [BASE])

    assert await run(args, config) == 0
    output = capsys.readouterr().out
    assert output.startswith("Recommended method is HTML extraction.")
    assert ("Saved run" in output) is save_history
    assert database.exists() is save_history


@pytest.mark.asyncio
async def test_documented_api_agent_calls_tools_and_finalizes(store, site, config):
    documented_site(site)
    recipe = api_recipe()

    async def first(request):
        assert [entry[2] for entry in site.requests] == ["/robots.txt", "/"]
        observation = json.loads(request["input"][0]["content"])
        assert observation["status"] == 200
        assert observation["page"]["documentation"]
        return "inspect_documentation", {"url": BASE + "/openapi.json"}

    async def next_action(request):
        report = json.loads(request["input"][-1]["output"])
        assert report["format"] == "OpenAPI"
        assert report["endpoints"][0]["urls"] == [API_URL]
        return "test_api", {"url": API_URL}

    llm = Model(
        first,
        next_action,
        ("test_extraction", {"recipe": recipe.model_dump(mode="json")}),
        final_action(recipe),
    )
    result = await run_with(store, site, llm).explore(BASE, config)
    assert result.stop_reason is StopReason.VERIFIED, result.model_dump()
    assert result.best_candidate_method is ExtractionMethod.DOCUMENTED_API
    assert result.candidate_verified and result.classification.public_api_verified
    assert result.usage.llm_calls == 4
    assert result.usage.input_tokens == 400 and result.usage.output_tokens == 200
    assert result.usage.tool_calls == 4
    progress = await store.get_exploration_progress(result.exploration_id)
    assert progress["status"] == "succeeded" and progress["recipe"]["method"] == "documented_api"
    assert progress["steps"][-1]["tool"] == "finalize_exploration"


@pytest.mark.asyncio
async def test_unverified_proposal_rejected_then_clean_insufficient_result(store, site, config):
    site.routes["/"] = (200, HTML)
    recipe = html_recipe()

    async def after_rejection(request):
        feedback = json.loads(request["input"][-1]["output"])
        assert feedback["accepted"] is False
        assert "deterministic" in feedback["error"]
        return "finalize_exploration", final_payload()

    llm = Model(
        ("finalize_exploration", final_payload(recipe, supporting_evidence=["step-1"])), after_rejection
    )
    result = await run_with(store, site, llm).explore(BASE, config)
    assert result.stop_reason is StopReason.INSUFFICIENT_EVIDENCE, result.model_dump()
    assert result.classification.public_api_verified is None
    assert not result.candidate_verified
    assert result.usage.tool_calls == 2


@pytest.mark.asyncio
async def test_prior_verified_recipe_survives_failed_run_and_resume(store, site, config):
    site.routes["/"] = (200, HTML)
    recipe = html_recipe()
    first = await run_with(
        store,
        site,
        Model(
            ("test_extraction", {"recipe": recipe.model_dump(mode="json")}),
            final_action(recipe),
        ),
    ).explore(BASE, config)
    assert first.stop_reason is StopReason.VERIFIED, first.model_dump()
    async with aiosqlite.connect(store.path) as db:
        [(selected_id,)] = await db.execute_fetchall(
            "SELECT selected_recipe_id FROM portal WHERE id = ?", (first.portal_id,)
        )
        assert selected_id
    site.routes["/"] = (401, "Authentication required")
    failed = await run_with(store, site, Model()).explore(BASE, config)
    assert failed.stop_reason is StopReason.AUTHENTICATION_REQUIRED
    async with aiosqlite.connect(store.path) as db:
        [portal] = await db.execute_fetchall(
            "SELECT selected_recipe_id, latest_exploration_status FROM portal WHERE id = ?",
            (first.portal_id,),
        )
        assert portal == (selected_id, "blocked")
    site.routes["/"] = (200, HTML)

    async def resumed_test(request):
        initial = json.loads(request["input"][0]["content"])
        assert initial["prior_run"]["finding"] == "AUTHENTICATION_REQUIRED"
        return "test_extraction", {"recipe": recipe.model_dump(mode="json")}

    resumed = await run_with(store, site, Model(resumed_test, final_action(recipe))).resume(
        failed.exploration_id, config
    )
    assert resumed.resumed_from == failed.exploration_id and resumed.stop_reason is StopReason.VERIFIED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limits", "expected", "network_count", "llm_count"),
    [
        ({"max_http_requests": 1}, StopReason.HTTP_LIMIT, 1, 0),
        ({"max_llm_calls": 1}, StopReason.LLM_LIMIT, 2, 1),
        ({"max_input_tokens": 20}, StopReason.TOKEN_LIMIT, 2, 0),
        ({"max_output_tokens": 5}, StopReason.TOKEN_LIMIT, 2, 0),
        ({"max_estimated_cost_usd": 0}, StopReason.COST_LIMIT, 2, 0),
        ({"max_duration_seconds": 0.2}, StopReason.TIME_LIMIT, 0, 0),
        ({"max_unique_pages": 1}, StopReason.PAGE_LIMIT, 2, 0),
        ({"max_tool_calls": 1}, StopReason.TOOL_LIMIT, 2, 1),
    ],
)
async def test_hard_budget_exhaustion_is_persisted(
    store, site, config, limits, expected, network_count, llm_count
):
    site.routes["/"] = (200, HTML)
    cfg = config.model_copy(update=limits)
    result = await run_with(store, site, Model(("inspect_page", {"url": BASE + "/"}))).explore(BASE, cfg)
    assert result.stop_reason is expected, result.model_dump()
    assert result.usage.http_requests == network_count
    assert result.usage.llm_calls == llm_count
    assert (await store.get_exploration_progress(result.exploration_id))["stop_reason"] == expected


@pytest.mark.asyncio
async def test_model_token_usage_exceeding_reservation_stops_after_call(store, site, config):
    site.routes["/"] = (200, HTML)
    cfg = config.model_copy(update={"max_output_tokens": 24})
    result = await run_with(
        store,
        site,
        Model(
            ("inspect_page", {"url": BASE + "/"}),
            tokens=(100, 25),
        ),
    ).explore(BASE, cfg)
    assert result.stop_reason is StopReason.TOKEN_LIMIT
    assert result.usage.llm_calls == 1 and result.usage.tool_calls == 0 and result.usage.output_tokens == 25


@pytest.mark.asyncio
async def test_duplicate_call_reports_no_progress_without_extra_execution(store, site, config):
    site.routes["/"] = (200, HTML)
    call = ("inspect_page", {"url": BASE})
    result = await run_with(store, site, Model(call, call)).explore(BASE, config)
    assert result.stop_reason is StopReason.REPEATED_TOOL_CALLS
    assert result.usage.tool_calls == 1 and result.usage.llm_calls == 2
    assert len(site.requests) == 2


@pytest.mark.asyncio
async def test_malformed_arguments_and_tool_exception_are_returned(store, site, config, monkeypatch):
    site.routes["/"] = (200, HTML)

    async def throws(*_):
        raise RuntimeError("Bearer generated-secret")

    from watchtower.tools import REGISTRY, Tool

    original = REGISTRY["inspect_network"]
    monkeypatch.setitem(REGISTRY, "inspect_network", Tool(original.description, original.schema, throws))

    async def after_malformed(request):
        assert "Malformed JSON" in json.loads(request["input"][-1]["output"])["error"]
        return "inspect_network", {"url": BASE}

    async def after_error(request):
        assert json.loads(request["input"][-1]["output"])["error"] == "Tool failed (RuntimeError)"
        return "finalize_exploration", final_payload()

    result = await run_with(
        store, site, Model(("inspect_page", "{invalid"), after_malformed, after_error)
    ).explore(BASE, config)
    assert result.stop_reason is StopReason.INSUFFICIENT_EVIDENCE
    assert result.usage.tool_calls == 2
    progress = await store.get_exploration_progress(result.exploration_id)
    assert "generated-secret" not in str(progress)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["http", "model"])
async def test_cancellation_during_slow_operation(
    store,
    site,
    config,
    phase,
):
    site.routes["/"] = (200, HTML)
    entered = asyncio.Event()
    exited = asyncio.Event()

    async def slow(_):
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            exited.set()

    llm = Model()
    if phase == "http":
        site.routes["/"] = slow
    else:
        llm.actions = [slow]
    explorer = run_with(store, site, llm)
    task = asyncio.create_task(explorer.explore(BASE, config))
    try:
        await asyncio.wait_for(entered.wait(), 4)
        run_id = next(run_id for run_id, active in _active.items() if active.task is task)
        await explorer.cancel_exploration(run_id)
        result = await task
        assert exited.is_set()
        assert result.stop_reason is StopReason.CANCELLED
        assert result.usage.http_requests == 2
        assert result.usage.llm_calls == (1 if phase == "model" else 0)
        progress = await explorer.get_exploration_progress(run_id)
        assert progress["status"] == "cancelled" and progress["steps"][-1]["error"] == "CANCELLED"
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_openai_counting_requests_and_retries_are_charged_to_request_budget(store, site, config):
    site.routes["/"] = (200, HTML)
    calls = []

    async def count(**request):
        calls.append("count")
        return SimpleNamespace(input_tokens=100)

    async def failed(_):
        calls.append("failed")
        raise TimeoutError

    llm = Model(failed, ("finalize_exploration", final_payload()))
    llm.responses.input_tokens = SimpleNamespace(count=count)
    result = await run_with(store, site, llm).explore(BASE, config)
    assert result.stop_reason is StopReason.INSUFFICIENT_EVIDENCE
    assert calls == ["count", "failed"] and result.usage.llm_calls == 3
    assert result.usage.unreported_model_usage == 1
    assert result.usage.unreported_input_reservation == 100
    assert result.usage.unreported_output_reservation == config.max_model_output_per_call
    assert result.usage.input_tokens == 100


@pytest.mark.asyncio
async def test_model_without_function_stops_at_consecutive_failure_limit(store, site, config):
    site.routes["/"] = (200, HTML)
    result = await run_with(store, site, Model(None, None, None)).explore(BASE, config)
    assert result.stop_reason is StopReason.FAILURE_LIMIT and result.usage.tool_calls == 0
