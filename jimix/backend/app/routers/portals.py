from datetime import datetime, timezone
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl

router = APIRouter(tags=["portals"])

PortalStatus = Literal["unknown", "ok", "error"]
RunKind = Literal["read", "repair"]
RunStatus = Literal["ok", "error"]

_READ_TIMEOUT_SECONDS = 5


class PortalCreate(BaseModel):
    address: HttpUrl


class Portal(BaseModel):
    id: int
    address: str
    status: PortalStatus
    status_code: int | None = None
    detail: str | None = None
    last_read_at: datetime | None = None


class Tender(BaseModel):
    id: int
    portal_id: int
    title: str
    reference: str | None = None
    url: str | None = None


class Run(BaseModel):
    id: int
    portal_id: int
    kind: RunKind
    status: RunStatus
    detail: str
    at: datetime


class Dashboard(BaseModel):
    portal_count: int
    ok_count: int
    error_count: int
    tender_count: int
    portals: list[Portal]
    recent_runs: list[Run]


class _ReadResult(BaseModel):
    ok: bool
    status_code: int | None
    detail: str


_portals: dict[int, Portal] = {}
_tenders: dict[int, list[Tender]] = {}
_runs: dict[int, list[Run]] = {}
_next_portal_id = 1
_next_tender_id = 1
_next_run_id = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_portal(portal_id: int) -> Portal:
    portal = _portals.get(portal_id)
    if portal is None:
        raise HTTPException(status_code=404, detail="Portal not found")
    return portal


def _read_address(address: str) -> _ReadResult:
    request = Request(address, method="GET", headers={"User-Agent": "Jimix"})
    try:
        with urlopen(request, timeout=_READ_TIMEOUT_SECONDS) as response:
            code = response.status
            if code < 400:
                return _ReadResult(ok=True, status_code=code, detail="Read portal")
            return _ReadResult(ok=False, status_code=code, detail=f"Portal returned {code}")
    except HTTPError as exc:
        return _ReadResult(ok=False, status_code=exc.code, detail=f"Portal returned {exc.code}")
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        return _ReadResult(ok=False, status_code=None, detail=str(reason))
    except TimeoutError:
        return _ReadResult(ok=False, status_code=None, detail="Timed out")


def _record_run(portal_id: int, kind: RunKind, result: _ReadResult) -> Run:
    global _next_run_id
    run = Run(
        id=_next_run_id,
        portal_id=portal_id,
        kind=kind,
        status="ok" if result.ok else "error",
        detail=result.detail,
        at=_now(),
    )
    _next_run_id += 1
    _runs.setdefault(portal_id, []).append(run)
    return run


def _apply_result(portal: Portal, result: _ReadResult) -> Portal:
    updated = portal.model_copy(
        update={
            "status": "ok" if result.ok else "error",
            "status_code": result.status_code,
            "detail": result.detail,
            "last_read_at": _now(),
        }
    )
    _portals[portal.id] = updated
    return updated


def _refresh(portal: Portal) -> Portal:
    result = _read_address(portal.address)
    _record_run(portal.id, "read", result)
    if not result.ok:
        result = _read_address(portal.address)
        _record_run(portal.id, "repair", result)
    return _apply_result(portal, result)


@router.get("/", response_model=Dashboard)
def dashboard() -> Dashboard:
    portals = list(_portals.values())
    runs = [run for portal_runs in _runs.values() for run in portal_runs]
    runs.sort(key=lambda run: run.at, reverse=True)
    return Dashboard(
        portal_count=len(portals),
        ok_count=sum(portal.status == "ok" for portal in portals),
        error_count=sum(portal.status == "error" for portal in portals),
        tender_count=sum(len(items) for items in _tenders.values()),
        portals=portals,
        recent_runs=runs[:20],
    )


@router.get("/portals", response_model=list[Portal])
def list_portals() -> list[Portal]:
    return list(_portals.values())


@router.post("/portals", response_model=Portal, status_code=201)
def create_portal(payload: PortalCreate) -> Portal:
    global _next_portal_id
    address = str(payload.address)
    if any(portal.address == address for portal in _portals.values()):
        raise HTTPException(status_code=409, detail="Portal already exists")
    portal = Portal(id=_next_portal_id, address=address, status="unknown")
    _next_portal_id += 1
    _portals[portal.id] = portal
    _tenders[portal.id] = []
    _runs[portal.id] = []
    return portal


@router.get("/portals/{portal_id}", response_model=Portal)
def get_portal(portal_id: int) -> Portal:
    return _require_portal(portal_id)


@router.delete("/portals/{portal_id}", status_code=204)
def delete_portal(portal_id: int) -> None:
    _require_portal(portal_id)
    del _portals[portal_id]
    _tenders.pop(portal_id, None)
    _runs.pop(portal_id, None)


@router.post("/portals/{portal_id}/refresh", response_model=Portal)
def refresh_portal(portal_id: int) -> Portal:
    return _refresh(_require_portal(portal_id))


@router.get("/portals/{portal_id}/tenders", response_model=list[Tender])
def list_tenders(portal_id: int) -> list[Tender]:
    _require_portal(portal_id)
    return _tenders.get(portal_id, [])


@router.get("/portals/{portal_id}/runs", response_model=list[Run])
def list_runs(portal_id: int) -> list[Run]:
    _require_portal(portal_id)
    return _runs.get(portal_id, [])
