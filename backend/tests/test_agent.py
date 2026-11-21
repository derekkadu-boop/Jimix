import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import agent
from app.fetch import Blocked
from app.recipe import Recipe

URL = "https://portal.example/notices"
RECIPE = {
    "method": "html",
    "listing": {"url": URL, "records": "article", "fields": {"id": {"path": "a", "attribute": "href"}}},
}
GIVE_UP = ("give_up", {"reason": "No working recipe", "blocked": False})


def fake_model(monkeypatch, turns, *, problem="no tenders found", blocks=()):
    completions = []
    for turn in turns:
        calls = [
            SimpleNamespace(
                id=f"{name}-{index}",
                function=SimpleNamespace(name=name, arguments=json.dumps(args)),
            )
            for index, (name, args) in enumerate(turn)
        ]
        message = SimpleNamespace(tool_calls=calls, model_dump=lambda **_: {"role": "assistant"})
        completions.append(SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None))
    create = AsyncMock(side_effect=completions)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(agent, "AsyncOpenAI", lambda **_: client)
    monkeypatch.setattr(agent, "discover", AsyncMock(return_value=[]))
    report = {"problem": problem, "warnings": [], "openTenderCount": int(problem is None)}
    monkeypatch.setattr(agent, "_test", AsyncMock(return_value=report))
    monkeypatch.setattr(agent, "MAX_TURNS", 8)
    fetcher = SimpleNamespace(blocks=list(blocks))

    def run():
        return asyncio.run(agent.explore(URL, fetcher, agent.Usage()))

    return run, create


def refusals(create):
    messages = create.call_args.kwargs["messages"]
    return [m["content"] for m in messages if m["role"] == "tool" and m["content"].startswith("REFUSED:")]


def test_give_up_waits_for_quarter_budget_even_after_a_recipe_test(monkeypatch):
    run, create = fake_model(monkeypatch, [
        [("test_recipe", {"recipe": RECIPE}), GIVE_UP],
        [GIVE_UP],
    ])

    with pytest.raises(agent.Failed, match="No working recipe"):
        run()

    assert create.await_count == 2
    assert len(refusals(create)) == 1
    agent._test.assert_awaited_once()


def test_give_up_with_no_real_recipe_test_waits_for_half_budget(monkeypatch):
    run, create = fake_model(monkeypatch, [
        [("test_recipe", {"recipe": {}})],
        [GIVE_UP],
        [GIVE_UP],
        [GIVE_UP],
    ])

    with pytest.raises(agent.Failed, match="No working recipe"):
        run()

    assert create.await_count == 4
    assert len(refusals(create)) == 2
    agent._test.assert_not_awaited()


def test_confirmed_access_denial_can_exit_on_first_turn(monkeypatch):
    run, create = fake_model(
        monkeypatch,
        [[("give_up", {"reason": "Access denied", "blocked": True})]],
        blocks=["Access denied"],
    )

    with pytest.raises(Blocked, match="Access denied"):
        run()

    assert create.await_count == 1


def test_successful_submit_can_exit_on_first_turn(monkeypatch):
    run, create = fake_model(monkeypatch, [[("submit_recipe", {"recipe": RECIPE})]], problem=None)

    assert run() == Recipe.model_validate(RECIPE)
    assert create.await_count == 1


def test_model_receives_untrusted_discovery_guidance(monkeypatch):
    run, create = fake_model(monkeypatch, [[("submit_recipe", {"recipe": RECIPE})]], problem=None)
    evidence = "[fixture] public archive description"
    monkeypatch.setattr(agent, "discover", AsyncMock(return_value=evidence))

    run()

    instruction, portal = create.call_args.kwargs["messages"][:2]
    assert instruction["role"] == "system"
    assert "automatic discovery report" in instruction["content"]
    assert "untrusted evidence" in instruction["content"]
    assert evidence not in instruction["content"]
    assert portal["role"] == "user" and evidence in portal["content"]
