"""Snapshot of one inspection and its exportable JSON report."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from partsdesk.domain import (Capture, CaptureKind, CaptureStatus, Event, Finding, Inspection, ReviewStatus,
                              RuleRevision)


@dataclass(frozen=True)
class Snapshot:
    inspection: Inspection
    rule: RuleRevision
    captures: tuple[Capture, ...]
    findings: tuple[Finding, ...]
    events: tuple[Event, ...]
    review_status: ReviewStatus
    issuer_draft: str | None

    def confirmed(self, kind: CaptureKind) -> Capture | None:
        matches = [c for c in self.captures if c.kind is kind and c.status is CaptureStatus.CONFIRMED]
        return matches[-1] if matches else None

    def pending(self) -> Capture | None:
        return next((c for c in self.captures if c.id == self.inspection.active_capture_id), None)

    def latest_attempt(self, kind: CaptureKind) -> Capture | None:
        matches = [c for c in self.captures if c.kind is kind and c.status is not CaptureStatus.REJECTED]
        return matches[-1] if matches else None


def capture_json(item: Capture | None) -> dict[str, Any] | None:
    if item is None:
        return None
    differences = []
    if item.confirmed is not None:
        differences = [name for name in ("part", "serial")
                       if getattr(item.raw, name) != getattr(item.confirmed, name)]
    return {
        "id": item.id, "kind": item.kind, "source": item.source, "status": item.status,
        "captured_at": item.captured_at, "confirmed_at": item.confirmed_at,
        "raw": item.raw.to_dict(), "confirmed": item.confirmed.to_dict() if item.confirmed else None,
        "corrected_fields": differences, "visibility": item.visibility, "reason": item.reason,
        "observations": list(item.observations), "original_sha256": item.original_sha256,
        "crop_sha256": item.crop_sha256, "has_crop": item.crop_ref is not None,
    }


def finding_json(item: Finding) -> dict[str, Any]:
    return {"check": item.check, "result": item.result, "capture_ids": list(item.capture_ids),
            "values": dict(item.values), "rule": item.rule}


def to_json(snapshot: Snapshot, mode: str) -> dict[str, Any]:
    item, rule = snapshot.inspection, snapshot.rule
    return {
        "id": item.id, "mode": mode, "training_case": item.training_case, "stage": item.stage,
        "operator": item.operator, "created_at": item.created_at, "updated_at": item.updated_at,
        "review_status": snapshot.review_status,
        "call": {"status": item.call_status, "call_id": item.call_id, "room_id": item.room_id},
        "pending": capture_json(snapshot.pending()),
        "tag": capture_json(snapshot.confirmed(CaptureKind.TAG)),
        "certificate": capture_json(snapshot.confirmed(CaptureKind.CERTIFICATE)),
        "latest_certificate_attempt": capture_json(snapshot.latest_attempt(CaptureKind.CERTIFICATE)),
        "findings": [finding_json(finding) for finding in snapshot.findings],
        "rule": {"id": rule.id, "version": rule.version, "notice_id": rule.notice_id,
                 "source_url": rule.source_url, "scope": rule.scope, "predicates": dict(rule.predicates)},
        "issuer_draft": snapshot.issuer_draft,
        "human_determination": "An inspector determines installation eligibility. This report approves nothing.",
        "last_seq": item.last_seq,
        "events": [{"seq": e.seq, "at": e.at, "type": e.type, "from_stage": e.from_stage,
                    "to_stage": e.to_stage, "detail": e.detail} for e in snapshot.events],
    }
