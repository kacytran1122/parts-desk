"""Immutable domain records shared by the workflow, rules, storage, and API."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class Stage(StrEnum):
    AWAITING_TAG = "awaiting_tag"
    CONFIRMING_TAG = "confirming_tag"
    AWAITING_CERTIFICATE = "awaiting_certificate"
    CONFIRMING_CERTIFICATE = "confirming_certificate"
    REVIEW_READY = "review_ready"
    CLOSED = "closed"


class CaptureKind(StrEnum):
    TAG = "tag"
    CERTIFICATE = "certificate"


class CaptureStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNUSABLE = "unusable"


class CaptureSource(StrEnum):
    FIXTURE = "training_fixture"
    UPLOAD = "inspector_upload"
    FACETIME = "facetime_frame"
    IMESSAGE = "chert_imessage"


class Visibility(StrEnum):
    PRESENT = "section_present"
    MISSING = "section_missing"
    UNRESOLVED = "unresolved"
    RETAKE = "retake"


class CallStatus(StrEnum):
    IDLE = "idle"
    DIALING = "dialing"
    RINGING = "ringing"
    CONNECTED = "connected"
    ENDING = "ending"
    ENDED = "ended"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    DECLINED = "declined"
    FAILED = "failed"


class Check(StrEnum):
    IDENTIFIERS = "identifier_agreement"
    TEMPLATE = "issuer_template"
    NOTICE_SCOPE = "faa_notice_scope"


class Result(StrEnum):
    AWAITING = "awaiting_evidence"
    MATCH = "match"
    DIFFERENT = "different_values"
    INSUFFICIENT = "insufficient_evidence"
    SECTION_PRESENT = "section_present"
    ISSUER_VERIFICATION = "issuer_verification_needed"
    HUMAN_REVIEW = "human_review"
    RETAKE = "retake_requested"
    IN_SCOPE = "part_named_in_notice"
    OUT_OF_SCOPE = "part_not_named_in_notice"


class ReviewStatus(StrEnum):
    AWAITING = "awaiting_evidence"
    RETAKE_OR_VERIFY = "retake_or_verify"
    INSPECTOR_REVIEW = "inspector_review_needed"
    CHECKS_RECORDED = "checks_recorded"


def frozen_map(values: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    return MappingProxyType(dict(values or {}))


@dataclass(frozen=True)
class Identifiers:
    part: str | None
    serial: str | None
    description: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {"part": self.part, "serial": self.serial, "description": self.description}

    @classmethod
    def from_dict(cls, values: Mapping[str, Any] | None) -> Identifiers | None:
        if values is None:
            return None
        return cls(values.get("part"), values.get("serial"), values.get("description"))


@dataclass(frozen=True)
class Capture:
    id: str
    inspection_id: str
    kind: CaptureKind
    source: CaptureSource
    status: CaptureStatus
    captured_at: float
    raw: Identifiers
    confirmed: Identifiers | None
    confirmed_at: float | None
    visibility: Visibility | None
    reason: str
    observations: tuple[str, ...]
    original_sha256: str
    crop_sha256: str | None
    original_ref: str | None
    crop_ref: str | None

    @property
    def effective(self) -> Identifiers:
        return self.confirmed if self.confirmed is not None else self.raw


@dataclass(frozen=True)
class RuleRevision:
    id: str
    version: int
    notice_id: str
    source_url: str
    scope: str
    predicates: Mapping[str, Any]

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


@dataclass(frozen=True)
class Finding:
    check: Check
    result: Result
    capture_ids: tuple[str, ...]
    values: Mapping[str, Any]
    rule: str | None = None


@dataclass(frozen=True)
class Inspection:
    id: str
    training_case: str
    stage: Stage
    operator: str
    created_at: float
    updated_at: float
    active_capture_id: str | None
    call_status: CallStatus
    call_id: str | None
    room_id: str | None
    last_seq: int


@dataclass(frozen=True)
class Event:
    inspection_id: str
    seq: int
    at: float
    type: str
    from_stage: Stage | None
    to_stage: Stage | None
    detail: str
