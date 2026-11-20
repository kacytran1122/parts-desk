"""Deterministic inspection state machine. Call lifecycle is tracked separately."""
from __future__ import annotations

from enum import StrEnum

from partsdesk.domain import CaptureKind, Stage


class Command(StrEnum):
    CAPTURE = "capture"
    CONFIRM = "confirm"
    RETAKE = "retake"
    CLOSE = "close"


class WorkflowError(Exception):
    """A command that is not allowed in the inspection's current stage."""


_TAG, _CERT = CaptureKind.TAG, CaptureKind.CERTIFICATE

TRANSITIONS: dict[tuple[Stage, Command, CaptureKind], Stage] = {
    (Stage.AWAITING_TAG, Command.CAPTURE, _TAG): Stage.CONFIRMING_TAG,
    (Stage.CONFIRMING_TAG, Command.CAPTURE, _TAG): Stage.CONFIRMING_TAG,
    (Stage.CONFIRMING_TAG, Command.CONFIRM, _TAG): Stage.AWAITING_CERTIFICATE,
    (Stage.CONFIRMING_TAG, Command.RETAKE, _TAG): Stage.AWAITING_TAG,
    (Stage.AWAITING_CERTIFICATE, Command.CAPTURE, _CERT): Stage.CONFIRMING_CERTIFICATE,
    (Stage.CONFIRMING_CERTIFICATE, Command.CAPTURE, _CERT): Stage.CONFIRMING_CERTIFICATE,
    (Stage.REVIEW_READY, Command.CAPTURE, _CERT): Stage.CONFIRMING_CERTIFICATE,
    (Stage.CONFIRMING_CERTIFICATE, Command.CONFIRM, _CERT): Stage.REVIEW_READY,
    (Stage.CONFIRMING_CERTIFICATE, Command.RETAKE, _CERT): Stage.AWAITING_CERTIFICATE,
}

_GUIDANCE = {
    Stage.AWAITING_TAG: "Capture the part tag first.",
    Stage.CONFIRMING_TAG: "Confirm or retake the tag reading.",
    Stage.AWAITING_CERTIFICATE: "Capture the certificate photo.",
    Stage.CONFIRMING_CERTIFICATE: "Confirm or retake the certificate reading.",
    Stage.REVIEW_READY: "Review the findings or submit a replacement certificate.",
    Stage.CLOSED: "This inspection is closed. Start a new one.",
}


def can_capture(stage: Stage, kind: CaptureKind) -> bool:
    return (stage, Command.CAPTURE, kind) in TRANSITIONS


def next_stage(stage: Stage, command: Command, kind: CaptureKind | None = None) -> Stage:
    if command is Command.CLOSE:
        if stage is Stage.CLOSED:
            raise WorkflowError(_GUIDANCE[stage])
        return Stage.CLOSED
    target = TRANSITIONS.get((stage, command, kind)) if kind else None
    if target is None:
        raise WorkflowError(_GUIDANCE[stage])
    return target
