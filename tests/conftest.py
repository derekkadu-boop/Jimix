import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import pytest
import pytest_asyncio

from watchtower.context import ExplorationContext
from watchtower.models import ExplorationConfig
from watchtower.storage import ExplorationStore
from watchtower.transport import ControlledTransport

BASE = "https://procurement.example.eu"


class Stream(httpx.AsyncByteStream):
    def __init__(self, content):
        self.content = content

    async def __aiter__(self):
        yield self.content


def reply(status=200, body="", kind="text/html", **headers):
    return httpx.Response(
        status,
        headers={"Content-Type": kind} | headers,
        stream=Stream(body.encode() if isinstance(body, str) else body),
    )


@dataclass
class Site:
    routes: dict = field(default_factory=dict)
    requests: list = field(default_factory=list)
    resolved_hosts: list = field(default_factory=list)

    async def resolve(self, host, port):
        self.resolved_hosts.append((host, port))
        return "93.184.216.34"

    async def send(self, request):
        key = (request.headers["host"], request.url.raw_path.decode())
        self.requests.append((request.method, *key, request.headers["user-agent"]))
        route = self.routes.get(key) or self.routes.get(key[1])
        if route is None:
            return reply(404, kind="text/plain")
        if isinstance(route, Callable):
            return await route(request)
        return reply(*route)

    @property
    def transport(self):
        return httpx.MockTransport(self.send)


@pytest_asyncio.fixture
async def store(tmp_path):
    db = ExplorationStore(tmp_path / "explorations.db")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture(autouse=True)
def no_live_throttle(monkeypatch):
    monkeypatch.setattr("watchtower.transport.MIN_REQUEST_INTERVAL", 0)


@pytest.fixture
def config():
    return ExplorationConfig(
        request_interval_seconds=0,
        http_timeout_seconds=0.5,
        llm_timeout_seconds=1,
        max_duration_seconds=25,
        max_input_tokens=80_000,
    )


@pytest.fixture
def site():
    return Site()


@pytest_asyncio.fixture
async def context(site, store, config):
    portal, run, effective = await store.start(BASE + "/", config.model_dump(), None)
    ctx = ExplorationContext(BASE + "/", portal, run, effective, store, asyncio.get_running_loop().time())
    ctx.transport = ControlledTransport(ctx, http_transport=site.transport, resolver=site.resolve)
    try:
        yield ctx
    finally:
        await ctx.transport.close()
