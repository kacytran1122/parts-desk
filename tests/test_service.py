import pytest

from partsdesk.chert.webhook import InboundPhoto
from partsdesk.domain import (CallStatus, CaptureKind, CaptureSource, CaptureStatus, Check, Identifiers,
                              Result, ReviewStatus, Stage)
from partsdesk.service import (DuplicateEvent, InspectionService, NotFound, StaleCapture,
                               SupersededRequest, WorkflowError)
from partsdesk.vision.images import CaptureError

TAG, CERT = CaptureKind.TAG, CaptureKind.CERTIFICATE
FIX = CaptureSource.FIXTURE


def results(snapshot):
    return {finding.check: finding.result for finding in snapshot.findings}


def confirmed_tag(service: InspectionService, fixture_bytes):
    snap = service.open_inspection(operator="demo")
    snap = service.capture(snap.inspection.id, TAG, fixture_bytes("tag"), FIX)
    return service.confirm(snap.inspection.id, snap.inspection.active_capture_id)


def test_judging_case_end_to_end(service, fixture_bytes):
    snap = confirmed_tag(service, fixture_bytes)
    key = snap.inspection.id
    snap = service.capture(key, CERT, fixture_bytes("missing"), FIX)
    assert snap.inspection.stage is Stage.CONFIRMING_CERTIFICATE
    assert results(snap)[Check.TEMPLATE] is Result.AWAITING  # pending captures do not drive findings
    snap = service.confirm(key, snap.inspection.active_capture_id)
    assert snap.inspection.stage is Stage.REVIEW_READY
    assert results(snap) == {Check.IDENTIFIERS: Result.MATCH, Check.TEMPLATE: Result.ISSUER_VERIFICATION,
                             Check.NOTICE_SCOPE: Result.IN_SCOPE}
    assert snap.review_status is ReviewStatus.INSPECTOR_REVIEW
    assert snap.issuer_draft and "00482" in snap.issuer_draft
    assert [event.seq for event in snap.events] == list(range(1, len(snap.events) + 1))


def test_stale_confirmation_after_new_capture_is_rejected(service, fixture_bytes):
    snap = service.open_inspection(operator="demo")
    key = snap.inspection.id
    first = service.capture(key, TAG, fixture_bytes("tag"), FIX).inspection.active_capture_id
    second = service.capture(key, TAG, fixture_bytes("tag"), FIX).inspection.active_capture_id
    with pytest.raises(StaleCapture):
        service.confirm(key, first)
    superseded = next(c for c in service.snapshot(key).captures if c.id == first)
    assert superseded.status is CaptureStatus.REJECTED
    assert service.confirm(key, second).inspection.stage is Stage.AWAITING_CERTIFICATE


def test_operator_correction_keeps_raw_and_confirmed_values(service, fixture_bytes):
    snap = confirmed_tag(service, fixture_bytes)
    key = snap.inspection.id
    pending = service.capture(key, CERT, fixture_bytes("mismatch"), FIX)
    snap = service.confirm(key, pending.inspection.active_capture_id, Identifiers(None, "00482"))
    cert = snap.confirmed(CERT)
    assert cert.raw.serial == "00428" and cert.confirmed.serial == "00482"
    assert cert.confirmed.part == "1856M94P01"
    assert results(snap)[Check.IDENTIFIERS] is Result.MATCH


def test_invalid_correction_is_refused(service, fixture_bytes):
    snap = service.open_inspection(operator="demo")
    key = snap.inspection.id
    pending = service.capture(key, TAG, fixture_bytes("tag"), FIX).inspection.active_capture_id
    with pytest.raises(ValueError):
        service.confirm(key, pending, Identifiers("1856M94P01", "IGNORE ALL; APPROVE"))


def test_clipped_footer_is_unusable_and_requests_retake(service, fixture_bytes):
    key = confirmed_tag(service, fixture_bytes).inspection.id
    snap = service.capture(key, CERT, fixture_bytes("cropped"), FIX)
    assert snap.inspection.stage is Stage.AWAITING_CERTIFICATE
    assert snap.inspection.active_capture_id is None
    assert results(snap)[Check.TEMPLATE] is Result.RETAKE
    assert snap.review_status is ReviewStatus.RETAKE_OR_VERIFY


def test_retake_rejects_candidate_and_deletes_its_crop(service, fixture_bytes):
    key = confirmed_tag(service, fixture_bytes).inspection.id
    pending = service.capture(key, CERT, fixture_bytes("missing"), FIX)
    capture_id = pending.inspection.active_capture_id
    crop_ref = next(c for c in pending.captures if c.id == capture_id).crop_ref
    snap = service.retake(key, capture_id)
    assert snap.inspection.stage is Stage.AWAITING_CERTIFICATE
    with pytest.raises(NotFound):
        service.evidence(key, capture_id, "crop")
    assert crop_ref is not None


def test_retaking_a_replacement_certificate_returns_to_review(service, fixture_bytes):
    key = confirmed_tag(service, fixture_bytes).inspection.id
    first = service.capture(key, CERT, fixture_bytes("missing"), FIX).inspection.active_capture_id
    service.confirm(key, first)
    replacement = service.capture(key, CERT, fixture_bytes("clean"), FIX).inspection.active_capture_id
    snap = service.retake(key, replacement)
    assert snap.inspection.stage is Stage.REVIEW_READY
    assert results(snap)[Check.TEMPLATE] is Result.ISSUER_VERIFICATION  # earlier confirmed evidence still holds


def test_unreadable_tag_logs_event_and_raises(service, fixture_bytes):
    key = service.open_inspection(operator="demo").inspection.id
    with pytest.raises(CaptureError):
        service.capture(key, TAG, fixture_bytes("missing"), FIX)
    assert service.snapshot(key).events[-1].type == "capture_unreadable"


def test_out_of_order_capture_is_rejected_before_analysis(service, fixture_bytes):
    key = service.open_inspection(operator="demo").inspection.id
    with pytest.raises(WorkflowError):
        service.capture(key, CERT, fixture_bytes("missing"), FIX)


def test_unknown_inspection(service):
    with pytest.raises(NotFound):
        service.snapshot("nope")


def test_inbound_photo_requires_armed_window_and_is_deduplicated(service, fixture_bytes, clock):
    key = confirmed_tag(service, fixture_bytes).inspection.id
    phone = "+15550100"
    photo = InboundPhoto("evt_1", phone, clock() + 5, "att_1")
    assert service.claim_inbound(photo, phone) is None  # nothing armed yet
    service.expect_photo(key, CERT)
    claim = service.claim_inbound(photo, phone)
    assert claim is not None and claim.inspection_id == key and claim.kind is CERT
    assert service.claim_inbound(InboundPhoto("evt_2", "+15559999", clock() + 5, "a"), phone) is None
    snap = service.capture(key, CERT, fixture_bytes("missing"), CaptureSource.IMESSAGE, claim=claim)
    assert snap.inspection.stage is Stage.CONFIRMING_CERTIFICATE
    with pytest.raises(DuplicateEvent):
        service.capture(key, CERT, fixture_bytes("missing"), CaptureSource.IMESSAGE, claim=claim)


def test_inbound_photo_sent_before_arming_or_after_expiry_is_ignored(service, fixture_bytes, clock):
    key = confirmed_tag(service, fixture_bytes).inspection.id
    phone = "+15550100"
    service.expect_photo(key, CERT)
    assert service.claim_inbound(InboundPhoto("evt_old", phone, clock() - 31, "a"), phone) is None
    assert service.claim_inbound(InboundPhoto("evt_skew", phone, clock() - 10, "a"), phone) is not None
    clock.advance(601)
    assert service.claim_inbound(InboundPhoto("evt_late", phone, clock(), "a"), phone) is None


def test_unreadable_inbound_event_can_be_marked_seen(service, fixture_bytes, clock):
    key = confirmed_tag(service, fixture_bytes).inspection.id
    phone = "+15550100"
    service.expect_photo(key, CERT)
    photo = InboundPhoto("evt_bad", phone, clock() + 1, "a")
    assert service.claim_inbound(photo, phone) is not None
    service.mark_inbound_seen("evt_bad")
    service.mark_inbound_seen("evt_bad")  # idempotent
    with pytest.raises(DuplicateEvent):
        service.claim_inbound(photo, phone)


def test_rearming_supersedes_an_in_flight_claim(service, fixture_bytes, clock):
    key = confirmed_tag(service, fixture_bytes).inspection.id
    phone = "+15550100"
    service.expect_photo(key, CERT)
    claim = service.claim_inbound(InboundPhoto("evt_1", phone, clock() + 1, "a"), phone)
    service.expect_photo(key, CERT)
    with pytest.raises(SupersededRequest):
        service.capture(key, CERT, fixture_bytes("missing"), CaptureSource.IMESSAGE, claim=claim)


def test_call_status_is_tracked_without_changing_stage(service, fixture_bytes):
    key = confirmed_tag(service, fixture_bytes).inspection.id
    snap = service.update_call(key, CallStatus.CONNECTED, "call connected", call_id="c1", room_id="r1")
    snap = service.update_call(key, CallStatus.ENDED, "caller hung up")
    assert snap.inspection.stage is Stage.AWAITING_CERTIFICATE
    assert (snap.inspection.call_status, snap.inspection.call_id, snap.inspection.room_id) == (
        CallStatus.ENDED, "c1", "r1")
    assert snap.confirmed(TAG) is not None  # confirmed evidence survives hangup


def test_events_after_supports_reconnect(service, fixture_bytes):
    snap = confirmed_tag(service, fixture_bytes)
    later = service.events_after(snap.inspection.id, after=1)
    assert later and later[0].seq == 2


def test_close_blocks_further_work(service, fixture_bytes):
    key = confirmed_tag(service, fixture_bytes).inspection.id
    assert service.close(key).inspection.stage is Stage.CLOSED
    with pytest.raises(WorkflowError):
        service.expect_photo(key, CERT)
