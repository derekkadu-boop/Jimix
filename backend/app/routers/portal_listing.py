import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from pydantic import BaseModel, HttpUrl, TypeAdapter

_HTTP_URL = TypeAdapter(HttpUrl)
_DATETIME = TypeAdapter(datetime)


class Document(BaseModel):
    fileName: str
    url: HttpUrl


class ListedTender(BaseModel):
    title: str
    reference: str
    submissionDeadline: datetime | None = None
    documents: list[Document]


def read_listing(body: bytes) -> list[ListedTender]:
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
            latest[ocid] = published, release["tender"]

    tenders = []
    now = datetime.now(timezone.utc)
    for _, tender in latest.values():
        status = tender["status"]
        if status not in {"planning", "planned", "active", "cancelled", "unsuccessful", "complete", "withdrawn"}:
            raise ValueError("The OCDS package contains an unrecognized tender status")
        if status != "active":
            continue
        deadline = (tender.get("tenderPeriod") or {}).get("endDate")
        if deadline is not None:
            deadline = _DATETIME.validate_python(deadline)
            if deadline.tzinfo is None:
                raise ValueError("A tender deadline is missing its time zone")
            if deadline <= now:
                continue

        documents = []
        for document in tender.get("documents") or []:
            url = document.get("url")
            if url and urlsplit(url).scheme in {"http", "https"}:
                documents.append(
                    Document(fileName=document.get("title") or document["id"], url=_HTTP_URL.validate_python(url))
                )
        tenders.append(
            ListedTender(
                title=tender["title"],
                reference=tender["id"],
                submissionDeadline=deadline,
                documents=documents,
            )
        )
    return tenders
