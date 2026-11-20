"""Bounded image decoding. Only JPEG and PNG are accepted."""
from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 24_000_000


class CaptureError(ValueError):
    """The capture cannot be read; the message is safe to show the operator."""


def image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    raise CaptureError("Use a JPEG or PNG image.")


def decode_image(data: bytes) -> np.ndarray:
    if not data or len(data) > MAX_BYTES:
        raise CaptureError("Use a JPEG or PNG under 12 MB.")
    image_type(data)
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG"} or source.width * source.height > MAX_PIXELS:
                raise CaptureError("Use a JPEG or PNG under 24 megapixels.")
            rgb = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise CaptureError("The image could not be read. Retake it as a JPEG or PNG.") from error
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def encode_png(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise CaptureError("Could not encode the evidence crop.")
    return buffer.tobytes()
