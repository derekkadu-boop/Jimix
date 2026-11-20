import json
import logging
import sqlite3
import uuid
from contextlib import asynccontextmanager, closing
from dataclasses import asdict
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app import db
from app.registration import RegistrationLocation, lookup_registration


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
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
    with closing(db.connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO portal_location (portal_id, city, country, country_code, latitude, longitude)
            SELECT id, :city, :country, :country_code, :latitude, :longitude
            FROM portal WHERE id = :portal_id
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
def create_portal(body: PortalIn, conn: Conn, background_tasks: BackgroundTasks):
    portal_id = uuid.uuid4().hex
    with conn:
        inserted = conn.execute(
            "INSERT INTO portal (id, url, created_at) VALUES (?, ?, ?) ON CONFLICT(url) DO NOTHING",
            (portal_id, body.url, db.now()),
        ).rowcount
        if not inserted:
            row = conn.execute("SELECT id FROM portal WHERE url = ?", (body.url,)).fetchone()
            return {"id": row["id"]}
    background_tasks.add_task(enrich_portal_location, portal_id, body.url)
    return {"id": portal_id}


def portal_out(row: sqlite3.Row) -> dict:
    portal = dict(row)
    location = {key: portal.pop(key) for key in ("city", "country", "countryCode", "latitude", "longitude")}
    if location["city"] or location["country"]:
        portal["location"] = location
    return portal


@app.get("/portals")
def list_portals(conn: Conn):
    return [portal_out(r) for r in conn.execute(PORTAL_SELECT + "ORDER BY p.created_at")]


@app.get("/portals/{portal_id}")
def get_portal(portal_id: str, conn: Conn):
    row = conn.execute(PORTAL_SELECT + "WHERE p.id = ?", (portal_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Portal not found")
    return portal_out(row)
    # TODO  Your system stops calling the portal within a minute.

@app.delete("/portals/{portal_id}", status_code=204, response_class=Response)
def delete_portal(portal_id: str, conn: Conn) -> Response:
    with conn:
        deleted = conn.execute("DELETE FROM portal WHERE id = ?", (portal_id,)).rowcount
        if not deleted:
            raise HTTPException(404, "Portal not found")
    return Response(status_code=204)


@app.delete("/portals/{portal_id}", status_code=204)
def delete_portal(portal_id: str, conn: Conn):
    with conn:
        deleted = conn.execute("DELETE FROM portal WHERE id = ?", (portal_id,)).rowcount
        if not deleted:
            raise HTTPException(404, "Portal not found")


TENDER_OPTIONAL = (
    "referenceNumber",
    "description",
    "procedureType",
    "contractNature",
    "cpvCodes",
    "placeOfPerformance",
)


def tender_out(row: sqlite3.Row) -> dict:
    t = dict(row)
    for key in ("cpvCodes", "placeOfPerformance"):
        if t[key] is not None:
            t[key] = json.loads(t[key])
    t["documents"] = []
    return {k: v for k, v in t.items() if v is not None or k not in TENDER_OPTIONAL}


@app.get("/portals/{portal_id}/tenders")
def list_tenders(portal_id: str, conn: Conn, changedSince: str | None = None):
    require_portal(conn, portal_id)
    rows = conn.execute(
        """
        SELECT id, url, title, buyer_name AS buyerName, publication_date AS publicationDate,
               submission_deadline AS submissionDeadline, status,
               reference_number AS referenceNumber, description,
               procedure_type AS procedureType, contract_nature AS contractNature,
               cpv_codes AS cpvCodes, place_of_performance AS placeOfPerformance,
               last_changed_at AS lastChangedAt
        FROM tender
        WHERE portal_id = ? AND (? IS NULL OR julianday(last_changed_at) > julianday(?))
        ORDER BY publication_date DESC
        """,
        (portal_id, changedSince, changedSince),
    )
    return [tender_out(r) for r in rows]


@app.get("/portals/{portal_id}/runs")
def list_runs(portal_id: str, conn: Conn):
    require_portal(conn, portal_id)
    rows = conn.execute(
        "SELECT id, kind, started_at AS startedAt, finished_at AS finishedAt, result"
        " FROM run WHERE portal_id = ? ORDER BY started_at DESC",
        (portal_id,),
    )
    return [dict(r) for r in rows]
