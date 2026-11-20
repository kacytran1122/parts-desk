from pathlib import Path

import pytest

from partsdesk.domain import Visibility
from partsdesk.vision.certificate import read_certificate
from partsdesk.vision.images import CaptureError, image_type
from partsdesk.vision.qr import read_tag

FIXTURES = Path(__file__).resolve().parents[1] / "src/partsdesk/static/fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.png").read_bytes()


def test_tag_qr_reads_part_and_serial_with_leading_zeros() -> None:
    reading = read_tag(fixture("tag"))
    assert (reading.part, reading.serial) == ("1856M94P01", "00482")


def test_certificate_is_not_a_tag() -> None:
    with pytest.raises(CaptureError):
        read_tag(fixture("missing"))


def test_garbage_bytes_are_rejected() -> None:
    with pytest.raises(CaptureError):
        read_tag(b"GIF89a....")
    with pytest.raises(CaptureError):
        image_type(b"%PDF-1.7")


def test_judging_case_reads_blocks_and_finds_visible_blank_section() -> None:
    reading = read_certificate(fixture("missing"))
    assert reading.visibility is Visibility.MISSING
    assert (reading.part, reading.serial) == ("1856M94P01", "00482")
    assert reading.description == "BUSHING"
    assert reading.crop_png and reading.crop_png.startswith(b"\x89PNG")


def test_clean_certificate_shows_section_present() -> None:
    assert read_certificate(fixture("clean")).visibility is Visibility.PRESENT


def test_mismatch_keeps_the_certificate_serial_as_read() -> None:
    reading = read_certificate(fixture("mismatch"))
    assert reading.serial == "00428"


def test_clipped_footer_requests_retake() -> None:
    reading = read_certificate(fixture("cropped"))
    assert reading.visibility is Visibility.RETAKE
    assert reading.part is None


def test_printed_instruction_is_recorded_as_data_and_needs_review() -> None:
    reading = read_certificate(fixture("adversarial"))
    assert reading.visibility is Visibility.UNRESOLVED
    assert any("IGNORE" in line.upper() for line in reading.observations)
