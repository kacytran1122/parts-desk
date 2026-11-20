"""Public Chert webhook. Every request is verified over its raw bytes before parsing."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from partsdesk.api.deps import get_calls, get_service, get_settings, limit_webhook
from partsdesk.chert.client import ChertMessaging
from partsdesk.chert.webhook import MAX_BODY_BYTES, WebhookError, parse_inbound_photo, verify_signature
from partsdesk.config import Settings
from partsdesk.domain import CaptureKind, CaptureSource
from partsdesk.live.manager import LiveCalls
from partsdesk.service import DuplicateEvent, InspectionService, SupersededRequest, WorkflowError
from partsdesk.speech.prompts import FRAMING_CUE
from partsdesk.vision.images import CaptureError

router = APIRouter()


async def _read_limited(request: Request) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BODY_BYTES:
            raise HTTPException(413, "Webhook body too large.")
    return bytes(data)


@router.post("/api/chert/messages", dependencies=[Depends(limit_webhook)])
async def chert_message(request: Request, settings: Settings = Depends(get_settings),
                        service: InspectionService = Depends(get_service),
                        calls: LiveCalls = Depends(get_calls)) -> dict:
    if not settings.messaging_ready:
        raise HTTPException(503, "Chert messaging is not configured on this instance.")
    body = await _read_limited(request)
    try:
        verify_signature(settings.chert_webhook_secret, request.headers.get("x-webhook-signature", ""), body,
                         time.time())
    except WebhookError as error:
        raise HTTPException(401, "Invalid webhook signature.") from error
    try:
        payload = json.loads(body)
    except ValueError as error:
        raise HTTPException(400, "Malformed webhook JSON.") from error
    photo = parse_inbound_photo(payload, settings.chert_tenant) if isinstance(payload, dict) else None
    if photo is None:
        return {"accepted": False}
    try:
        claim = await run_in_threadpool(service.claim_inbound, photo, settings.test_phone)
    except DuplicateEvent:
        return {"accepted": True, "duplicate": True}
    if claim is None:
        return {"accepted": False}
    messaging = ChertMessaging(request.app.state.http, settings.chert_api_key, settings.chert_tenant)
    data = await messaging.download_attachment(photo.attachment_id)
    try:
        await run_in_threadpool(service.capture, claim.inspection_id, claim.kind, data, CaptureSource.IMESSAGE, claim)
    except DuplicateEvent:
        return {"accepted": True, "duplicate": True}
    except (SupersededRequest, WorkflowError):
        return {"accepted": False, "superseded": True}
    except CaptureError:
        # Terminal for this event: record it so a replay cannot re-run download and OCR.
        await run_in_threadpool(service.mark_inbound_seen, photo.event_id)
        if claim.kind is CaptureKind.TAG:
            calls.say(claim.inspection_id, FRAMING_CUE)
        return {"accepted": True, "readable": False}
    return {"accepted": True}
