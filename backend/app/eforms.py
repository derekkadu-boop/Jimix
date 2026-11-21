import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as XML
from zipfile import BadZipFile, ZipFile

MAX_XML_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_XML_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 20_000
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS = {"cbc": CBC, "cac": CAC}
CONTRACT_NOTICE = "{urn:oasis:names:specification:ubl:schema:xsd:ContractNotice-2}ContractNotice"


class _NoticeTree(XML.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError("eForms notices must not contain a DTD")


def _text(node, path: str) -> str:
    return (node.findtext(path, namespaces=NS) or "").strip()


def _public_link(value: str) -> bool:
    try:
        url = urlsplit(value)
        return url.scheme in {"https", "http"} and bool(url.hostname) and url.username is None
    except ValueError:
        return False


def _deadline(period):
    date = _text(period, "cbc:EndDate")
    clock = _text(period, "cbc:EndTime")
    if not date or not clock:
        return None
    offset = date[10:]
    if offset and not re.search(r"(Z|[+-]\d\d:\d\d)$", clock):
        clock += offset
    try:
        value = datetime.fromisoformat(f"{date[:10]}T{clock}")
    except ValueError:
        return None
    return value if value.tzinfo else None


def read_notices(path: Path, listing_url: str, publication_date: str) -> list[dict]:
    latest: dict[str, tuple[tuple[str, int], dict]] = {}
    source = urlsplit(listing_url)
    origin = f"{source.scheme}://{source.netloc}"
    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ENTRIES:
                raise ValueError("eForms archive has too many entries")
            files = [m for m in members if not m.is_dir() and m.filename.lower().endswith(".xml")]
            if members and not files:
                raise ValueError("eForms archive has no XML notices")
            if any(m.file_size > MAX_XML_BYTES for m in files) or sum(
                m.file_size for m in files
            ) > MAX_ARCHIVE_XML_BYTES:
                raise ValueError("eForms archive exceeds XML size limits")
            for member in files:
                root = XML.fromstring(archive.read(member), parser=XML.XMLParser(target=_NoticeTree()))
                if root.tag != CONTRACT_NOTICE:
                    continue
                notice_id = _text(root, "cbc:ID")
                if not notice_id:
                    continue
                version = _text(root, "cbc:VersionID")
                issue = _text(root, "cbc:IssueDate")
                rank = publication_date + issue, int(version) if version.isdecimal() and len(version) <= 6 else 0
                if notice_id in latest and rank <= latest[notice_id][0]:
                    continue
                project = root.find("cac:ProcurementProject", NS)
                title = _text(project, "cbc:Name") if project is not None else ""
                deadline = max(
                    (d for p in root.findall(".//cac:TenderSubmissionDeadlinePeriod", NS)
                     if (d := _deadline(p)) is not None),
                    default=None,
                )
                buyer_ref = _text(root, "cac:ContractingParty/cac:Party/cac:PartyIdentification/cbc:ID")
                buyer = _text(root, "cac:ContractingParty/cac:Party/cac:PartyName/cbc:Name")
                if not buyer and buyer_ref:
                    for org in root.findall(".//{*}Organization"):
                        party = org.find("{*}Company")
                        if party is not None and _text(party, "cac:PartyIdentification/cbc:ID") == buyer_ref:
                            buyer = _text(party, "cac:PartyName/cbc:Name")
                            break
                docs = [
                    element.text.strip()
                    for element in root.findall(
                        ".//cac:CallForTendersDocumentReference/cac:Attachment/"
                        "cac:ExternalReference/cbc:URI", NS
                    )
                    if element.text and _public_link(element.text.strip())
                ]
                notice_url = f"{origin}/ui/en/search/details?noticeId={quote(notice_id, safe='')}"
                latest[notice_id] = rank, {
                    "id": notice_id,
                    "url": notice_url,
                    "title": title or None,
                    "buyerName": buyer or None,
                    "publicationDate": publication_date,
                    "submissionDeadline": deadline.isoformat() if deadline else None,
                    "status": "closed" if deadline and deadline < datetime.now(UTC) else "open",
                    "documents": [],
                    "_documentUrls": list(dict.fromkeys(docs)),
                }
    except (BadZipFile, XML.ParseError, NotImplementedError, RuntimeError) as exc:
        raise ValueError("invalid eForms export archive") from exc
    return [notice for _, notice in latest.values()]
