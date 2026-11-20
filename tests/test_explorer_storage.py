import asyncio

import pytest

from watchtower.models import ExplorationConfig
from watchtower.storage import ExplorationStore
from watchtower.urls import UnsafeURL, normalize_url, sanitize


@pytest.mark.asyncio
async def test_configuration_persisted_reloaded_and_snapshotted(tmp_path):
    path = tmp_path / "explorer.db"
    store = ExplorationStore(path)
    await store.initialize()
    portal, run, config = await store.start("https://procurement.example.eu/", {"max_llm_calls": 2}, None)
    assert config.max_llm_calls == 2
    assert (await store.get_portal_config(portal)).max_llm_calls == 10
    await store.update_portal_config(portal, {"max_llm_calls": 5, "max_document_probe_bytes": 1000})
    await store.close()
    store = ExplorationStore(path)
    try:
        assert (await store.get_portal_config(portal)).max_llm_calls == 5
        _, _, effective = await store.start("https://procurement.example.eu/", {}, None)
        assert effective.max_document_probe_bytes == 1000
        assert (await store.get_exploration_progress(run))["configuration"]["max_llm_calls"] == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_concurrent_stores_can_start_runs_for_one_portal(tmp_path):
    stores = [ExplorationStore(tmp_path / "shared.db") for _ in range(5)]

    async def start(store):
        await store.initialize()
        return await store.start("https://procurement.example.eu/", {}, None)

    try:
        runs = await asyncio.gather(*(start(store) for store in stores))
        assert len({portal_id for portal_id, _, _ in runs}) == 1
        assert len({run_id for _, run_id, _ in runs}) == len(stores)
    finally:
        await asyncio.gather(*(store.close() for store in stores))


def test_defaults_and_secret_handling():
    config = ExplorationConfig()
    assert config.max_http_requests == 30
    assert config.max_document_probe_bytes == 65_536
    assert normalize_url("EXAMPLE.eu/portal?page=2#ignored") == "https://example.eu/portal?page=2"
    with pytest.raises(UnsafeURL):
        normalize_url("https://example.eu?token=abc")
    assert sanitize({"authorization": "Bearer example", "url": "https://example.eu?token=abc&page=2"}) == {
        "url": "https://example.eu/?page=2"
    }
