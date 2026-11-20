"""Decode the training tag QR. Identifiers stay strings so leading zeros survive."""
from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from partsdesk.vision.images import CaptureError, decode_image
from partsdesk.vision.template import QR_PAYLOAD

MAX_DECODE_EDGE = 1800
MIN_QR_EDGE = 60


@dataclass(frozen=True)
class TagReading:
    part: str
    serial: str


def _downscale(image: np.ndarray) -> np.ndarray:
    longest = max(image.shape[:2])
    if longest <= MAX_DECODE_EDGE:
        return image
    scale = MAX_DECODE_EDGE / longest
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def read_tag(data: bytes) -> TagReading:
    image = _downscale(decode_image(data))
    text, points, _ = cv2.QRCodeDetector().detectAndDecode(image)
    match = re.fullmatch(QR_PAYLOAD, text or "")
    if not match or points is None:
        raise CaptureError("No readable training QR. Show the full code, closer to the camera.")
    corners = points[0]
    if min(np.linalg.norm(corners - np.roll(corners, 1, axis=0), axis=1)) < MIN_QR_EDGE:
        raise CaptureError("The QR code is too small in frame. Move closer.")
    return TagReading(part=match[1], serial=match[2])
