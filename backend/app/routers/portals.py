import asyncio
from datetime import datetime, timezone
from http.client import HTTPException as HTTPClientError
from typing import Literal
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, HttpUrl

from app.routers.portal_listing import TenderFields, read_listing

router = APIRouter(tags=["portals"])

PortalStatus = Literal["integrating", "serving", "healing", "failed"]
RunKind = Literal["read", "repair"]
RunStatus = Literal["ok", "error"]

_READ_TIMEOUT_SECONDS = 5
_MAX_LISTING_BYTES = 5 * 1024 * 1024
_MONITOR_INTERVAL_SECONDS = 60
_REPAIR_DELAY_SECONDS = 5


class PortalCreate(BaseModel):
    address: HttpUrl


class Portal(BaseModel):
    id: int
    address: str
    status: PortalStatus
    status_code: int | None = None
    detail: str | None = None
    last_read_at: datetime | None = None
    reachable: bool | None = None
    tender_count: int = 0


class Tender(TenderFields):
    id: int
    portal_id: int


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
    tenders: list[TenderFields] | None = None


_portals: dict[int, Portal] = {}
_tenders: dict[int, list[Tender]] = {}
_runs: dict[int, list[Run]] = {}
_checking: set[int] = set()
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
    code = None
    request = Request(address, method="GET", headers={"User-Agent": "Jimix"})
    try:
        with urlopen(request, timeout=_READ_TIMEOUT_SECONDS) as response:
            code = response.status
            body = response.read(_MAX_LISTING_BYTES + 1)
            if len(body) > _MAX_LISTING_BYTES:
                return _ReadResult(ok=False, status_code=code, detail="The listing exceeds the 5 MB read limit")
            try:
                tenders = read_listing(body)
            except (KeyError, AttributeError, TypeError, RecursionError):
                return _ReadResult(ok=False, status_code=code, detail="The procurement listing has invalid data")
            except ValueError as exc:
                return _ReadResult(ok=False, status_code=code, detail=str(exc))
            return _ReadResult(
                ok=True, status_code=code, detail=f"Verified listing with {len(tenders)} open tenders", tenders=tenders
            )
    except HTTPError as exc:
        return _ReadResult(ok=False, status_code=exc.code, detail=f"Portal returned HTTP {exc.code}")
    except TimeoutError:
        return _ReadResult(ok=False, status_code=code, detail="Portal request timed out")
    except (HTTPClientError, OSError, ValueError):
        detail = "Could not read the portal response" if code is not None else "Could not reach the portal"
        return _ReadResult(ok=False, status_code=code, detail=detail)


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


def _save_result(portal: Portal, status: PortalStatus, result: _ReadResult) -> None:
    global _next_tender_id
    if result.tenders is not None:
        tenders = []
        for tender in result.tenders:
            tenders.append(Tender(id=_next_tender_id, portal_id=portal.id, **tender.model_dump()))
            _next_tender_id += 1
        _tenders[portal.id] = tenders

    _portals[portal.id] = portal.model_copy(update={
        "status": status,
        "status_code": result.status_code,
        "reachable": result.status_code is not None and 200 <= result.status_code < 300,
        "detail": result.detail,
        "last_read_at": _now() if result.ok else portal.last_read_at,
        "tender_count": len(_tenders[portal.id]),
    })


async def _check(portal_id: int) -> None:
    portal = _portals.get(portal_id)
    if portal is None or portal_id in _checking:
        return
    _checking.add(portal_id)
    try:
        result = await asyncio.to_thread(_read_address, portal.address)
        if portal_id not in _portals:
            return
        _record_run(portal_id, "read", result)
        if not result.ok and portal.last_read_at is not None:
            _save_result(portal, "healing", result)
            await asyncio.sleep(_REPAIR_DELAY_SECONDS)
            if portal_id not in _portals:
                return
            result = await asyncio.to_thread(_read_address, portal.address)
            if portal_id not in _portals:
                return
            _record_run(portal_id, "repair", result)
        _save_result(portal, "serving" if result.ok else "failed", result)
    finally:
        _checking.discard(portal_id)


async def monitor_portals() -> None:
    while True:
        await asyncio.sleep(_MONITOR_INTERVAL_SECONDS)
        await asyncio.gather(*(_check(portal_id) for portal_id in tuple(_portals)))


@router.get("/", response_model=Dashboard)
async def dashboard() -> Dashboard:
    portals = list(_portals.values())
    runs = [run for portal_runs in _runs.values() for run in portal_runs]
    runs.sort(key=lambda run: run.at, reverse=True)
    return Dashboard(
        portal_count=len(portals),
        ok_count=sum(portal.status == "serving" for portal in portals),
        error_count=sum(portal.status in {"failed", "healing"} for portal in portals),
        tender_count=sum(len(items) for items in _tenders.values()),
        portals=portals,
        recent_runs=runs[:20],
    )


@router.get("/portals", response_model=list[Portal])
async def list_portals() -> list[Portal]:
    return list(_portals.values())


@router.post("/portals", response_model=Portal, status_code=201)
async def create_portal(payload: PortalCreate, background_tasks: BackgroundTasks) -> Portal:
    global _next_portal_id
    address = str(payload.address)
    if any(portal.address == address for portal in _portals.values()):
        raise HTTPException(status_code=409, detail="Portal already exists")
    portal = Portal(id=_next_portal_id, address=address, status="integrating", detail="Checking the procurement listing")
    _next_portal_id += 1
    _portals[portal.id] = portal
    _tenders[portal.id] = []
    _runs[portal.id] = []
    background_tasks.add_task(_check, portal.id)
    return portal


@router.get("/portals/{portal_id}", response_model=Portal)
async def get_portal(portal_id: int) -> Portal:
    return _require_portal(portal_id)


@router.delete("/portals/{portal_id}", status_code=204)
async def delete_portal(portal_id: int) -> None:
    _require_portal(portal_id)
    del _portals[portal_id]
    _tenders.pop(portal_id, None)
    _runs.pop(portal_id, None)


@router.post("/portals/{portal_id}/refresh", response_model=Portal)
async def refresh_portal(portal_id: int) -> Portal:
    portal = _require_portal(portal_id)
    await _check(portal_id)
    return _require_portal(portal.id)


@router.get("/portals/{portal_id}/tenders", response_model=list[Tender])
async def list_tenders(portal_id: int) -> list[Tender]:
    _require_portal(portal_id)
    return _tenders.get(portal_id, [])


@router.get("/portals/{portal_id}/runs", response_model=list[Run])
async def list_runs(portal_id: int) -> list[Run]:
    _require_portal(portal_id)
    return _runs.get(portal_id, [])
