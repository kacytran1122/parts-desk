"""Three independent findings: identifier agreement, issuer template, FAA notice scope."""
from __future__ import annotations

from partsdesk.domain import (Capture, CaptureStatus, Check, Finding, Result, ReviewStatus,
                              RuleRevision, Visibility, frozen_map)

FAA_NOTICE_URL = "https://www.faa.gov/aircraft/safety/programs/sups/upn/2023-AAE-EHL-20230801-713"
NOTICE_PART = "1856M94P01"

GE_FOOTER_RULE = RuleRevision(
    id="PD-GE-FOOTER",
    version=1,
    notice_id="2023-AAE-EHL-20230801-713",
    source_url=FAA_NOTICE_URL,
    scope=(
        "GE bushing 1856M94P01 sold by AOG Technics to TAP Maintenance & Engineering. "
        "Falsified paperwork characteristics include missing User/Installer Responsibilities language."
    ),
    predicates=frozen_map({
        "part_number": NOTICE_PART,
        "expected_section": "User/Installer Responsibilities",
        "notice_supplier": "AOG Technics",
        "notice_buyer": "TAP Maintenance & Engineering",
    }),
)

_TEMPLATE_RESULTS = {
    Visibility.PRESENT: Result.SECTION_PRESENT,
    Visibility.MISSING: Result.ISSUER_VERIFICATION,
    Visibility.UNRESOLVED: Result.HUMAN_REVIEW,
    Visibility.RETAKE: Result.RETAKE,
}
_REVIEW_RESULTS = {Result.DIFFERENT, Result.ISSUER_VERIFICATION, Result.HUMAN_REVIEW}
_RETAKE_RESULTS = {Result.RETAKE, Result.INSUFFICIENT}


def _ids(capture: Capture | None) -> tuple[str, ...]:
    return (capture.id,) if capture else ()


def identifier_finding(tag: Capture | None, cert: Capture | None) -> Finding:
    if tag is None or cert is None:
        return Finding(Check.IDENTIFIERS, Result.AWAITING, _ids(tag) + _ids(cert), frozen_map())
    left, right = tag.effective, cert.effective
    values = frozen_map({"tag": {"part": left.part, "serial": left.serial},
                         "certificate": {"part": right.part, "serial": right.serial}})
    if not all((left.part, left.serial, right.part, right.serial)):
        result = Result.INSUFFICIENT
    elif (left.part, left.serial) == (right.part, right.serial):
        result = Result.MATCH
    else:
        result = Result.DIFFERENT
    return Finding(Check.IDENTIFIERS, result, (tag.id, cert.id), values)


def template_finding(cert: Capture | None, latest_attempt: Capture | None, rule: RuleRevision) -> Finding:
    if cert is not None and cert.visibility is not None:
        values = frozen_map({"visibility": cert.visibility.value, "reason": cert.reason,
                             "expected_section": rule.predicates["expected_section"]})
        return Finding(Check.TEMPLATE, _TEMPLATE_RESULTS[cert.visibility], (cert.id,), values, rule.ref)
    if latest_attempt is not None and latest_attempt.status is CaptureStatus.UNUSABLE:
        values = frozen_map({"visibility": Visibility.RETAKE.value, "reason": latest_attempt.reason})
        return Finding(Check.TEMPLATE, Result.RETAKE, (latest_attempt.id,), values, rule.ref)
    return Finding(Check.TEMPLATE, Result.AWAITING, (), frozen_map(), rule.ref)


def scope_finding(tag: Capture | None, cert: Capture | None, rule: RuleRevision) -> Finding:
    source = tag or cert
    part = source.effective.part if source else None
    if part is None:
        return Finding(Check.NOTICE_SCOPE, Result.AWAITING, _ids(source), frozen_map(), rule.ref)
    in_scope = part == rule.predicates["part_number"]
    values = frozen_map({"part": part, "notice_part": rule.predicates["part_number"],
                         "supplier_verified": False, "buyer_verified": False})
    return Finding(Check.NOTICE_SCOPE, Result.IN_SCOPE if in_scope else Result.OUT_OF_SCOPE,
                   _ids(source), values, rule.ref)


def evaluate(tag: Capture | None, cert: Capture | None, latest_cert_attempt: Capture | None,
             rule: RuleRevision) -> tuple[Finding, ...]:
    """Evaluate confirmed captures only; `latest_cert_attempt` may be an unusable retake."""
    return (
        identifier_finding(tag, cert),
        template_finding(cert, latest_cert_attempt, rule),
        scope_finding(tag, cert, rule),
    )


def review_status(findings: tuple[Finding, ...]) -> ReviewStatus:
    results = {finding.check: finding.result for finding in findings}
    if _REVIEW_RESULTS & set(results.values()):
        return ReviewStatus.INSPECTOR_REVIEW
    if _RETAKE_RESULTS & set(results.values()):
        return ReviewStatus.RETAKE_OR_VERIFY
    if results.get(Check.IDENTIFIERS) is Result.MATCH and results.get(Check.TEMPLATE) is Result.SECTION_PRESENT:
        return ReviewStatus.CHECKS_RECORDED
    return ReviewStatus.AWAITING


def issuer_request_draft(cert: Capture | None, rule: RuleRevision) -> str | None:
    """Draft for the inspector to review and send. Never sent automatically."""
    if cert is None or cert.visibility is not Visibility.MISSING:
        return None
    ids = cert.effective
    return (
        f"Please verify the release record for part {ids.part or '(unread)'}, serial {ids.serial or '(unread)'}. "
        f"The expected {rule.predicates['expected_section']} section is absent from a visible area of the "
        f"submitted certificate photo. Evidence SHA-256: {cert.original_sha256}. "
        f"Reference: FAA notice {rule.notice_id} ({rule.source_url}). "
        "This is a training exercise draft for inspector review."
    )
