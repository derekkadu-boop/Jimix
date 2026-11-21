import asyncio
import hashlib
import logging
import os
import shutil
import uuid
import zipfile
from pathlib import Path

import httpx

from .fetch import MAX_FILE_BYTES, MAX_RUN_DOWNLOAD_BYTES, Blocked, BudgetExceeded, DocumentSkipped, Fetcher

log = logging.getLogger("watchtower.documents")
FILES_DIR = Path(os.environ.get("JIMIX_FILES", "data/files"))
MAX_DOCUMENTS = int(os.environ.get("WATCHTOWER_MAX_DOCS_PER_TENDER", "30"))
OFFICE_CONTENT_TYPES = "[Content_Types].xml"
OFFICE_PACKAGE_MARKERS = {OFFICE_CONTENT_TYPES, "_rels/.rels"}
OFFICE_MAIN_PARTS = {"word/document.xml", "xl/workbook.xml", "xl/workbook.bin", "ppt/presentation.xml"}
OFFICE_MAIN_NAMES = {Path(part).name for part in OFFICE_MAIN_PARTS}


def _store(part: Path, name: str) -> dict:
    digest = hashlib.sha256()
    with part.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    sha = digest.hexdigest()
    target = FILES_DIR / sha
    if target.exists():
        part.unlink()
    else:
        part.replace(target)
    log.info("stored %s as %s", name, sha)
    return {"fileName": name, "sizeBytes": target.stat().st_size, "sha256": sha, "path": f"/files/{sha}"}


def _unzip(archive: Path, budget: int) -> tuple[list[dict], int]:
    documents, unpacked = [], 0
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir() or info.file_size > MAX_FILE_BYTES:
                continue
            if unpacked + info.file_size > budget:
                archive.unlink()
                raise BudgetExceeded
            unpacked += info.file_size
            part = FILES_DIR / f".part-{uuid.uuid4().hex}"
            try:
                with zf.open(info) as src, part.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            except (RuntimeError, NotImplementedError, zipfile.BadZipFile):
                part.unlink(missing_ok=True)
                continue
            documents.append(_store(part, Path(info.filename).name))
    archive.unlink()
    return documents, unpacked


def _save(part: Path, name: str, budget: int) -> tuple[list[dict], int]:
    if zipfile.is_zipfile(part):
        with zipfile.ZipFile(part) as archive:
            members = set(archive.namelist())
        if not OFFICE_PACKAGE_MARKERS <= members or OFFICE_MAIN_PARTS.isdisjoint(members):
            return _unzip(part, budget)
    return [_store(part, name)], 0


def has_unpacked_office(documents: list[dict]) -> bool:
    names = {doc["fileName"] for doc in documents}
    return OFFICE_CONTENT_TYPES in names and not OFFICE_MAIN_NAMES.isdisjoint(names)


async def fetch_documents(
    urls: list[str], fetcher: Fetcher, *, skipped: dict[str, str] | None = None
) -> tuple[list[dict], bool, str | None]:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    documents, complete, blocked = [], True, None
    for url in urls[:MAX_DOCUMENTS]:
        if skipped is not None and url in skipped:
            continue
        try:
            downloaded = await fetcher.download(url, FILES_DIR)
            budget = MAX_RUN_DOWNLOAD_BYTES - fetcher.downloaded
            saved, unpacked = await asyncio.to_thread(_save, *downloaded, budget)
            for doc in saved:
                if all(doc["sha256"] != known["sha256"] for known in documents):
                    documents.append(doc)
            fetcher.downloaded += unpacked
        except BudgetExceeded:
            return documents, False, None
        except DocumentSkipped as exc:
            reason = str(exc)
            log.info("document skipped: %s", reason)
            if skipped is not None:
                skipped[url] = reason
            complete = complete and not exc.retryable
        except Blocked as exc:
            complete, blocked = False, str(exc)
        except (httpx.HTTPError, ValueError, OSError, zipfile.BadZipFile):
            complete = False
    return documents, complete, blocked
