"""Pure helpers for the Chert Bring Your Own Room call lifecycle."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from partsdesk.config import Settings
from partsdesk.domain import CallStatus

AGENT_IDENTITY = "parts-desk-agent"
BRIDGE_IDENTITY = "chert-bridge"
BRIDGE_VIDEO_TRACK = "facetime-remote-video"
TOKEN_TTL = timedelta(minutes=12)

_STATUS = {
    "queued": CallStatus.DIALING, "pending": CallStatus.DIALING, "initiated": CallStatus.DIALING,
    "dialing": CallStatus.DIALING, "allocating": CallStatus.DIALING,
    "ringing": CallStatus.RINGING,
    "answered": CallStatus.CONNECTED, "active": CallStatus.CONNECTED, "connected": CallStatus.CONNECTED,
    "in_progress": CallStatus.CONNECTED,
    "ending": CallStatus.ENDING,
    "ended": CallStatus.ENDED, "completed": CallStatus.ENDED, "cancelled": CallStatus.ENDED,
    "canceled": CallStatus.ENDED, "expired": CallStatus.ENDED,
    "no_answer": CallStatus.NO_ANSWER, "busy": CallStatus.BUSY, "declined": CallStatus.DECLINED,
    "failed": CallStatus.FAILED,
}
TERMINAL = {CallStatus.ENDED, CallStatus.NO_ANSWER, CallStatus.BUSY, CallStatus.DECLINED, CallStatus.FAILED}


def classify(status: str) -> CallStatus | None:
    return _STATUS.get(status.strip().lower())


def is_terminal(status: CallStatus | None) -> bool:
    return status in TERMINAL


def has_bridge_ready(payload: dict[str, Any]) -> bool:
    data = payload.get("data", payload)
    records = data if isinstance(data, list) else (data.get("events", []) if isinstance(data, dict) else [])
    names = ("event_type", "type", "kind", "event")
    return any(isinstance(record, dict) and any(
        str(record.get(name, "")).lower() in {"bridge_ready", "call.started"} for name in names)
        for record in records)


def room_name(inspection_id: str, nonce: str) -> str:
    return f"parts-desk-{inspection_id}-{nonce}"


def outbound_body(settings: Settings, bridge_token: str) -> dict[str, Any]:
    return {
        "direction": "outbound", "mode": "customer_managed", "line_id": settings.chert_facetime_line_id,
        "destination": settings.test_phone, "destination_type": "phone",
        "media": {"audio": True, "video": True}, "livekit_url": settings.livekit_url,
        "participant_token": bridge_token, "remote_participant_identity": AGENT_IDENTITY,
    }


def room_token(settings: Settings, room: str, identity: str) -> str:  # pragma: no cover - needs livekit-api
    from livekit import api

    grants = api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True,
                             can_publish_data=False)
    return (api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
            .with_identity(identity).with_ttl(TOKEN_TTL).with_grants(grants).to_jwt())
