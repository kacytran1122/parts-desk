"""Straighten the certificate photo, then read blocks 7, 8, 10 and the footer independently."""
from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from partsdesk.domain import Visibility
from partsdesk.vision.images import decode_image, encode_png
from partsdesk.vision.ocr import confident, read_lines
from partsdesk.vision.template import (ANCHORS, BLOCK_7_DESCRIPTION, BLOCK_8_PART, BLOCK_10_SERIAL,
                                       FOOTER_EVIDENCE, FOOTER_FRAME, FOOTER_REFERENCE,
                                       FOOTER_REFERENCE_TEXT, FOOTER_SECTION, MARKER_SIZE,
                                       MIN_MARKER_EDGE, PAGE, Box)

MIN_HOMOGRAPHY_INLIERS = 14
MIN_REFERENCE_SHARPNESS = 65.0
INK_LEVEL = 175
BLANK_INK_RATIO = 0.002
HEADING = re.compile(r"USER[IL]NSTALLER[A-Z]?RESPONSIBILITIES")


@dataclass(frozen=True)
class CertificateReading:
    visibility: Visibility
    reason: str
    part: str | None
    serial: str | None
    description: str | None
    observations: tuple[str, ...]
    crop_png: bytes | None


def _marker_targets(x: int, y: int) -> list[tuple[int, int]]:
    edge = MARKER_SIZE - 1
    return [(x, y), (x + edge, y), (x + edge, y + edge), (x, y + edge)]


def align(image: np.ndarray) -> np.ndarray | None:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    observed, ids, _ = cv2.aruco.ArucoDetector(dictionary).detectMarkers(image)
    if ids is None:
        return None
    found = {int(code): corners[0] for code, corners in zip(ids.ravel(), observed, strict=True) if int(code) in ANCHORS}
    if set(found) != set(ANCHORS):
        return None
    source, target = [], []
    for code, (x, y) in ANCHORS.items():
        corners = found[code]
        if min(np.linalg.norm(corners - np.roll(corners, 1, axis=0), axis=1)) < MIN_MARKER_EDGE:
            return None
        source.extend(corners)
        target.extend(_marker_targets(x, y))
    transform, inliers = cv2.findHomography(np.float32(source), np.float32(target), cv2.RANSAC, 4)
    if transform is None or inliers is None or int(inliers.sum()) < MIN_HOMOGRAPHY_INLIERS:
        return None
    frame = cv2.perspectiveTransform(np.float32([FOOTER_FRAME]), np.linalg.inv(transform))[0]
    height, width = image.shape[:2]
    if not all(0 <= x < width and 0 <= y < height for x, y in frame):
        return None
    return cv2.warpPerspective(image, transform, PAGE, borderValue=(255, 255, 255))


def crop(image: np.ndarray, box: Box, pad: int = 0) -> np.ndarray:
    x1, y1, x2, y2 = box
    region = image[y1:y2, x1:x2]
    if not pad:
        return region
    return cv2.copyMakeBorder(region, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255))


def read_block(page: np.ndarray, box: Box, pattern: str) -> str | None:
    for text in confident(read_lines(crop(page, box, pad=12))):
        match = re.search(pattern, text.upper().strip())
        if match:
            return match[1].strip()
    return None


def _retake(reason: str, crop_png: bytes | None = None) -> CertificateReading:
    return CertificateReading(Visibility.RETAKE, reason, None, None, None, (), crop_png)


def read_certificate(data: bytes) -> CertificateReading:
    page = align(decode_image(data))
    if page is None:
        return _retake("Show all four corner markers and the entire footer in a sharp photo.")
    evidence = encode_png(crop(page, FOOTER_EVIDENCE))
    reference = crop(page, FOOTER_REFERENCE)
    reference_read = any(re.search(FOOTER_REFERENCE_TEXT, text.upper())
                         for text in confident(read_lines(reference)))
    if not reference_read or cv2.Laplacian(reference, cv2.CV_64F).var() < MIN_REFERENCE_SHARPNESS:
        return _retake("The footer reference is unreadable. Retake with less glare or blur.", evidence)
    description = read_block(page, BLOCK_7_DESCRIPTION, r"^7\s*DESCRIPTION\s*:?\s*(.+)$")
    part = read_block(page, BLOCK_8_PART, r"^8\s*PART\s*NUMBER\s*:?\s*([A-Z0-9]{5,18})$")
    serial = read_block(page, BLOCK_10_SERIAL, r"^10\s*SERIAL\s*NUMBER\s*:?\s*([0-9]{5})$")
    section = crop(page, FOOTER_SECTION)
    observations = confident(read_lines(crop(page, FOOTER_SECTION, pad=18)))
    letters = re.sub(r"[^A-Z]", "", "".join(observations).upper())
    blank = float(np.mean(cv2.cvtColor(section, cv2.COLOR_BGR2GRAY) < INK_LEVEL)) < BLANK_INK_RATIO
    if HEADING.search(letters):
        visibility, reason = Visibility.PRESENT, "The expected section heading was read in the footer."
    elif blank:
        visibility, reason = (Visibility.MISSING,
                              "The expected section area is in frame and blank. The footer reference was readable.")
    else:
        visibility, reason = (Visibility.UNRESOLVED,
                              "The footer has marks the reader could not verify. Inspect the crop or retake.")
    return CertificateReading(visibility, reason, part, serial, description, observations, evidence)
