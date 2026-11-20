import asyncio
import logging
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from .models import (
    Capabilities,
    ExplorationConfig,
    ExtractionMethod,
    ExtractionRecipeSpec,
    InitialObservation,
    StopReason,
    Usage,
)
from .storage import ExplorationStore, utcnow
from .urls import sanitize

logger = logging.getLogger("watchtower")


class StopExploration(Exception):
    def __init__(self, reason: StopReason):
        super().__init__(reason.value)
        self.reason = reason


class ToolError(ValueError):
    pass


@dataclass
class ExplorationContext:
    url: str
    portal_id: str | None
    run_id: str
    config: ExplorationConfig
    store: ExplorationStore | None
    started: float
    resumed_from: str | None = None
    usage: Usage = field(default_factory=Usage)
    capabilities: Capabilities = field(default_factory=Capabilities)
    pages: set[str] = field(default_factory=set)
    candidates: dict[str, set[str]] = field(default_factory=dict)
    documentation_depth: dict[str, int] = field(default_factory=dict)
    documented_endpoints: set[str] = field(default_factory=set)
    documented_parameters: dict[str, set[str]] = field(default_factory=dict)
    blocked: set[str] = field(default_factory=set)
    repetitions: Counter = field(default_factory=Counter)
    evidence: list[dict] = field(default_factory=list)
    validations: dict[str, tuple[ExtractionRecipeSpec, dict]] = field(default_factory=dict)
    browser_evidence: dict[str, Any] = field(default_factory=dict)
    best: tuple[ExtractionRecipeSpec, dict] | None = None
    proposal: Any = None
    initial: InitialObservation | None = None
    stop: StopReason | None = None
    failures: int = 0
    last_action: str | None = None
    last_state: dict | None = None
    step_number: int = 0
    transport: Any = None
    browser: Any = None
    browser_unavailable: bool = False
    browser_limit_encountered: bool = False
    llm: Any = None

    @property
    def deadline(self) -> float:
        return self.started + self.config.max_duration_seconds

    @property
    def elapsed(self) -> float:
        return asyncio.get_running_loop().time() - self.started

    @property
    def remaining(self) -> float:
        return self.config.max_duration_seconds - self.elapsed

    def require(self, *, ignoring: tuple[StopReason, ...] = (), duration: float = 0):
        reason = check_stop_conditions(self, ignoring=ignoring)
        if reason:
            raise StopExploration(reason)
        if duration >= self.remaining:
            raise StopExploration(StopReason.TIME_LIMIT)

    def log(self, message: str, *args):
        elapsed = int(self.elapsed)
        logger.info("[%02d:%02d] " + message, elapsed // 60, elapsed % 60, *args)

    def add_candidate(self, url: str, role: str) -> bool:
        if url not in self.candidates and len(self.candidates) >= self.config.max_candidate_urls:
            return False
        self.candidates.setdefault(url, set()).add(role)
        return True

    def is_blocked(self, url: str) -> bool:
        return url.partition("?")[0] in self.blocked

    def block(self, reason: StopReason, *urls: str):
        self.blocked.update(url.partition("?")[0] for url in urls)
        if reason is StopReason.CAPTCHA:
            self.capabilities.captcha_detected = True
        elif reason is StopReason.AUTHENTICATION_REQUIRED:
            self.capabilities.authentication_required = True

    def add_page(self, url: str):
        if url not in self.pages:
            if self.usage.unique_pages >= self.config.max_unique_pages:
                raise StopExploration(StopReason.PAGE_LIMIT)
            self.pages.add(url)
            self.usage.unique_pages += 1

    def remember_candidate(self, recipe: ExtractionRecipeSpec, validation: dict):
        priority = {"documented_api": 5, "discovered_api": 4, "html": 3, "hybrid": 2, "browser": 1}

        def score(candidate, evidence):
            return (
                bool(evidence.get("verified")),
                priority[candidate.method.value],
                sum(
                    evidence.get(field) is True
                    for field in (
                        "detail_verified",
                        "document_verified",
                        "pagination_verified",
                    )
                ),
                evidence.get("sample_count", 0),
            )

        if (
            not self.best
            or self.best[0] == recipe
            and not self.best[1].get("verified")
            or score(recipe, validation) > score(*self.best)
        ):
            self.best = recipe, validation

    def progress(self) -> dict:
        return {
            "url": self.url,
            "usage": self.usage.model_dump(mode="json"),
            "duration_seconds": round(self.elapsed, 3),
            "best_candidate_method": self.best[0].method.value if self.best else "unknown",
            "candidate_verified": bool(self.best and self.best[1].get("verified")),
            "last_successful_action": self.last_action,
            "last_website_state": self.last_state,
            "evidence": list(self.evidence),
            "initial_observation": self.initial.model_dump(mode="json") if self.initial else None,
            "classification": self.capabilities.model_dump(mode="json"),
            "resume_checkpoint": self.resume_checkpoint(),
        }

    def resume_checkpoint(self) -> dict:
        return {
            "candidates": [
                {"url": url, "roles": sorted(roles), "documentation_depth": self.documentation_depth.get(url)}
                for url, roles in self.candidates.items()
            ]
        }

    async def checkpoint(self, step: dict | None = None):
        if self.store is not None:
            await self.store.checkpoint(self.run_id, self.progress(), step)

    @asynccontextmanager
    async def step(self, name: str, arguments: dict):
        self.step_number += 1
        number = self.step_number
        started, at = utcnow(), self.elapsed
        previous_requests = self.usage.http_requests
        observation: dict = {"evidence_id": f"step-{number}"}
        error = None
        try:
            yield observation
            error = observation.get("error")
        except BaseException as exc:
            error = (
                "CANCELLED"
                if isinstance(exc, asyncio.CancelledError)
                else str(exc)
                if isinstance(exc, (StopExploration, ToolError))
                else type(exc).__name__
            )
            raise
        finally:
            ended = utcnow()
            success = error is None
            if success:
                self.last_action = name
            if self.store is not None:
                await self.checkpoint(
                    {
                        "step_number": number,
                        "tool_name": name,
                        "arguments_json": sanitize(arguments),
                        "observation_json": sanitize(observation),
                        "started_at": started,
                        "finished_at": ended,
                        "duration_ms": round((self.elapsed - at) * 1000),
                        "http_requests_used": self.usage.http_requests - previous_requests,
                        "success": success,
                        "error": sanitize(error),
                    }
                )


# In-flight actions can commit observations after consuming their last unit. This
# list lets each new action check the full run while allowing its parent's slot.
IN_FLIGHT_LIMITS = (StopReason.LLM_LIMIT, StopReason.TOOL_LIMIT, StopReason.TOKEN_LIMIT)


def _only_browser_option(context: ExplorationContext) -> bool:
    if not context.browser_limit_encountered or not context.initial:
        return False
    if context.best and context.best[1].get("verified") or context.capabilities.public_api_observed:
        return False
    page = context.initial.page
    if not page.get("js_rendering_signals") or any(
        page.get(key)
        for key in (
            "documentation",
            "tender_content",
            "sitemaps",
            "embedded_json",
            "links",
            "pagination",
        )
    ):
        return False
    # Establish that the static application state was checked before ruling out alternatives.
    return any(
        event.get("tool") == "inspect_scripts"
        and not event.get("error")
        and not event.get("embedded_json")
        and not event.get("endpoints")
        for event in context.evidence
    )


def check_stop_conditions(
    context: ExplorationContext,
    *,
    ignoring: tuple[StopReason, ...] = (),
) -> StopReason | None:
    if context.stop:
        return context.stop
    usage, config = context.usage, context.config
    limits = (
        (StopReason.TIME_LIMIT, context.remaining <= 0),
        (StopReason.HTTP_LIMIT, usage.http_requests >= config.max_http_requests),
        (StopReason.PAGE_LIMIT, usage.unique_pages >= config.max_unique_pages),
        (StopReason.TOOL_LIMIT, usage.tool_calls >= config.max_tool_calls),
        (StopReason.LLM_LIMIT, usage.llm_calls >= config.max_llm_calls),
        (
            StopReason.TOKEN_LIMIT,
            usage.input_tokens + usage.unreported_input_reservation >= config.max_input_tokens
            or usage.output_tokens + usage.unreported_output_reservation >= config.max_output_tokens,
        ),
        (
            StopReason.COST_LIMIT,
            usage.estimated_cost_usd + usage.unreported_cost_reservation_usd > config.max_estimated_cost_usd,
        ),
        (StopReason.BROWSER_LIMIT, _only_browser_option(context)),
        (StopReason.FAILURE_LIMIT, context.failures >= config.max_consecutive_failures),
    )
    return next((reason for reason, reached in limits if reached and reason not in ignoring), None)
