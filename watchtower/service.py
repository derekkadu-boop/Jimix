import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
from openai import AsyncOpenAI, OpenAIError

from .agent import run_exploration_agent
from .browser import observe_browser
from .context import ExplorationContext, StopExploration, ToolError
from .models import ExplorationConfig, ExplorationResult, StopReason
from .observations import initial_fetch
from .storage import ExplorationStore
from .tools.finalization import build_result
from .transport import ControlledTransport, verified_tls_context
from .urls import UnsafeURL, normalize_url, resolve_public


@dataclass
class ActiveRun:
    context: ExplorationContext
    task: asyncio.Task
    cancel_requested: bool = False


_active: dict[str, ActiveRun] = {}


async def _settle(operation):
    """Finish a short storage transaction even when its caller is cancelled."""
    task = asyncio.create_task(operation)
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True


class Explorer:
    def __init__(
        self,
        store: ExplorationStore | None = None,
        *,
        llm=None,
        http_transport=None,
        resolver=resolve_public,
        browser=observe_browser,
    ):
        self.store = store
        self.llm = llm
        self.http_transport = http_transport
        self.resolver = resolver
        self.browser = browser

    async def explore(
        self,
        url: str,
        config: ExplorationConfig | None = None,
        *,
        resumed_from: str | None = None,
    ) -> ExplorationResult:
        started = asyncio.get_running_loop().time()
        url = normalize_url(url)
        prior = await self._history_store().get_exploration_progress(resumed_from) if resumed_from else None
        if self.store is None:
            portal_id, run_id, effective = None, uuid4().hex, config or ExplorationConfig()
            cancelled = False
        else:
            overrides = config.model_dump(exclude_unset=True) if config else {}
            (portal_id, run_id, effective), cancelled = await _settle(
                self.store.start(url, overrides, resumed_from)
            )
        ctx = ExplorationContext(url, portal_id, run_id, effective, self.store, started, resumed_from)
        task = asyncio.current_task()
        _active[run_id] = ActiveRun(ctx, task)
        ctx.log("Starting exploration (%s)", run_id)
        try:
            if cancelled:
                raise asyncio.CancelledError
            async with asyncio.timeout_at(ctx.deadline):
                async with AsyncExitStack() as stack:
                    ctx.transport = ControlledTransport(
                        ctx, http_transport=self.http_transport, resolver=self.resolver
                    )
                    stack.push_async_callback(ctx.transport.close)
                    ctx.browser = self.browser
                    observation = await initial_fetch(url, ctx)
                    if ctx.stop:
                        raise StopExploration(ctx.stop)
                    if prior:
                        _restore_candidates(prior.get("resume_checkpoint", {}), ctx)
                    ctx.require(duration=effective.llm_timeout_seconds)
                    ctx.llm = self.llm
                    if ctx.llm is None:
                        http = await stack.enter_async_context(
                            httpx.AsyncClient(verify=verified_tls_context(), trust_env=False)
                        )
                        try:
                            ctx.llm = AsyncOpenAI(
                                max_retries=0,
                                timeout=effective.llm_timeout_seconds,
                                http_client=http,
                            )
                        except OpenAIError:
                            async with ctx.step("openai_setup", {}) as trace:
                                trace["error"] = "Set OPENAI_API_KEY before running model exploration"
                                ctx.evidence.append(trace.copy())
                            raise StopExploration(StopReason.ERROR) from None
                    result = await run_exploration_agent(observation, ctx, resumed=prior)
        except asyncio.CancelledError:
            result = build_result(ctx, StopReason.CANCELLED)
        except TimeoutError:
            result = build_result(ctx, StopReason.TIME_LIMIT)
        except StopExploration as exc:
            result = build_result(ctx, exc.reason)
        except UnsafeURL as exc:
            ctx.evidence.append({"tool": "url_safety", "error": str(exc)})
            result = build_result(ctx, StopReason.UNSAFE_URL)
        except ToolError as exc:
            ctx.evidence.append({"tool": "exploration", "error": str(exc)})
            result = build_result(
                ctx,
                StopReason.FAILURE_LIMIT
                if ctx.failures >= effective.max_consecutive_failures
                else StopReason.ERROR,
            )
        except Exception as exc:
            ctx.evidence.append({"tool": "exploration", "error": type(exc).__name__})
            result = build_result(ctx, StopReason.ERROR)

        try:
            if self.store is not None:
                await _settle(self.store.finish(result, ctx.resume_checkpoint()))
            ctx.log(
                "Exploration finished (%s, %s)", result.stop_reason.value, result.best_candidate_method.value
            )
            _log_usage(result, ctx)
            return result
        finally:
            _active.pop(run_id, None)

    def _history_store(self) -> ExplorationStore:
        if self.store is None:
            raise ValueError("Saved history requires an ExplorationStore")
        return self.store

    async def resume(self, exploration_id: str, config: ExplorationConfig | None = None) -> ExplorationResult:
        if exploration_id in _active:
            raise ValueError("Cancel or finish the active run before resuming it")
        progress = await self._history_store().get_exploration_progress(exploration_id)
        return await self.explore(progress["url"], config, resumed_from=exploration_id)

    async def get_portal_config(self, portal_id: str) -> ExplorationConfig:
        return await self._history_store().get_portal_config(portal_id)

    async def update_portal_config(self, portal_id: str, updates: dict) -> ExplorationConfig:
        return await self._history_store().update_portal_config(portal_id, updates)

    async def get_exploration_progress(self, run_id: str) -> dict:
        saved = await self._history_store().get_exploration_progress(run_id)
        active = _active.get(run_id)
        if active and saved["status"] == "running":
            return saved | active.context.progress() | {"cancellation_requested": active.cancel_requested}
        return saved

    async def cancel_exploration(self, exploration_id: str) -> None:
        await cancel_exploration(exploration_id)


def _restore_candidates(snapshot: dict, ctx: ExplorationContext):
    permitted = {"page", "pagination", "detail", "document", "documentation", "api", "sitemap", "script"}
    for entry in snapshot.get("candidates", [])[: ctx.config.max_candidate_urls]:
        try:
            url = normalize_url(entry["url"])
        except UnsafeURL:
            continue
        roles = entry["roles"]
        for role in sorted(permitted.intersection(roles)):
            ctx.add_candidate(url, role)
        depth = entry.get("documentation_depth")
        if {"documentation", "sitemap"}.intersection(roles) and isinstance(depth, int) and depth >= 1:
            ctx.documentation_depth[url] = min(depth, ctx.documentation_depth.get(url, depth))
    # Prior URLs are leads only. This run must reconstruct documented endpoint
    # provenance by inspecting the documentation and fetch live tender data again.


def _log_usage(result: ExplorationResult, ctx: ExplorationContext):
    usage, config = result.usage, ctx.config
    ctx.log(
        "HTTP requests %d/%d | Unique pages %d/%d | Tools %d/%d | OpenAI requests %d/%d",
        usage.http_requests,
        config.max_http_requests,
        usage.unique_pages,
        config.max_unique_pages,
        usage.tool_calls,
        config.max_tool_calls,
        usage.llm_calls,
        config.max_llm_calls,
    )
    ctx.log(
        "Input tokens %d/%d | Output tokens %d/%d | Estimated cost $%.4f/$%.2f | Elapsed %.2fs/%.2fs",
        usage.input_tokens,
        config.max_input_tokens,
        usage.output_tokens,
        config.max_output_tokens,
        usage.estimated_cost_usd,
        config.max_estimated_cost_usd,
        result.duration_seconds,
        config.max_duration_seconds,
    )


async def cancel_exploration(exploration_id: str) -> None:
    active = _active.get(exploration_id)
    if active is None:
        return
    if not active.cancel_requested:
        active.cancel_requested = True
        active.task.cancel()
    if active.task is not asyncio.current_task():
        # Waiting shields the run's cleanup from cancellation of this caller.
        await asyncio.shield(active.task)


async def explore_portal(url: str, config: ExplorationConfig | None = None) -> ExplorationResult:
    return await Explorer().explore(url, config)


async def resume_exploration(
    exploration_id: str, config: ExplorationConfig | None = None
) -> ExplorationResult:
    async with _default_explorer() as explorer:
        return await explorer.resume(exploration_id, config)


async def get_portal_config(portal_id: str) -> ExplorationConfig:
    async with _default_explorer() as explorer:
        return await explorer.get_portal_config(portal_id)


async def update_portal_config(portal_id: str, updates: dict) -> ExplorationConfig:
    async with _default_explorer() as explorer:
        return await explorer.update_portal_config(portal_id, updates)


async def get_exploration_progress(run_id: str) -> dict:
    async with _default_explorer() as explorer:
        return await explorer.get_exploration_progress(run_id)


@asynccontextmanager
async def _default_explorer(path: str | Path | None = None):
    store = ExplorationStore(path)
    try:
        await store.initialize()
        yield Explorer(store)
    finally:
        await store.close()
