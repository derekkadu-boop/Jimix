import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from .models import AccessRestrictionResult, StopReason
from .phrases import PHRASES, contains_phrase

AUTH_PATH = re.compile(
    r"(?:^|/)(?:login|log-in|signin|sign-in|sso|oauth2?|saml|auth|authorize|authenticate)(?:[/.;-]|$)", re.I
)
CHALLENGE_PATH = re.compile(r"(?:/cdn-cgi/challenge-platform|/(?:captcha|bot-challenge)(?:[/.;-]|$))", re.I)


@dataclass
class HTTPObservation:
    original_url: str
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes = b""
    truncated: bool = False
    redirects: list[dict] = field(default_factory=list)
    client: str = "httpx"
    user_agent: str = ""
    restriction: AccessRestrictionResult = field(default_factory=AccessRestrictionResult)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").partition(";")[0].strip().lower()

    @property
    def is_html(self) -> bool:
        if self.content_type in {"text/html", "application/xhtml+xml"}:
            return True
        return not self.content_type and self.body[:200].lstrip().lower().startswith(
            (b"<!doctype html", b"<html")
        )

    def summary(self) -> dict:
        return {
            "url": self.final_url,
            "status": self.status,
            "content_type": self.content_type,
            "client": self.client,
            "user_agent": self.user_agent,
            "truncated": self.truncated,
            "redirects": self.redirects,
            "access_restriction": self.restriction.model_dump(mode="json"),
        }


def retry_delay(value: str | None) -> int | None:
    if not value or len(value) > 100:
        return None
    if value.isascii() and value.isdigit():
        return min(int(value), 10**9)
    try:
        at = parsedate_to_datetime(value)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        delta = (at - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(delta + 0.999))
    except (ValueError, TypeError, OverflowError):
        return None


def _result(
    reason: StopReason,
    confidence: float,
    evidence: list[str],
    *,
    retry_after: int | None = None,
    action: str = "stop",
) -> AccessRestrictionResult:
    return AccessRestrictionResult(
        restriction_detected=True,
        restriction_type=reason.value,
        confidence=confidence,
        evidence=evidence,
        retry_after_seconds=retry_after,
        recommended_action=action,
    )


def detect_access_restrictions(response: HTTPObservation) -> AccessRestrictionResult:
    status = response.status
    location = unquote(response.headers.get("location", "").partition("?")[0])
    redirect_auth = False
    if 300 <= status < 400:
        try:
            target = urlsplit(location)
            host = target.hostname or ""
            auth_host = bool(
                host
                and host != urlsplit(response.final_url).hostname
                and host.split(".", 1)[0] in {"accounts", "auth", "idp", "login", "sso"}
            )
            redirect_auth = bool(AUTH_PATH.search(target.path) or auth_host)
        except ValueError:
            pass
    if status == 429:
        return _result(
            StopReason.RATE_LIMITED,
            1,
            ["HTTP 429"],
            action="wait_or_stop",
            retry_after=retry_delay(response.headers.get("retry-after")),
        )
    if status in {401, 407} or redirect_auth:
        return _result(StopReason.AUTHENTICATION_REQUIRED, 1, ["Authentication status or redirect"])
    if 300 <= status < 400 and CHALLENGE_PATH.search(location):
        return _result(StopReason.BOT_PROTECTION, 0.99, ["Redirect to a bot challenge"])
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return _result(StopReason.BOT_PROTECTION, 1, ["Cloudflare challenge response header"])

    if response.is_html:
        soup = BeautifulSoup(response.body[:300_000], "html.parser")
        title = soup.title.get_text(" ", strip=True)[:200] if soup.title else ""
        text = soup.get_text(" ", strip=True)[:3500]
        human_prompt = contains_phrase(text, "human_verification")
        captcha_title = contains_phrase(title, "captcha_title")
        widget = soup.select_one(
            ".g-recaptcha, .h-captcha, .cf-turnstile, iframe[src*='recaptcha'], "
            "iframe[src*='hcaptcha'], input[name*='captcha'], #captcha, #captcha-form, "
            "form[action*='captcha']"
        )
        if (
            (
                widget
                and (
                    human_prompt
                    or status in {403, 503}
                    or captcha_title
                    or (widget.find_parent("form") and len(text) < 350)
                )
            )
            or captcha_title
            and human_prompt
        ):
            return _result(StopReason.CAPTCHA, 0.99, ["CAPTCHA widget or explicit human verification"])
        if (
            contains_phrase(title, "browser_challenge_title")
            or soup.select_one(
                "#challenge-form, #cf-challenge-running, script[src*='/cdn-cgi/challenge-platform']"
            )
            or (len(text) < 700 and contains_phrase(text, "browser_challenge_prompt"))
        ):
            return _result(StopReason.BOT_PROTECTION, 0.95, ["Browser verification title or challenge DOM"])
        password = soup.select_one("form input[type=password]")
        login_title = contains_phrase(title, "login_title")
        path = response.final_url.partition("?")[0].split("/", 3)[-1]
        login_layout = len(text) < 700 and not contains_phrase(text, "public_listing")
        if password and (login_title or AUTH_PATH.search(path) or status == 403 or login_layout):
            return _result(StopReason.AUTHENTICATION_REQUIRED, 0.98, ["Login form with password input"])
        if (
            contains_phrase(title, "access_denied_title")
            or title.casefold() in PHRASES["access_denied_exact_title"]
        ):
            return _result(StopReason.ACCESS_BLOCKED, 0.97, ["Access-denied page title"])
    if status in {403, 451}:
        return _result(StopReason.ACCESS_BLOCKED, 1, [f"HTTP {status}"])
    if status >= 500:
        return AccessRestrictionResult(
            restriction_detected=True,
            restriction_type="TEMPORARY_SERVER_FAILURE",
            confidence=1,
            evidence=[f"HTTP {status}"],
            retry_after_seconds=retry_delay(response.headers.get("retry-after")),
            recommended_action="retry_later",
        )
    return AccessRestrictionResult(evidence=["No access challenge detected"])
