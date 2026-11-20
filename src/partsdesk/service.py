"""Application layer shared by the HTTP API and the live FaceTime worker.

Slow image analysis runs before the write transaction. Each state change, its capture
record, its event, and the recomputed findings commit together.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from partsdesk.chert.webhook import InboundPhoto
from partsdesk.domain import (CallStatus, Capture, CaptureKind, CaptureSource, CaptureStatus, Identifiers,
                              Inspection, RuleRevision, Stage, Visibility)
from partsdesk.report import Snapshot
from partsdesk.rules import evaluate, issuer_request_draft, review_status
from partsdesk.storage import repository as repo
from partsdesk.storage.database import initialize, transaction
from partsdesk.storage.evidence import EvidenceError, EvidenceStore, StoredFile
from partsdesk.storage.repository import NotFound, PhotoRequest
from partsdesk.vision.certificate import CertificateReading, read_certificate
from partsdesk.vision.images import CaptureError, image_type
from partsdesk.vision.qr import TagReading, read_tag
from partsdesk.workflow import Command, WorkflowError, can_capture, next_stage

__all__ = ["CaptureError", "CorrectionError", "DuplicateEvent", "InboundClaim", "InspectionService", "NotFound",
           "StaleCapture", "SupersededRequest", "WorkflowError"]

log = logging.getLogger("partsdesk")
PHOTO_WINDOW_SECONDS = 600
CLOCK_SKEW_SECONDS = 30
_PART = re.compile(r"[A-Z0-9-]{1,24}")
_SERIAL = re.compile(r"[A-Z0-9]{1,18}")
SOURCE_LABELS = {
    CaptureSource.FIXTURE: "Training fixture", CaptureSource.UPLOAD: "Inspector upload",
    CaptureSource.FACETIME: "FaceTime frame", CaptureSource.IMESSAGE: "iMessage attachment",
}


class CorrectionError(ValueError):
    """An inspector correction is not a valid identifier."""


class StaleCapture(Exception):
    """A confirmation or retake targeted a capture that is no longer the active candidate."""


class DuplicateEvent(Exception):
    """This inbound webhook event was already processed."""


class SupersededRequest(Exception):
    """The photo request was re-armed, cleared, or expired while the attachment was processed."""


@dataclass(frozen=True)
class InboundClaim:
    inspection_id: str
    kind: CaptureKind
    event_id: str
    token: str


@dataclass(frozen=True)
class _Analysis:
    identifiers: Identifiers
    visibility: Visibility | None
    reason: str
    observations: tuple[str, ...]
    crop_png: bytes | None


def _normalize(value: str, pattern: re.Pattern[str]) -> str:
    cleaned = value.strip().upper()
    if not pattern.fullmatch(cleaned):
        raise CorrectionError("Corrections may contain only letters, digits, and dashes.")
    return cleaned


def merge_corrections(raw: Identifiers, corrections: Identifiers | None) -> Identifiers:
    if corrections is None:
        return raw
    part = _normalize(corrections.part, _PART) if corrections.part else raw.part
    serial = _normalize(corrections.serial, _SERIAL) if corrections.serial else raw.serial
    return Identifiers(part, serial, raw.description)


class InspectionService:
    def __init__(self, db_path: Path, evidence: EvidenceStore, rule: RuleRevision,
                 clock: Callable[[], float] = time.time,
                 tag_reader: Callable[[bytes], TagReading] = read_tag,
                 certificate_reader: Callable[[bytes], CertificateReading] = read_certificate) -> None:
        initialize(db_path, rule)
        self._db, self._evidence, self._rule, self._clock = db_path, evidence, rule, clock
        self._read_tag, self._read_certificate = tag_reader, certificate_reader

    # Reads -----------------------------------------------------------------

    def snapshot(self, inspection_id: str) -> Snapshot:
        with transaction(self._db) as db:
            return self._snapshot(db, inspection_id)

    def events_after(self, inspection_id: str, after: int) -> tuple:
        with transaction(self._db) as db:
            repo.get_inspection(db, inspection_id)
            return repo.list_events(db, inspection_id, after)

    def evidence(self, inspection_id: str, capture_id: str, view: str) -> bytes:
        with transaction(self._db) as db:
            item = repo.get_capture(db, inspection_id, capture_id)
        ref = item.original_ref if view == "original" else item.crop_ref
        if ref is None:
            raise NotFound("Evidence was not retained for this capture.")
        try:
            return self._evidence.read(ref)
        except EvidenceError as error:
            raise NotFound("Evidence file is not available.") from error

    # Inspection lifecycle --------------------------------------------------

    def open_inspection(self, operator: str, training_case: str = "judging") -> Snapshot:
        now = self._clock()
        item = Inspection(uuid.uuid4().hex[:12], training_case, Stage.AWAITING_TAG, operator, now, now, None,
                          CallStatus.IDLE, None, None, 0)
        with transaction(self._db) as db:
            repo.insert_inspection(db, item, self._rule)
            repo.append_event(db, item, "inspection_opened", "Training inspection opened.", now,
                              None, Stage.AWAITING_TAG)
            self._refresh(db, item.id, now)
            return self._snapshot(db, item.id)

    def close(self, inspection_id: str) -> Snapshot:
        with transaction(self._db) as db:
            now, item = self._clock(), repo.get_inspection(db, inspection_id)
            target = next_stage(item.stage, Command.CLOSE)
            request = repo.active_photo_request(db)
            if request and request.inspection_id == inspection_id:
                repo.clear_photo_request(db)
            repo.append_event(db, replace(item, stage=target, active_capture_id=None), "inspection_closed",
                              "Inspection closed by the inspector.", now, item.stage, target)
            return self._snapshot(db, inspection_id)

    # Captures --------------------------------------------------------------

    def capture(self, inspection_id: str, kind: CaptureKind, data: bytes, source: CaptureSource,
                claim: InboundClaim | None = None) -> Snapshot:
        with transaction(self._db) as db:
            self._require_capture(repo.get_inspection(db, inspection_id), kind)
        try:
            analysis = self._analyze(kind, data)
        except CaptureError as error:
            self.note(inspection_id, "capture_unreadable", f"{SOURCE_LABELS[source]}: {error}")
            raise
        capture_id = uuid.uuid4().hex[:16]
        stored = self._store(inspection_id, capture_id, data, analysis.crop_png)
        try:
            with transaction(self._db) as db:
                snapshot, discard = self._record(db, inspection_id, kind, source, capture_id, analysis, stored, claim)
        except BaseException:
            for item in stored:
                self._evidence.delete(item.ref)
            raise
        self._discard(discard)
        return snapshot

    def confirm(self, inspection_id: str, capture_id: str, corrections: Identifiers | None = None) -> Snapshot:
        with transaction(self._db) as db:
            now, item = self._clock(), repo.get_inspection(db, inspection_id)
            candidate = self._active(db, item, capture_id)
            confirmed = merge_corrections(candidate.raw, corrections)
            target = next_stage(item.stage, Command.CONFIRM, candidate.kind)
            repo.update_capture(db, replace(candidate, status=CaptureStatus.CONFIRMED, confirmed=confirmed,
                                            confirmed_at=now))
            changed = [name for name in ("part", "serial")
                       if getattr(confirmed, name) != getattr(candidate.raw, name)]
            detail = f"{candidate.kind.capitalize()} confirmed by the inspector."
            if changed:
                detail += f" Corrected: {', '.join(changed)}."
            repo.append_event(db, replace(item, stage=target, active_capture_id=None), "capture_confirmed",
                              detail, now, item.stage, target)
            self._refresh(db, inspection_id, now)
            return self._snapshot(db, inspection_id)

    def retake(self, inspection_id: str, capture_id: str) -> Snapshot:
        with transaction(self._db) as db:
            now, item = self._clock(), repo.get_inspection(db, inspection_id)
            candidate = self._active(db, item, capture_id)
            target = next_stage(item.stage, Command.RETAKE, candidate.kind)
            reviewed = any(c.kind is CaptureKind.CERTIFICATE and c.status is CaptureStatus.CONFIRMED
                           for c in repo.list_captures(db, inspection_id))
            if candidate.kind is CaptureKind.CERTIFICATE and reviewed:
                target = Stage.REVIEW_READY  # a replacement was rejected; the confirmed certificate stands
            repo.update_capture(db, replace(candidate, status=CaptureStatus.REJECTED, crop_ref=None))
            repo.append_event(db, replace(item, stage=target, active_capture_id=None), "capture_retaken",
                              f"{candidate.kind.capitalize()} reading rejected. Retake requested.", now,
                              item.stage, target)
            self._refresh(db, inspection_id, now)
            snapshot = self._snapshot(db, inspection_id)
        self._discard((candidate.crop_ref,))
        return snapshot

    # iMessage photos -------------------------------------------------------

    def expect_photo(self, inspection_id: str, kind: CaptureKind) -> Snapshot:
        with transaction(self._db) as db:
            now, item = self._clock(), repo.get_inspection(db, inspection_id)
            self._require_capture(item, kind)
            repo.arm_photo_request(db, PhotoRequest(inspection_id, kind, now, now + PHOTO_WINDOW_SECONDS,
                                                    uuid.uuid4().hex))
            repo.append_event(db, item, "photo_expected",
                              f"Expecting one {kind} photo from the verified test phone for 10 minutes.", now)
            return self._snapshot(db, inspection_id)

    def claim_inbound(self, photo: InboundPhoto, allowed_sender: str) -> InboundClaim | None:
        """Match a verified inbound photo to the single armed request, without consuming it."""
        if not allowed_sender or photo.sender != allowed_sender:
            return None
        with transaction(self._db) as db:
            if repo.seen_inbound(db, photo.event_id):
                raise DuplicateEvent(photo.event_id)
            request = repo.active_photo_request(db)
        now = self._clock()
        if (request is None or now >= request.expires_at
                or photo.sent_at < request.armed_at - CLOCK_SKEW_SECONDS):
            return None
        return InboundClaim(request.inspection_id, request.kind, photo.event_id, request.token)

    def mark_inbound_seen(self, event_id: str) -> None:
        """Record a terminally handled event (e.g. unreadable photo) so replays are ignored."""
        with transaction(self._db) as db:
            if not repo.seen_inbound(db, event_id):
                repo.record_inbound(db, event_id, self._clock())

    # Call lifecycle and notes (never change the inspection stage) ---------

    def update_call(self, inspection_id: str, status: CallStatus, detail: str,
                    call_id: str | None = None, room_id: str | None = None) -> Snapshot:
        with transaction(self._db) as db:
            now, item = self._clock(), repo.get_inspection(db, inspection_id)
            updated = replace(item, call_status=status, call_id=call_id or item.call_id,
                              room_id=room_id or item.room_id)
            repo.append_event(db, updated, f"call_{status}", detail, now)
            return self._snapshot(db, inspection_id)

    def note(self, inspection_id: str, kind: str, detail: str) -> None:
        with transaction(self._db) as db:
            repo.append_event(db, repo.get_inspection(db, inspection_id), kind, detail, self._clock())

    # Internals -------------------------------------------------------------

    def _snapshot(self, db: sqlite3.Connection, inspection_id: str) -> Snapshot:
        item = repo.get_inspection(db, inspection_id)
        rule, captures = repo.get_rule(db, inspection_id), repo.list_captures(db, inspection_id)
        findings = repo.list_findings(db, inspection_id)
        partial = Snapshot(item, rule, captures, findings, repo.list_events(db, inspection_id),
                           review_status(findings), None)
        return replace(partial, issuer_draft=issuer_request_draft(partial.confirmed(CaptureKind.CERTIFICATE), rule))

    def _refresh(self, db: sqlite3.Connection, inspection_id: str, now: float) -> None:
        captures = repo.list_captures(db, inspection_id)

        def latest(kind: CaptureKind, statuses: set[CaptureStatus]) -> Capture | None:
            matches = [c for c in captures if c.kind is kind and c.status in statuses]
            return matches[-1] if matches else None

        confirmed = {CaptureStatus.CONFIRMED}
        findings = evaluate(latest(CaptureKind.TAG, confirmed), latest(CaptureKind.CERTIFICATE, confirmed),
                            latest(CaptureKind.CERTIFICATE, {CaptureStatus.CONFIRMED, CaptureStatus.PENDING,
                                                             CaptureStatus.UNUSABLE}),
                            repo.get_rule(db, inspection_id))
        repo.replace_findings(db, inspection_id, findings, now)

    @staticmethod
    def _require_capture(item: Inspection, kind: CaptureKind) -> None:
        if not can_capture(item.stage, kind):
            raise WorkflowError(f"A {kind} capture is not expected while the inspection is {item.stage}.")

    @staticmethod
    def _active(db: sqlite3.Connection, item: Inspection, capture_id: str) -> Capture:
        if item.active_capture_id != capture_id:
            raise StaleCapture("That capture is no longer the active candidate. Reload and review the latest one.")
        candidate = repo.get_capture(db, item.id, capture_id)
        if candidate.status is not CaptureStatus.PENDING:
            raise StaleCapture("That capture was already resolved.")
        return candidate

    def _analyze(self, kind: CaptureKind, data: bytes) -> _Analysis:
        if kind is CaptureKind.TAG:
            tag = self._read_tag(data)
            return _Analysis(Identifiers(tag.part, tag.serial), None, "Training QR decoded.", (), None)
        cert = self._read_certificate(data)
        return _Analysis(Identifiers(cert.part, cert.serial, cert.description), cert.visibility, cert.reason,
                         cert.observations, cert.crop_png)

    def _store(self, inspection_id: str, capture_id: str, data: bytes,
               crop_png: bytes | None) -> tuple[StoredFile, ...]:
        original = self._evidence.save(inspection_id, capture_id, f"original.{image_type(data)}", data)
        if crop_png is None:
            return (original,)
        return original, self._evidence.save(inspection_id, capture_id, "crop.png", crop_png)

    def _consume_claim(self, db: sqlite3.Connection, inspection_id: str, claim: InboundClaim, now: float) -> None:
        if repo.seen_inbound(db, claim.event_id):
            raise DuplicateEvent(claim.event_id)
        request = repo.active_photo_request(db)
        if (request is None or request.token != claim.token or request.inspection_id != inspection_id
                or now >= request.expires_at):
            raise SupersededRequest("The photo request changed while the attachment was processed.")
        repo.record_inbound(db, claim.event_id, now)
        repo.clear_photo_request(db)

    def _record(self, db: sqlite3.Connection, inspection_id: str, kind: CaptureKind, source: CaptureSource,
                capture_id: str, analysis: _Analysis, stored: tuple[StoredFile, ...],
                claim: InboundClaim | None) -> tuple[Snapshot, tuple[str | None, ...]]:
        now, item = self._clock(), repo.get_inspection(db, inspection_id)
        self._require_capture(item, kind)
        if claim is not None:
            self._consume_claim(db, inspection_id, claim, now)
        usable = analysis.visibility is not Visibility.RETAKE
        crop = stored[1] if len(stored) > 1 else None
        repo.insert_capture(db, Capture(
            capture_id, inspection_id, kind, source, CaptureStatus.PENDING if usable else CaptureStatus.UNUSABLE,
            now, analysis.identifiers, None, None, analysis.visibility, analysis.reason, analysis.observations,
            stored[0].sha256, crop.sha256 if crop else None, stored[0].ref, crop.ref if crop else None))
        label, discard = SOURCE_LABELS[source], ()
        if not usable:
            repo.append_event(db, item, "capture_unusable", f"{label}: {analysis.reason}", now)
        else:
            if item.active_capture_id:
                previous = repo.get_capture(db, inspection_id, item.active_capture_id)
                repo.update_capture(db, replace(previous, status=CaptureStatus.REJECTED, crop_ref=None))
                item = repo.append_event(db, item, "capture_superseded",
                                         "A newer capture replaced the unconfirmed candidate.", now)
                discard = (previous.crop_ref,)
            target = next_stage(item.stage, Command.CAPTURE, kind)
            ids = analysis.identifiers
            detail = (f"{label}: tag reads part {ids.part}, serial {ids.serial}. Awaiting confirmation."
                      if kind is CaptureKind.TAG else f"{label}: {analysis.reason} Awaiting confirmation.")
            repo.append_event(db, replace(item, stage=target, active_capture_id=capture_id), "capture_recorded",
                              detail, now, item.stage, target)
        self._refresh(db, inspection_id, now)
        return self._snapshot(db, inspection_id), discard

    def _discard(self, refs: tuple[str | None, ...]) -> None:
        """Delete rejected crops after commit. A failure here must not undo a committed change."""
        for ref in refs:
            if not ref:
                continue
            try:
                self._evidence.delete(ref)
            except OSError as error:
                log.warning("Could not delete rejected evidence %s (%s).", ref, type(error).__name__)
