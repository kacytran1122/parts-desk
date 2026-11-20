import json

import pytest

from partsdesk.chert.webhook import WebhookError, parse_inbound_photo, sign, verify_signature

SECRET = "test-secret"
PHONE = "+15550100"


def payload(**overrides):
    message = {"direction": "inbound", "sender_handle": {"handle": PHONE},
               "sent_at": "2026-11-14T12:00:00+00:00",
               "parts": [{"type": "media", "attachment_id": "att_1"}]}
    message.update(overrides.pop("message", {}))
    body = {"event_type": "message.received", "event_id": "evt_1", "partner_id": "tenant",
            "data": {"message": message, "chat": {"is_group": False}}}
    body.update(overrides)
    return body


def test_valid_signature_over_raw_body_passes():
    body = json.dumps(payload()).encode()
    verify_signature(SECRET, sign(SECRET, body, 1000), body, now=1010)


@pytest.mark.parametrize("mutate", [
    lambda header, body: (header, body + b" "),
    lambda header, body: (header.replace("t=1000", "t=1001"), body),
    lambda header, body: ("v1=abc", body),
    lambda header, body: ("", body),
])
def test_tampered_or_missing_signature_is_rejected(mutate):
    body = json.dumps(payload()).encode()
    header, body = mutate(sign(SECRET, body, 1000), body)
    with pytest.raises(WebhookError):
        verify_signature(SECRET, header, body, now=1000)


def test_stale_timestamp_is_rejected():
    body = b"{}"
    with pytest.raises(WebhookError):
        verify_signature(SECRET, sign(SECRET, body, 1000), body, now=1000 + 301)


def test_missing_secret_is_rejected():
    with pytest.raises(WebhookError):
        verify_signature("", sign("x", b"{}", 1000), b"{}", now=1000)


def test_parse_inbound_photo_extracts_one_attachment():
    photo = parse_inbound_photo(payload(), tenant="tenant")
    assert (photo.event_id, photo.sender, photo.attachment_id) == ("evt_1", PHONE, "att_1")
    assert photo.sent_at == pytest.approx(1794657600.0)


@pytest.mark.parametrize("body", [
    payload(event_type="message.sent"),
    payload(partner_id="other"),
    payload(message={"direction": "outbound"}),
    payload(message={"parts": []}),
    payload(message={"parts": [{"type": "media", "attachment_id": "a"}, {"type": "media", "attachment_id": "b"}]}),
    payload(message={"sent_at": "not a date"}),
    payload(message={"sent_at": "2026-11-14T12:00:00"}),  # no offset: ambiguous, rejected
    payload(data={"message": {}, "chat": {"is_group": True}}),
    payload(event_id=None),
])
def test_parse_ignores_messages_that_are_not_a_single_inbound_photo(body):
    assert parse_inbound_photo(body, tenant="tenant") is None
