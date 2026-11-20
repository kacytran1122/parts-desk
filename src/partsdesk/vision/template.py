"""Geometry of the training certificate template, in straightened page pixels."""
from __future__ import annotations

Box = tuple[int, int, int, int]

PAGE: tuple[int, int] = (1200, 780)
MARKER_SIZE = 76
MIN_MARKER_EDGE = 34
ANCHORS: dict[int, tuple[int, int]] = {10: (40, 35), 11: (1084, 35), 12: (40, 660), 13: (1084, 660)}

# Blocks 7, 8 and 10 are cropped by field position and read independently.
BLOCK_7_DESCRIPTION: Box = (90, 283, 1110, 339)
BLOCK_8_PART: Box = (90, 342, 1110, 399)
BLOCK_10_SERIAL: Box = (90, 402, 1110, 461)

FOOTER_SECTION: Box = (135, 558, 1064, 678)
FOOTER_REFERENCE: Box = (135, 686, 1064, 730)
FOOTER_EVIDENCE: Box = (115, 540, 1084, 738)
# The whole footer frame must project inside the photo before absence is evaluated.
FOOTER_FRAME: tuple[tuple[int, int], ...] = ((135, 554), (1064, 554), (1064, 735), (135, 735))

FOOTER_REFERENCE_TEXT = r"(FOOTER\s*CHECK\s*PD[0O]7)"
QR_PAYLOAD = r"PD1\|([A-Z0-9]{5,18})\|([0-9]{5})"
