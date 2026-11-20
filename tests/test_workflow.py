import pytest

from partsdesk.domain import CaptureKind, Stage
from partsdesk.workflow import Command, WorkflowError, can_capture, next_stage

TAG, CERT = CaptureKind.TAG, CaptureKind.CERTIFICATE


def test_happy_path_walks_tag_then_certificate_to_review() -> None:
    stage = Stage.AWAITING_TAG
    for command, kind in [(Command.CAPTURE, TAG), (Command.CONFIRM, TAG),
                          (Command.CAPTURE, CERT), (Command.CONFIRM, CERT)]:
        stage = next_stage(stage, command, kind)
    assert stage is Stage.REVIEW_READY


def test_retake_returns_to_the_matching_awaiting_stage() -> None:
    assert next_stage(Stage.CONFIRMING_TAG, Command.RETAKE, TAG) is Stage.AWAITING_TAG
    assert next_stage(Stage.CONFIRMING_CERTIFICATE, Command.RETAKE, CERT) is Stage.AWAITING_CERTIFICATE


def test_new_capture_replaces_pending_candidate_without_advancing() -> None:
    assert next_stage(Stage.CONFIRMING_TAG, Command.CAPTURE, TAG) is Stage.CONFIRMING_TAG


def test_reviewed_inspection_accepts_a_replacement_certificate() -> None:
    assert next_stage(Stage.REVIEW_READY, Command.CAPTURE, CERT) is Stage.CONFIRMING_CERTIFICATE


@pytest.mark.parametrize("stage,command,kind", [
    (Stage.AWAITING_TAG, Command.CAPTURE, CERT),
    (Stage.AWAITING_TAG, Command.CONFIRM, TAG),
    (Stage.AWAITING_CERTIFICATE, Command.CAPTURE, TAG),
    (Stage.REVIEW_READY, Command.CONFIRM, CERT),
    (Stage.CLOSED, Command.CAPTURE, TAG),
])
def test_out_of_order_commands_are_rejected(stage: Stage, command: Command, kind: CaptureKind) -> None:
    with pytest.raises(WorkflowError):
        next_stage(stage, command, kind)


def test_close_is_allowed_once_from_any_open_stage() -> None:
    assert next_stage(Stage.CONFIRMING_TAG, Command.CLOSE) is Stage.CLOSED
    with pytest.raises(WorkflowError):
        next_stage(Stage.CLOSED, Command.CLOSE)


def test_can_capture_reflects_the_transition_table() -> None:
    assert can_capture(Stage.AWAITING_TAG, TAG)
    assert not can_capture(Stage.AWAITING_TAG, CERT)
    assert can_capture(Stage.REVIEW_READY, CERT)
    assert not can_capture(Stage.CLOSED, CERT)
