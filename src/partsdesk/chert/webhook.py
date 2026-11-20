"""Verify Chert webhook signatures over the raw body and extract one inbound photo."""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

TOLERANCE_SECONDS = 300
MAX_BODY_BYTES = 256 * 1024
_ATTACHMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,160}")


class WebhookError(Exception):
    pass


@dataclass(frozen=True)
class InboundPhoto:
    event_id: str
    sender: str
    sent_at: float
    attachment_id: str


def _digest(secret: str, body: bytes, timestamp: str) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def sign(secret: str, body: bytes, timestamp: int) -> str:
    """Build a signature header. Used by tests and local rehearsal scripts."""
    return f"t={timestamp},v1={_digest(secret, body, str(timestamp))}"


def verify_signature(secret: str, header: str, body: bytes, now: float) -> None:
    if not secret:
        raise WebhookError("Webhook secret is not configured.")
    pairs = dict(item.strip().split("=", 1) for item in header.split(",") if "=" in item)
    stamp = pairs.get("t", "")
    if not re.fullmatch(r"[0-9]{1,12}", stamp) or abs(now - int(stamp)) > TOLERANCE_SECONDS:
        raise WebhookError("Invalid or stale webhook timestamp.")
    expected = _digest(secret, body, stamp)
    if not hmac.compare_digest(expected.encode(), pairs.get("v1", "").encode()):
        raise WebhookError("Invalid webhook signature.")


def _sent_at(message: dict[str, Any]) -> float | None:
    """Accept only offset-aware timestamps; a naive time would depend on the host's zone."""
    try:
        parsed = datetime.fromisoformat(str(message["sent_at"]))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def parse_inbound_photo(payload: dict[str, Any], tenant: str) -> InboundPhoto | None:
    """Return the single inbound photo, or None when the event is not one we accept."""
    data = payload.get("data") or {}
    message, chat = data.get("message") or {}, data.get("chat") or {}
    event_id = payload.get("event_id")
    sender = (message.get("sender_handle") or {}).get("handle")
    if (payload.get("event_type") != "message.received" or not isinstance(event_id, str) or not event_id
            or chat.get("is_group") is not False or message.get("direction") != "inbound"
            or not isinstance(sender, str) or not tenant or payload.get("partner_id") != tenant):
        return None
    media = [part for part in message.get("parts") or [] if part.get("type") == "media"]
    if len(media) != 1 or not _ATTACHMENT_ID.fullmatch(str(media[0].get("attachment_id", ""))):
        return None
    sent_at = _sent_at(message)
    if sent_at is None:
        return None
    return InboundPhoto(event_id, sender, sent_at, media[0]["attachment_id"])
