from dataclasses import replace

from partsdesk.domain import (Capture, CaptureKind, CaptureSource, CaptureStatus, Check, Identifiers,
                              Result, ReviewStatus, Visibility)
from partsdesk.rules import GE_FOOTER_RULE, evaluate, issuer_request_draft, review_status


def capture(kind, part="1856M94P01", serial="00482", status=CaptureStatus.CONFIRMED,
            visibility=None, confirmed=None):
    raw = Identifiers(part=part, serial=serial)
    return Capture(
        id=f"{kind}-1", inspection_id="i1", kind=kind, source=CaptureSource.FIXTURE, status=status,
        captured_at=1.0, raw=raw, confirmed=confirmed if confirmed is not None else
        (raw if status is CaptureStatus.CONFIRMED else None),
        confirmed_at=2.0 if status is CaptureStatus.CONFIRMED else None, visibility=visibility,
        reason="", observations=(), original_sha256="a" * 64, crop_sha256=None,
        original_ref="o", crop_ref=None,
    )


TAG = capture(CaptureKind.TAG)
MISSING = capture(CaptureKind.CERTIFICATE, visibility=Visibility.MISSING)
PRESENT = capture(CaptureKind.CERTIFICATE, visibility=Visibility.PRESENT)


def by_check(findings):
    return {finding.check: finding for finding in findings}


def test_judging_case_matches_ids_but_asks_for_issuer_verification():
    findings = by_check(evaluate(TAG, MISSING, MISSING, GE_FOOTER_RULE))
    assert findings[Check.IDENTIFIERS].result is Result.MATCH
    assert findings[Check.TEMPLATE].result is Result.ISSUER_VERIFICATION
    assert findings[Check.TEMPLATE].rule == "PD-GE-FOOTER@1"
    assert findings[Check.NOTICE_SCOPE].result is Result.IN_SCOPE
    assert findings[Check.NOTICE_SCOPE].values["supplier_verified"] is False
    assert review_status(tuple(findings.values())) is ReviewStatus.INSPECTOR_REVIEW


def test_findings_are_independent_template_present_with_different_serial():
    cert = replace(PRESENT, raw=Identifiers("1856M94P01", "00428"), confirmed=Identifiers("1856M94P01", "00428"))
    findings = by_check(evaluate(TAG, cert, cert, GE_FOOTER_RULE))
    assert findings[Check.IDENTIFIERS].result is Result.DIFFERENT
    assert findings[Check.TEMPLATE].result is Result.SECTION_PRESENT


def test_leading_zeros_are_significant():
    cert = replace(PRESENT, confirmed=Identifiers("1856M94P01", "0482"))
    assert by_check(evaluate(TAG, cert, cert, GE_FOOTER_RULE))[Check.IDENTIFIERS].result is Result.DIFFERENT


def test_unreadable_certificate_serial_is_insufficient_evidence():
    cert = replace(PRESENT, confirmed=Identifiers("1856M94P01", None))
    findings = evaluate(TAG, cert, cert, GE_FOOTER_RULE)
    assert by_check(findings)[Check.IDENTIFIERS].result is Result.INSUFFICIENT
    assert review_status(findings) is ReviewStatus.RETAKE_OR_VERIFY


def test_clipped_footer_attempt_requests_retake():
    unusable = capture(CaptureKind.CERTIFICATE, status=CaptureStatus.UNUSABLE, visibility=Visibility.RETAKE)
    findings = evaluate(TAG, None, unusable, GE_FOOTER_RULE)
    assert by_check(findings)[Check.TEMPLATE].result is Result.RETAKE
    assert review_status(findings) is ReviewStatus.RETAKE_OR_VERIFY


def test_unrecognized_footer_marks_need_human_review():
    cert = replace(PRESENT, visibility=Visibility.UNRESOLVED)
    assert by_check(evaluate(TAG, cert, cert, GE_FOOTER_RULE))[Check.TEMPLATE].result is Result.HUMAN_REVIEW


def test_clean_case_records_checks():
    findings = evaluate(TAG, PRESENT, PRESENT, GE_FOOTER_RULE)
    assert review_status(findings) is ReviewStatus.CHECKS_RECORDED


def test_part_outside_notice_is_reported_as_scope_not_matched():
    tag = replace(TAG, confirmed=Identifiers("ABC123", "00482"))
    assert by_check(evaluate(tag, None, None, GE_FOOTER_RULE))[Check.NOTICE_SCOPE].result is Result.OUT_OF_SCOPE


def test_pending_candidates_do_not_drive_findings():
    pending = capture(CaptureKind.TAG, status=CaptureStatus.PENDING)
    findings = by_check(evaluate(None, None, None, GE_FOOTER_RULE))
    assert all(f.result is Result.AWAITING for f in findings.values())
    assert pending.effective == pending.raw


def test_draft_only_for_issuer_verification_and_cites_hash_and_notice():
    draft = issuer_request_draft(MISSING, GE_FOOTER_RULE)
    assert "00482" in draft and "a" * 64 in draft and GE_FOOTER_RULE.notice_id in draft
    assert issuer_request_draft(PRESENT, GE_FOOTER_RULE) is None
    assert issuer_request_draft(None, GE_FOOTER_RULE) is None
