from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, HttpUrl

from app.database import connect, initialize_database
from app.jobs import refresh_jobs

router = APIRouter(tags=["portals"])
PortalState = Literal["integrating", "serving", "healing", "failed"]


class PortalCreate(BaseModel):
    url: HttpUrl


class Portal(BaseModel):
    id: int
    url: str
    state: PortalState
    reason: str | None
    tenderCount: int
    lastSuccessAt: datetime | None


class Run(BaseModel):
    runId: int
    portalId: int
    status: Literal["running", "succeeded", "failed"]
    createdAt: datetime
    finishedAt: datetime | None = None
    detail: str | None = None


class Tender(BaseModel):
    id: int
    portalId: int
    title: str
    reference: str | None = None
    url: str | None = None


def _portal(row) -> Portal:
    return Portal(id=row["id"], url=row["url"], state=row["state"], reason=row["reason"],
                  tenderCount=row["tender_count"], lastSuccessAt=row["last_success_at"])


def _require_portal(db, portal_id: int):
    row = db.execute("SELECT * FROM portals WHERE id = ?", (portal_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Portal not found")
    return row


@router.on_event("startup")
def startup() -> None:
    initialize_database()


@router.post("/portals", response_model=Portal, status_code=201)
def create_portal(payload: PortalCreate) -> Portal:
    url = str(payload.url)
    with connect() as db:
        row = db.execute("SELECT * FROM portals WHERE url = ?", (url,)).fetchone()
        if row:
            return _portal(row)
        cursor = db.execute("INSERT INTO portals(url, state, created_at) VALUES (?, 'integrating', ?)",
                            (url, datetime.now(timezone.utc).isoformat()))
        return _portal(db.execute("SELECT * FROM portals WHERE id = ?", (cursor.lastrowid,)).fetchone())


@router.get("/portals", response_model=list[Portal])
def list_portals() -> list[Portal]:
    with connect() as db:
        return [_portal(row) for row in db.execute("SELECT * FROM portals ORDER BY id")]


@router.get("/portals/{portal_id}", response_model=Portal)
def get_portal(portal_id: int) -> Portal:
    with connect() as db:
        return _portal(_require_portal(db, portal_id))


@router.delete("/portals/{portal_id}", status_code=204)
def delete_portal(portal_id: int) -> Response:
    with connect() as db:
        _require_portal(db, portal_id)
        db.execute("DELETE FROM portals WHERE id = ?", (portal_id,))
    return Response(status_code=204)


@router.post("/portals/{portal_id}/refresh", response_model=Run, status_code=202)
def refresh_portal(portal_id: int) -> Run:
    initialize_database()
    with connect() as db:
        _require_portal(db, portal_id)
        existing = db.execute("SELECT * FROM runs WHERE portal_id = ? AND status = 'running' ORDER BY id LIMIT 1",
                              (portal_id,)).fetchone()
        if existing:
            return Run(runId=existing["id"], portalId=portal_id, status=existing["status"],
                       createdAt=existing["created_at"], finishedAt=existing["finished_at"], detail=existing["detail"])
        now = datetime.now(timezone.utc).isoformat()
        cursor = db.execute("INSERT INTO runs(portal_id, status, created_at) VALUES (?, 'running', ?)", (portal_id, now))
        run_id = cursor.lastrowid
        db.execute("UPDATE portals SET state = 'healing', reason = NULL WHERE id = ?", (portal_id,))
    refresh_jobs.enqueue(portal_id, run_id)
    return Run(runId=run_id, portalId=portal_id, status="running", createdAt=now)


@router.get("/portals/{portal_id}/runs", response_model=list[Run])
def list_runs(portal_id: int) -> list[Run]:
    with connect() as db:
        _require_portal(db, portal_id)
        return [Run(runId=r["id"], portalId=r["portal_id"], status=r["status"], createdAt=r["created_at"],
                    finishedAt=r["finished_at"], detail=r["detail"])
                for r in db.execute("SELECT * FROM runs WHERE portal_id = ? ORDER BY id DESC", (portal_id,))]


@router.get("/portals/{portal_id}/tenders", response_model=list[Tender])
def list_tenders(portal_id: int) -> list[Tender]:
    with connect() as db:
        _require_portal(db, portal_id)
        return [Tender(id=r["id"], portalId=r["portal_id"], title=r["title"], reference=r["reference"], url=r["url"])
                for r in db.execute("SELECT * FROM tenders WHERE portal_id = ? ORDER BY id", (portal_id,))]
