import json
import logging
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import db, watchtower
from app.documents import FILES_DIR
from app.registration import RegistrationLocation, lookup_registration


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    db.init()
    watchtower.recover()
    yield


app = FastAPI(title="Jimix", lifespan=lifespan)
logger = logging.getLogger(__name__)
Conn = Annotated[sqlite3.Connection, Depends(db.get_db)]

PORTAL_SELECT = """
SELECT p.id, p.url, p.state, p.reason,
       l.city, l.country, l.country_code AS countryCode, l.latitude, l.longitude,
       (SELECT COUNT(*) FROM tender t WHERE t.portal_id = p.id) AS tenderCount,
       (SELECT MAX(r.finished_at) FROM run r
         WHERE r.portal_id = p.id AND r.result = 'ok') AS lastSuccessAt
FROM portal p
LEFT JOIN portal_location l ON l.portal_id = p.id
"""


class PortalIn(BaseModel):
    url: str


def require_portal(conn: sqlite3.Connection, portal_id: str) -> None:
    if not conn.execute("SELECT 1 FROM portal WHERE id = ?", (portal_id,)).fetchone():
        raise HTTPException(404, "Portal not found")


def save_portal_location(portal_id: str, location: RegistrationLocation) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO portal_location (portal_id, city, country, country_code, latitude, longitude)
            SELECT id, :city, :country, :country_code, :latitude, :longitude
            FROM portal WHERE id = :portal_id
            ON CONFLICT(portal_id) DO UPDATE SET
                city = excluded.city,
                country = excluded.country,
                country_code = excluded.country_code,
                latitude = excluded.latitude,
                longitude = excluded.longitude
            """,
            {"portal_id": portal_id, **asdict(location)},
        )


async def enrich_portal_location(portal_id: str, url: str) -> None:
    location = await lookup_registration(url)
    if location is not None:
        try:
            await run_in_threadpool(save_portal_location, portal_id, location)
        except sqlite3.Error:
            logger.warning("Could not save registration location for portal %s", portal_id, exc_info=True)


@app.post("/portals", status_code=201)
async def create_portal(body: PortalIn, conn: Conn, background_tasks: BackgroundTasks):
    inserted = conn.execute(
        "INSERT OR IGNORE INTO portal (id, url, created_at) VALUES (?, ?, ?)",
        (uuid.uuid4().hex, body.url, db.now()),
    ).rowcount
    conn.commit()
    portal_id = conn.execute("SELECT id FROM portal WHERE url = ?", (body.url,)).fetchone()["id"]
    if inserted:
        watchtower.start(portal_id)
        background_tasks.add_task(enrich_portal_location, portal_id, body.url)
    return {"id": portal_id}


def portal_out(row: sqlite3.Row) -> dict:
    portal = dict(row)
    location = {key: portal.pop(key) for key in ("city", "country", "countryCode", "latitude", "longitude")}
    if location["city"] or location["country"]:
        portal["location"] = location
    return portal


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/portals")
def list_portals(conn: Conn):
    return [portal_out(r) for r in conn.execute(PORTAL_SELECT + "ORDER BY p.created_at")]


@app.get("/portals/{portal_id}")
def get_portal(portal_id: str, conn: Conn):
    row = conn.execute(PORTAL_SELECT + "WHERE p.id = ?", (portal_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Portal not found")
    return portal_out(row)


@app.delete("/portals/{portal_id}", status_code=204)
async def delete_portal(portal_id: str, conn: Conn):
    require_portal(conn, portal_id)
    conn.execute("DELETE FROM portal WHERE id = ?", (portal_id,))
    conn.commit()
    return Response(status_code=204)


@app.post("/portals/{portal_id}/refresh", status_code=202)
async def refresh_portal(portal_id: str, conn: Conn, background_tasks: BackgroundTasks):
    row = conn.execute("SELECT url FROM portal WHERE id = ?", (portal_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Portal not found")
    background_tasks.add_task(enrich_portal_location, portal_id, row["url"])
    return {"runId": watchtower.start(portal_id)}


@app.get("/portals/{portal_id}/tenders")
def list_tenders(portal_id: str, conn: Conn, changedSince: str | None = None):
    require_portal(conn, portal_id)
    if changedSince is not None:
        try:
            since = datetime.fromisoformat(re.sub(r" (\d\d:?\d\d)$", r"+\1", changedSince))
        except ValueError:
            raise HTTPException(422, "changedSince must be a date and time with offset") from None
        if since.tzinfo is None:
            raise HTTPException(422, "changedSince must include an offset")
        changedSince = since.isoformat()
    rows = conn.execute(
        """
        SELECT data, last_changed_at FROM tender
        WHERE portal_id = ? AND (? IS NULL OR julianday(last_changed_at) > julianday(?))
        ORDER BY json_extract(data, '$.publicationDate') DESC
        """,
        (portal_id, changedSince, changedSince),
    )
    tenders = []
    for row in rows:
        stored = json.loads(row["data"])
        tender = {k: v for k, v in stored.items() if not k.startswith("_")}
        tender["lastChangedAt"] = row["last_changed_at"]
        if "_documentsComplete" in stored:
            if not stored["_documentsComplete"]:
                tender["documentsStatus"] = "pending"
            elif tender.get("documents"):
                tender["documentsStatus"] = "complete"
            elif stored.get("_documentUrls"):
                tender["documentsStatus"] = "unavailable"
            else:
                tender["documentsStatus"] = "none"
        tenders.append(tender)
    return tenders


@app.get("/portals/{portal_id}/runs")
def list_runs(portal_id: str, conn: Conn):
    require_portal(conn, portal_id)
    rows = conn.execute(
        """
        SELECT id, kind, started_at AS startedAt, finished_at AS finishedAt, result, reason,
               llm_calls AS llmCalls, input_tokens AS inputTokens, output_tokens AS outputTokens,
               cost_usd AS costUsd
        FROM run WHERE portal_id = ? ORDER BY started_at DESC
        """,
        (portal_id,),
    )
    return [dict(r) for r in rows]


@app.get("/files/{sha256}")
def get_file(sha256: str):
    path = FILES_DIR / sha256
    if not re.fullmatch(r"[0-9a-f]{64}", sha256) or not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path)
