from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_USER_AGENT = "WatchtowerExplorer/0.1 (Patterno Watchtower public procurement crawler)"
REQUIRED_FIELDS = ("id", "title")
Short = Annotated[str, Field(min_length=1, max_length=250)]
URLText = Annotated[str, Field(min_length=1, max_length=2048)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class URLInput(Model):
    url: URLText


class ExplorationConfig(Model):
    max_tool_calls: int = Field(12, ge=1, le=200)
    max_llm_calls: int = Field(10, ge=0, le=100)
    max_http_requests: int = Field(30, ge=1, le=500)
    max_unique_pages: int = Field(12, ge=1, le=200)
    max_duration_seconds: float = Field(120, gt=0, le=3600)
    max_input_tokens: int = Field(30_000, ge=0, le=1_000_000)
    max_output_tokens: int = Field(6_000, ge=0, le=100_000)
    max_estimated_cost_usd: float = Field(0.10, ge=0)
    max_response_bytes: int = Field(2_000_000, ge=1, le=8_000_000)
    max_document_probe_bytes: int = Field(65_536, ge=1, le=262_144)
    max_browser_navigations: int = Field(3, ge=0, le=30)
    max_documentation_depth: int = Field(2, ge=0, le=10)
    max_consecutive_failures: int = Field(3, ge=1, le=20)
    max_repeated_tool_calls: int = Field(2, ge=1, le=20)
    request_interval_seconds: float = Field(1.0, ge=0, le=120)
    http_timeout_seconds: float = Field(10, gt=0, le=120)
    llm_timeout_seconds: float = Field(20, gt=0, le=120)
    browser_timeout_seconds: float = Field(18, gt=0, le=120)
    user_agent: str = Field(DEFAULT_USER_AGENT, min_length=1, max_length=250, pattern=r"^[ -~]+$")
    accept: str = Field(
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        min_length=1,
        max_length=250,
        pattern=r"^[ -~]+$",
    )
    accept_language: str = Field("en-US,en;q=0.9", min_length=1, max_length=250, pattern=r"^[ -~]+$")
    ambiguous_robots: Literal["deny", "allow"] = "deny"
    max_redirects: int = Field(5, ge=0, le=15)
    max_json_depth: int = Field(20, ge=2, le=60)
    max_candidate_urls: int = Field(80, ge=1, le=300)
    max_html_chars: int = Field(1400, ge=0, le=6000)
    max_observation_chars: int = Field(4500, ge=512, le=20_000)
    max_model_output_per_call: int = Field(1500, ge=16, le=16_000)
    max_llm_retries: int = Field(1, ge=0, le=3)
    exact_input_token_count: bool = True
    model: str = Field("gpt-5.4-mini", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9._:-]+$")
    input_usd_per_million: float = Field(0.75, ge=0)
    output_usd_per_million: float = Field(4.50, ge=0)


class StopReason(StrEnum):
    VERIFIED = "VERIFIED"
    CAPTCHA = "CAPTCHA"
    BOT_PROTECTION = "BOT_PROTECTION"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    ACCESS_BLOCKED = "ACCESS_BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    ROBOTS_UNAVAILABLE = "ROBOTS_UNAVAILABLE"
    HTTP_LIMIT = "HTTP_LIMIT"
    LLM_LIMIT = "LLM_LIMIT"
    TOOL_LIMIT = "TOOL_LIMIT"
    PAGE_LIMIT = "PAGE_LIMIT"
    TIME_LIMIT = "TIME_LIMIT"
    TOKEN_LIMIT = "TOKEN_LIMIT"
    COST_LIMIT = "COST_LIMIT"
    BROWSER_LIMIT = "BROWSER_LIMIT"
    REPEATED_TOOL_CALLS = "REPEATED_TOOL_CALLS"
    FAILURE_LIMIT = "FAILURE_LIMIT"
    CANCELLED = "CANCELLED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNSAFE_URL = "UNSAFE_URL"
    ERROR = "ERROR"


class ExtractionMethod(StrEnum):
    DOCUMENTED_API = "documented_api"
    DISCOVERED_API = "discovered_api"
    HTML = "html"
    BROWSER = "browser"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class Capabilities(Model):
    documentation_available: bool | None = None
    public_api_observed: bool | None = None
    public_api_verified: bool | None = None
    server_rendered_tender_listings: bool | None = None
    javascript_rendering_required: bool | None = None
    pagination_available: bool | None = None
    tender_detail_access: bool | None = None
    document_access: bool | None = None
    authentication_required: bool | None = None
    captcha_detected: bool | None = None
    robots_restrictions: bool | None = None
    rate_limiting_observed: bool | None = None
    best_extraction_method: ExtractionMethod = ExtractionMethod.UNKNOWN
    alternatives: list[ExtractionMethod] = Field(default_factory=list, max_length=5)


class AccessRestrictionResult(Model):
    restriction_detected: bool = False
    restriction_type: str | None = None
    confidence: float = Field(0, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    retry_after_seconds: int | None = None
    recommended_action: str = "continue"

    @property
    def is_blocking(self) -> bool:
        return self.recommended_action == "stop"


class RobotsDecision(Model):
    allowed: bool
    status: Literal["allowed", "disallowed", "absent", "unavailable", "malformed"]
    robots_url: str
    crawl_delay_seconds: float | None = None
    sitemaps: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class InitialObservation(Model):
    original_url: str
    final_url: str
    status: int
    content_type: str
    access_restriction: AccessRestrictionResult
    robots: RobotsDecision
    page: dict = Field(default_factory=dict)


class FieldMapping(Model):
    path: Short
    attribute: Short | None = None


class Retrieval(Model):
    kind: Literal["api", "html", "browser"]
    url: URLText
    records: Short = Field(
        description="CSS row selector or dot-separated JSON path. Use $ for the JSON root."
    )
    fields: dict[Short, FieldMapping] = Field(min_length=1, max_length=16)
    parameters: dict[Short, Short] = Field(default_factory=dict, max_length=10)


class Pagination(Model):
    kind: Literal["next", "page", "offset"]
    path: Short
    attribute: Short | None = None
    increment: int = Field(1, ge=1, le=1000)


class LinkedRetrieval(Model):
    url_field: Short
    kind: Literal["html", "api", "browser"] = "html"
    fields: dict[Short, FieldMapping] = Field(default_factory=dict, max_length=16)


class ExtractionRecipeSpec(Model):
    method: ExtractionMethod
    listing: Retrieval
    pagination: Pagination | None = None
    detail: LinkedRetrieval | None = None
    document_field: Short | None = None
    known_restrictions: list[Short] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def check_method(self):
        allowed = {
            ExtractionMethod.DOCUMENTED_API: "api",
            ExtractionMethod.DISCOVERED_API: "api",
            ExtractionMethod.HTML: "html",
            ExtractionMethod.BROWSER: "browser",
        }
        if self.method in allowed and allowed[self.method] != self.listing.kind:
            raise ValueError("Method must match the listing retrieval kind")
        if self.method is ExtractionMethod.UNKNOWN:
            raise ValueError("A recipe needs a specific extraction method")
        if self.method is ExtractionMethod.HYBRID and (
            not self.detail or self.detail.kind == self.listing.kind
        ):
            raise ValueError("A hybrid recipe needs distinct listing and detail retrieval kinds")
        return self


class FinalizationProposal(Model):
    selected_method: ExtractionMethod
    classification: Capabilities
    recipe: ExtractionRecipeSpec | None = None
    supporting_evidence: list[Short] = Field(max_length=12)
    validation_references: list[Short] = Field(max_length=8)
    known_limitations: list[Short] = Field(max_length=8)
    confidence: float = Field(ge=0, le=1)
    reasons: list[Short] = Field(max_length=8)
    alternatives: list[ExtractionMethod] = Field(max_length=5)
    additional_exploration_required: bool


class Usage(Model):
    http_requests: int = 0
    unique_pages: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0
    browser_navigations: int = 0
    cache_hits: int = 0
    unreported_model_usage: int = 0
    unreported_input_reservation: int = 0
    unreported_output_reservation: int = 0
    unreported_cost_reservation_usd: float = 0


class ExplorationResult(Model):
    exploration_id: str
    portal_id: str | None = None
    url: str
    status: Literal["succeeded", "incomplete", "blocked", "cancelled", "failed"]
    stop_reason: StopReason
    usage: Usage
    duration_seconds: float
    configuration: ExplorationConfig
    classification: Capabilities
    initial_observation: InitialObservation | None = None
    best_candidate_method: ExtractionMethod = ExtractionMethod.UNKNOWN
    candidate_verified: bool = False
    recipe: ExtractionRecipeSpec | None = None
    validation: dict | None = None
    evidence: list[dict] = Field(default_factory=list)
    last_successful_action: str | None = None
    last_website_state: dict | None = None
    remaining_assumptions: list[str] = Field(default_factory=list)
    recommended_next_step: str
    resumed_from: str | None = None
