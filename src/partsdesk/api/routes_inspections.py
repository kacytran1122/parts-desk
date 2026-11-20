"""Inspection worksheet routes: drill fixtures, uploads, confirmation, evidence, export."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from partsdesk.api.auth import require_operator
from partsdesk.api.deps import (CaptureId, InspectionId, get_calls, get_service, get_settings, limit_writes,
                                mode)
from partsdesk.api.schemas import Confirmation, FixtureRequest, NewInspection
from partsdesk.config import Settings
from partsdesk.domain import CaptureKind, CaptureSource, Identifiers
from partsdesk.live.manager import LiveCalls
from partsdesk.report import to_json
from partsdesk.service import InspectionService
from partsdesk.speech.prompts import CERTIFICATE_REQUEST, CERTIFICATE_RETAKE, FRAMING_CUE
from partsdesk.vision.images import MAX_BYTES, image_type

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "static/fixtures"
router = APIRouter(prefix="/api/inspections", dependencies=[Depends(require_operator)])
Service = Depends(get_service)


def respond(settings: Settings, snapshot: Any) -> dict[str, Any]:
    return to_json(snapshot, mode(settings))


@router.post("", dependencies=[Depends(limit_writes)])
async def create(body: NewInspection = Body(default_factory=NewInspection), operator: str = Depends(require_operator),
                 service: InspectionService = Service, settings: Settings = Depends(get_settings)) -> dict:
    return respond(settings, await run_in_threadpool(service.open_inspection, operator, body.training_case))


@router.get("/{inspection_id}")
async def read(inspection_id: InspectionId, service: InspectionService = Service,
               settings: Settings = Depends(get_settings)) -> dict:
    return respond(settings, await run_in_threadpool(service.snapshot, inspection_id))


@router.get("/{inspection_id}/events")
async def events(inspection_id: InspectionId, after: int = Query(0, ge=0),
                 service: InspectionService = Service) -> dict:
    items = await run_in_threadpool(service.events_after, inspection_id, after)
    return {"events": [{"seq": e.seq, "at": e.at, "type": e.type, "from_stage": e.from_stage,
                        "to_stage": e.to_stage, "detail": e.detail} for e in items],
            "last_seq": items[-1].seq if items else after}


@router.get("/{inspection_id}/report")
async def export(inspection_id: InspectionId, service: InspectionService = Service,
                 settings: Settings = Depends(get_settings)) -> JSONResponse:
    body = {**respond(settings, await run_in_threadpool(service.snapshot, inspection_id)), "exported_at": time.time()}
    return JSONResponse(body, headers={"Content-Disposition": f'attachment; filename="parts-desk-{inspection_id}.json"',
                                       "Cache-Control": "no-store"})


@router.post("/{inspection_id}/fixtures", dependencies=[Depends(limit_writes)])
async def fixture(inspection_id: InspectionId, body: FixtureRequest, service: InspectionService = Service,
                  settings: Settings = Depends(get_settings)) -> dict:
    data = await run_in_threadpool((FIXTURE_DIR / f"{body.variant}.png").read_bytes)
    return respond(settings, await run_in_threadpool(service.capture, inspection_id, body.kind, data,
                                                     CaptureSource.FIXTURE))


@router.post("/{inspection_id}/uploads/{kind}", dependencies=[Depends(limit_writes)])
async def upload(inspection_id: InspectionId, kind: CaptureKind, photo: UploadFile = File(...),
                 service: InspectionService = Service, settings: Settings = Depends(get_settings)) -> dict:
    data = await photo.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Use a JPEG or PNG under 12 MB.")
    return respond(settings, await run_in_threadpool(service.capture, inspection_id, kind, data,
                                                     CaptureSource.UPLOAD))


@router.post("/{inspection_id}/captures/{capture_id}/confirm", dependencies=[Depends(limit_writes)])
async def confirm(inspection_id: InspectionId, capture_id: CaptureId,
                  body: Confirmation = Body(default_factory=Confirmation), service: InspectionService = Service,
                  settings: Settings = Depends(get_settings), calls: LiveCalls = Depends(get_calls)) -> dict:
    corrections = Identifiers(body.part, body.serial) if (body.part or body.serial) else None
    snapshot = await run_in_threadpool(service.confirm, inspection_id, capture_id, corrections)
    if snapshot.confirmed(CaptureKind.TAG) and snapshot.confirmed(CaptureKind.TAG).id == capture_id:
        calls.say(inspection_id, CERTIFICATE_REQUEST)
    return respond(settings, snapshot)


@router.post("/{inspection_id}/captures/{capture_id}/retake", dependencies=[Depends(limit_writes)])
async def retake(inspection_id: InspectionId, capture_id: CaptureId, service: InspectionService = Service,
                 settings: Settings = Depends(get_settings), calls: LiveCalls = Depends(get_calls)) -> dict:
    snapshot = await run_in_threadpool(service.retake, inspection_id, capture_id)
    calls.clear_prompts(inspection_id)
    rejected = next(c for c in snapshot.captures if c.id == capture_id)
    calls.say(inspection_id, FRAMING_CUE if rejected.kind is CaptureKind.TAG else CERTIFICATE_RETAKE)
    return respond(settings, snapshot)


@router.post("/{inspection_id}/close", dependencies=[Depends(limit_writes)])
async def close(inspection_id: InspectionId, service: InspectionService = Service,
                settings: Settings = Depends(get_settings), calls: LiveCalls = Depends(get_calls)) -> dict:
    snapshot = await run_in_threadpool(service.close, inspection_id)
    calls.clear_prompts(inspection_id)
    calls.request_end(inspection_id)
    return respond(settings, snapshot)


@router.get("/{inspection_id}/captures/{capture_id}/{view}")
async def evidence(inspection_id: InspectionId, capture_id: CaptureId, view: Literal["original", "crop"],
                   service: InspectionService = Service) -> Response:
    data = await run_in_threadpool(service.evidence, inspection_id, capture_id, view)
    return Response(data, media_type=f"image/{image_type(data)}", headers={"Cache-Control": "no-store"})
