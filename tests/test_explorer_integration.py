import asyncio
import json

import httpx
import pytest
from conftest import BASE
from openai import AsyncOpenAI
from test_explorer_agent import Model, final_payload, run_with
from test_explorer_tools import HTML

from watchtower import StopReason
from watchtower.service import Explorer
from watchtower.transport import verified_tls_context


@pytest.mark.asyncio
async def test_actual_sdk_serialization_with_mock_openai_http(store, site, config):
    site.routes["/"] = (200, HTML)
    paths = []

    async def api(request):
        paths.append(request.url.path)
        payload = json.loads(request.content)
        assert payload["parallel_tool_calls"] is False
        assert any(tool["name"] == "finalize_exploration" for tool in payload["tools"])
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        assert payload["store"] is False
        assert payload["max_output_tokens"] <= config.max_output_tokens
        return httpx.Response(
            200,
            json={
                "id": "resp_synthetic",
                "object": "response",
                "created_at": 1732345678,
                "model": config.model,
                "status": "completed",
                "output": [
                    {
                        "id": "fc_synthetic",
                        "type": "function_call",
                        "call_id": "call_synthetic",
                        "name": "finalize_exploration",
                        "arguments": json.dumps(final_payload()),
                        "status": "completed",
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens": 50,
                    "output_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": 150,
                },
            },
        )

    async with AsyncOpenAI(
        api_key="synthetic-test-only",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(api),
        ),
    ) as llm:
        result = await run_with(store, site, llm).explore(BASE, config)
    assert result.stop_reason is StopReason.INSUFFICIENT_EVIDENCE, result.model_dump()
    assert paths == ["/v1/responses/input_tokens", "/v1/responses"] and result.usage.llm_calls == 2


@pytest.mark.asyncio
async def test_synthetic_site_over_real_loopback_socket(store, config):
    requests = []

    async def respond(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            path = headers.split(b" ", 2)[1]
            requests.append(headers)
            found = path == b"/"
            body = HTML.encode("utf-8") if found else b""
            writer.write(
                (b"HTTP/1.1 200 OK" if found else b"HTTP/1.1 404 Not Found")
                + b"\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    class LocalFixtureTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            # Test-only mapping after production URL and IP checks. This sends
            # no traffic to the synthetic hostname or validated fixture IP.
            assert request.url.host == "93.184.216.34"
            request.url = request.url.copy_with(host="127.0.0.1", port=port)
            return await super().handle_async_request(request)

    async def resolve(host, _):
        assert host == "procurement.example.eu"
        return "93.184.216.34"

    async with server:
        explorer = Explorer(
            store,
            llm=Model(),
            resolver=resolve,
            http_transport=LocalFixtureTransport(verify=verified_tls_context(), trust_env=False),
        )
        result = await explorer.explore(
            BASE.replace("https://", "http://"),
            config.model_copy(
                update={"max_llm_calls": 0},
            ),
        )
    assert result.stop_reason is StopReason.LLM_LIMIT, result.model_dump()
    assert result.initial_observation.status == 200 and result.initial_observation.robots.status == "absent"
    assert len(requests) == 2
    assert all(b"host: procurement.example.eu" in req.lower() for req in requests)


@pytest.mark.asyncio
async def test_wall_clock_timeout_interrupts_inflight_tool(store, site, config):
    site.routes["/"] = (200, HTML)
    entered, exited = asyncio.Event(), asyncio.Event()

    async def slow_render(*_):
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            exited.set()

    cfg = config.model_copy(update={"max_duration_seconds": 1.25, "llm_timeout_seconds": 0.2})
    result = await run_with(
        store, site, Model(("inspect_network", {"url": BASE})), browser=slow_render
    ).explore(BASE, cfg)
    assert result.stop_reason is StopReason.TIME_LIMIT
    assert entered.is_set() and exited.is_set()
    assert result.usage.tool_calls == 1
    assert (await store.get_exploration_progress(result.exploration_id))["status"] == "incomplete"
