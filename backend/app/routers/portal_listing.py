import json
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, HttpUrl, TypeAdapter

_HTTP_URL = TypeAdapter(HttpUrl)
_DATETIME = TypeAdapter(datetime)


class Document(BaseModel):
    fileName: str
    url: HttpUrl


class TenderFields(BaseModel):
    title: str
    reference: str | None = None
    address: HttpUrl | None = None
    url: HttpUrl | None = None
    buyerName: str | None = None
    submissionOpensAt: datetime | None = None
    submissionDeadline: datetime | None = None
    lastChangedAt: datetime | None = None
    status: Literal["open"] = "open"
    documents: list[Document] = Field(default_factory=list)


def _period_date(period: dict, field: str) -> datetime | None:
    value = period.get(field)
    if value is None:
        return None
    timestamp = _DATETIME.validate_python(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"A tender {field} is missing its time zone")
    return timestamp


def read_listing(body: bytes) -> list[TenderFields]:
    """Read a single OCDS release or record package."""
    try:
        package = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("No OCDS procurement listing found at this URL") from exc

    if (
        not isinstance(package, dict)
        or package.get("version") != "1.1"
        or not isinstance(package.get("publisher"), dict)
        or not package["publisher"].get("name")
        or not package.get("publishedDate")
    ):
        raise ValueError("No OCDS 1.1 procurement listing found at this URL")
    if (package.get("links") or {}).get("next"):
        raise ValueError("This listing spans multiple pages. A single-package feed is required.")

    if "records" in package:
        releases = [record["compiledRelease"] for record in package["records"]]
    else:
        releases = package["releases"]
    if not isinstance(releases, list):
        raise ValueError("The OCDS package has an invalid release list")

    latest = {}
    for release in releases:
        ocid = release["ocid"]
        published = _DATETIME.validate_python(release["date"])
        if published.tzinfo is None or not isinstance(ocid, str) or not ocid:
            raise ValueError("The OCDS package contains an invalid release")
        if "tender" in release and (ocid not in latest or published >= latest[ocid][0]):
            latest[ocid] = published, release

    tenders = []
    now = datetime.now(timezone.utc)
    for published, release in latest.values():
        tender = release["tender"]
        status = tender["status"]
        if status not in {"planning", "planned", "active", "cancelled", "unsuccessful", "complete", "withdrawn"}:
            raise ValueError("The OCDS package contains an unrecognized tender status")
        if status != "active":
            continue
        period = tender.get("tenderPeriod") or {}
        deadline = _period_date(period, "endDate")
        if deadline is not None and deadline <= now:
            continue

        documents = []
        address = None
        for document in tender.get("documents") or []:
            url = document.get("url")
            if url and urlsplit(url).scheme in {"http", "https"}:
                link = _HTTP_URL.validate_python(url)
                documents.append(Document(fileName=document.get("title") or document["id"], url=link))
                if address is None and document.get("documentType") == "tenderNotice":
                    address = link
        buyer = release.get("buyer") or {}
        buyer_name = buyer.get("name")
        if not buyer_name:
            buyer_name = next((
                party.get("name") for party in release.get("parties") or []
                if party.get("name") and (
                    (buyer.get("id") is not None and party.get("id") == buyer["id"])
                    or (buyer.get("id") is None and "buyer" in (party.get("roles") or []))
                )
            ), None)
        tenders.append(
            TenderFields(
                title=tender["title"],
                reference=tender["id"],
                address=address,
                url=address,
                buyerName=buyer_name,
                submissionOpensAt=_period_date(period, "startDate"),
                submissionDeadline=deadline,
                lastChangedAt=published,
                documents=documents,
            )
        )
    return tenders
