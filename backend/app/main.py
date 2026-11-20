import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from app import db


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    yield


app = FastAPI(title="Jimix", lifespan=lifespan)
Conn = Annotated[sqlite3.Connection, Depends(db.get_db)]

PORTAL_SELECT = """
SELECT p.id, p.url, p.state, p.reason,
       (SELECT COUNT(*) FROM tender t WHERE t.portal_id = p.id) AS tenderCount,
       (SELECT MAX(r.finished_at) FROM run r
         WHERE r.portal_id = p.id AND r.result = 'ok') AS lastSuccessAt
FROM portal p
"""


class PortalIn(BaseModel):
    url: str


def require_portal(conn: sqlite3.Connection, portal_id: str) -> None:
    if not conn.execute("SELECT 1 FROM portal WHERE id = ?", (portal_id,)).fetchone():
        raise HTTPException(404, "Portal not found")


@app.post("/portals", status_code=201)
def create_portal(body: PortalIn, conn: Conn):
    conn.execute(
        "INSERT OR IGNORE INTO portal (id, url, created_at) VALUES (?, ?, ?)",
        (uuid.uuid4().hex, body.url, db.now()),
    )
    row = conn.execute("SELECT id FROM portal WHERE url = ?", (body.url,)).fetchone()
    return {"id": row["id"]}


@app.get("/portals")
def list_portals(conn: Conn):
    return [dict(r) for r in conn.execute(PORTAL_SELECT + "ORDER BY p.created_at")]


@app.get("/portals/{portal_id}")
def get_portal(portal_id: str, conn: Conn):
    row = conn.execute(PORTAL_SELECT + "WHERE p.id = ?", (portal_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Portal not found")
    return dict(row)


TENDER_OPTIONAL = ("referenceNumber", "description", "procedureType", "contractNature",
                   "cpvCodes", "placeOfPerformance")


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
