"""Live rehearsal routes: FaceTime call control, caller preview, QR capture, iMessage arming."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from partsdesk.api.auth import require_operator
from partsdesk.api.deps import (InspectionId, get_calls, get_frames, get_service, get_settings, limit_writes,
                                mode)
from partsdesk.api.schemas import PhotoKind
from partsdesk.config import Settings
from partsdesk.domain import CaptureKind, CaptureSource, Stage
from partsdesk.live.frames import FrameStore
from partsdesk.live.manager import LiveCalls
from partsdesk.report import to_json
from partsdesk.service import InspectionService
from partsdesk.speech.prompts import FRAMING_CUE, tag_readback
from partsdesk.vision.images import CaptureError, image_type

router = APIRouter(prefix="/api/inspections", dependencies=[Depends(require_operator)])


def _require_live(settings: Settings) -> None:
    if not settings.live:
        raise HTTPException(503, "Live mode is off. Use the training fixtures or uploads.")


@router.post("/{inspection_id}/expect-photo", dependencies=[Depends(limit_writes)])
async def expect_photo(inspection_id: InspectionId, body: PhotoKind, settings: Settings = Depends(get_settings),
                       service: InspectionService = Depends(get_service)) -> dict:
    if not settings.messaging_ready:
        raise HTTPException(503, "Configure live mode, the verified test phone, and Chert messaging.")
    return to_json(await run_in_threadpool(service.expect_photo, inspection_id, body.kind), mode(settings))


@router.post("/{inspection_id}/facetime", dependencies=[Depends(limit_writes)])
async def start_call(inspection_id: InspectionId, request: Request, settings: Settings = Depends(get_settings),
                     service: InspectionService = Depends(get_service), frames: FrameStore = Depends(get_frames),
                     calls: LiveCalls = Depends(get_calls)) -> dict:
    if not settings.facetime_ready:
        raise HTTPException(503, "Configure the verified test iPhone, the Chert FaceTime line, and LiveKit.")
    snapshot = await run_in_threadpool(service.snapshot, inspection_id)
    if snapshot.inspection.stage is Stage.CLOSED:
        raise HTTPException(409, "This inspection is closed. Start a new one before calling.")
    try:
        from partsdesk.live.worker import run_call
    except ImportError as error:
        raise HTTPException(503, "Install the facetime extra: uv sync --extra facetime.") from error
    await run_in_threadpool(service.note, inspection_id, "call_requested",
                            "Inspector requested a FaceTime training call.")
    http = request.app.state.http
    calls.start(inspection_id, lambda end, attach: run_call(
        settings=settings, service=service, frames=frames, http=http, inspection_id=inspection_id,
        end=end, attach=attach))
    return {"requested": True}


@router.post("/{inspection_id}/facetime/end", dependencies=[Depends(limit_writes)])
async def end_call(inspection_id: InspectionId, calls: LiveCalls = Depends(get_calls)) -> dict:
    return {"end_requested": calls.request_end(inspection_id)}


@router.get("/{inspection_id}/preview")
async def preview(inspection_id: InspectionId, frames: FrameStore = Depends(get_frames)) -> Response:
    frame = frames.latest(inspection_id)
    if frame is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return Response(frame, media_type=f"image/{image_type(frame)}", headers={"Cache-Control": "no-store"})


@router.post("/{inspection_id}/capture-tag", dependencies=[Depends(limit_writes)])
async def capture_tag(inspection_id: InspectionId, settings: Settings = Depends(get_settings),
                      service: InspectionService = Depends(get_service), frames: FrameStore = Depends(get_frames),
                      calls: LiveCalls = Depends(get_calls)) -> dict:
    _require_live(settings)
    frame = frames.latest(inspection_id)
    if frame is None:
        raise HTTPException(409, "Wait for a current FaceTime camera frame.")
    try:
        snapshot = await run_in_threadpool(service.capture, inspection_id, CaptureKind.TAG, frame,
                                           CaptureSource.FACETIME)
    except CaptureError:
        calls.say(inspection_id, FRAMING_CUE)
        raise
    pending = snapshot.pending()
    if pending is not None and pending.raw.part and pending.raw.serial:
        calls.clear_prompts(inspection_id)
        calls.say(inspection_id, tag_readback(pending.raw.part, pending.raw.serial))
    return to_json(snapshot, mode(settings))
