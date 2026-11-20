import re
from dataclasses import dataclass, field

from protego import Protego

from .models import RobotsDecision
from .urls import candidate_url

_ROBOTS_HEADER = re.compile(r"^(?:user-agent|sitemap)[ \t]*:[ \t]*\S", re.IGNORECASE | re.MULTILINE)


@dataclass
class RobotsPolicy:
    url: str
    state: str
    evidence: list[str]
    parser: Protego | None = None
    sitemaps: list[str] = field(default_factory=list)

    def evaluate(self, url: str, user_agent: str, allow_ambiguous: bool) -> RobotsDecision:
        evidence = self.evidence.copy()
        if self.parser is None:
            return RobotsDecision(
                allowed=self.state == "absent" or allow_ambiguous,
                status=self.state,
                robots_url=self.url,
                evidence=evidence,
            )

        allowed = self.parser.can_fetch(url, user_agent)
        delay = self.parser.crawl_delay(user_agent)
        if rate := self.parser.request_rate(user_agent):
            try:
                delay = max(delay or 0, rate.seconds / rate.requests)
            except OverflowError:
                return RobotsDecision(
                    allowed=allow_ambiguous,
                    status="malformed",
                    robots_url=self.url,
                    evidence=evidence + ["robots.txt request rate exceeds the supported range"],
                )
        evidence.append(f"Applicable robots policy {'allows' if allowed else 'disallows'} this path")
        if delay is not None:
            evidence.append(f"Applicable robots request interval is {delay:g}s")

        return RobotsDecision(
            allowed=allowed,
            status="allowed" if allowed else "disallowed",
            robots_url=self.url,
            crawl_delay_seconds=delay,
            sitemaps=self.sitemaps,
            evidence=evidence,
        )


def parse_robots(url: str, body: bytes, truncated: bool) -> RobotsPolicy:
    malformed = RobotsPolicy(url, "malformed", ["robots.txt is malformed or exceeds the read limit"])
    if truncated:
        return malformed
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return malformed

    # Protego ignores unknown lines. Treat an unrelated response body as ambiguous.
    content = "\n".join(line.partition("#")[0].strip() for line in text.splitlines()).strip()
    if "\x00" in text or content.startswith("<") or (content and not _ROBOTS_HEADER.search(content)):
        return malformed
    parser = Protego.parse(text)
    sitemaps: dict[str, None] = {}
    for value in parser.sitemaps:
        if target := candidate_url(url, value):
            sitemaps[target] = None
        if len(sitemaps) == 12:
            break
    return RobotsPolicy(url, "allowed", ["robots.txt policy parsed"], parser, list(sitemaps))


async def check_robots(url: str, user_agent: str, context) -> RobotsDecision:
    return await context.transport.check_robots(url, user_agent)
