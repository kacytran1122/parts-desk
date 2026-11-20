"""Deterministic capture checks for this exercise's printed template."""
import io
import re
import threading

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from rapidocr_onnxruntime import RapidOCR

MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 24_000_000
PAGE = (1200, 780)
MARKER_SIZE = 76
ANCHORS = {10: (40, 35), 11: (1084, 35), 12: (40, 660), 13: (1084, 660)}
IDS = (90, 280, 1110, 461)
FOOT = (135, 558, 1064, 678)
FOOT_CHECK = (135, 686, 1064, 730)
FOOT_EVIDENCE = (115, 540, 1084, 738)
PART = "1856M94P01"
FAA_URL = "https://www.faa.gov/aircraft/safety/programs/sups/upn/2023-AAE-EHL-20230801-713"
_ocr_lock = threading.Lock()
_ocr = None


class CaptureError(ValueError):
    pass


def decode_image(data):
    if not data or len(data) > MAX_BYTES:
        raise CaptureError("Use a JPEG or PNG under 12 MB.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG"} or source.width * source.height > MAX_PIXELS:
                raise CaptureError("Use a JPEG or PNG under 24 megapixels.")
            rgb = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise CaptureError("The image could not be read. Retake it as a JPEG or PNG.") from error
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def png(image):
    return cv2.imencode(".png", image)[1].tobytes()


def decode_tag(image):
    candidate = image
    if max(image.shape[:2]) > 1800:
        scale = 1800 / max(image.shape[:2])
        candidate = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    text, points, _ = cv2.QRCodeDetector().detectAndDecode(candidate)
    match = re.fullmatch(r"PD1\|([A-Z0-9]{5,18})\|([0-9]{5})", text)
    if not match or points is None or min(np.linalg.norm(points[0] - np.roll(points[0], 1, axis=0), axis=1)) < 60:
        raise CaptureError("No readable Parts Desk training QR. Show the full code, closer to the camera.")
    return {"part": match[1], "serial": match[2]}


def align_photo(image):
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    observed, ids, _ = cv2.aruco.ArucoDetector(dictionary).detectMarkers(image)
    if ids is None:
        return None
    matches = {int(code): corners[0] for code, corners in zip(ids.ravel(), observed) if int(code) in ANCHORS}
    if set(matches) != set(ANCHORS):
        return None
    source, target = [], []
    for code, (x, y) in ANCHORS.items():
        corners = matches[code]
        if min(np.linalg.norm(corners - np.roll(corners, 1, axis=0), axis=1)) < 34:
            return None
        source.extend(corners)
        target.extend([(x, y), (x + MARKER_SIZE - 1, y), (x + MARKER_SIZE - 1, y + MARKER_SIZE - 1), (x, y + MARKER_SIZE - 1)])
    transform, inliers = cv2.findHomography(np.float32(source), np.float32(target), cv2.RANSAC, 4)
    if transform is None or inliers is None or int(inliers.sum()) < 14:
        return None
    boundary = cv2.perspectiveTransform(
        np.float32([[[135, 554], [1064, 554], [1064, 735], [135, 735]]]),
        np.linalg.inv(transform),
    )[0]
    height, width = image.shape[:2]
    if not all(0 <= x < width and 0 <= y < height for x, y in boundary):
        return None
    return cv2.warpPerspective(image, transform, PAGE, borderValue=(255, 255, 255))


def crop(image, box):
    x1, y1, x2, y2 = box
    return image[y1:y2, x1:x2]


def read_lines(image):
    global _ocr
    with _ocr_lock:
        if _ocr is None:
            _ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        result, _ = _ocr(image)
    return [{"text": text, "confidence": round(float(score), 3)} for _, text, score in (result or [])]


def field(lines, expression):
    for line in lines:
        if line["confidence"] >= .75:
            match = re.search(expression, line["text"].upper())
            if match:
                return match[1]
    return None


def inspect_photo(image):
    aligned = align_photo(image)
    unreadable = {"status": "retake", "part": None, "serial": None, "description": None, "observations": []}
    if aligned is None:
        return {**unreadable, "reason": "Show all four markers and the entire footer in a sharp photo."}, None
    footer = crop(aligned, FOOT)
    evidence = png(crop(aligned, FOOT_EVIDENCE))
    control = crop(aligned, FOOT_CHECK)
    reference = read_lines(control)
    if not field(reference, r"(FOOTER\s*CHECK\s*PD[0O]7)") or cv2.Laplacian(control, cv2.CV_64F).var() < 65:
        return {**unreadable, "reason": "The footer reference is unreadable. Retake with less glare or blur."}, evidence
    fields = read_lines(crop(aligned, IDS))
    # Each value is parsed from its own labelled line. Ambiguous reads stay unresolved.
    pn = field(fields, r"^8\s*PART\s*NUMBER\s*:?\s*([A-Z0-9]{5,18})$")
    sn = field(fields, r"^10\s*SERIAL\s*NUMBER\s*:?\s*([0-9]{5})$")
    description = field(fields, r"^7\s*DESCRIPTION\s*:?\s*(.+)$")
    lines = read_lines(cv2.copyMakeBorder(footer, 18, 18, 4, 4, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    observed_text = "".join(item["text"].upper() for item in lines if item["confidence"] >= .75)
    heading = re.search(r"USER[IL]NSTALLER[A-Z]?RESPONSIBILITIES", re.sub(r"[^A-Z]", "", observed_text))
    # A visibly blank region can establish absence on this template. Unrecognized
    # ink could be degraded text, so it requires human review or a retake.
    gray = cv2.cvtColor(footer, cv2.COLOR_BGR2GRAY)
    blank = np.mean(gray < 175) < .002
    result = {"part": pn, "serial": sn, "description": description, "observations": lines}
    if heading:
        return {**result, "status": "present", "reason": "The expected section heading was read in the footer."}, evidence
    if blank:
        return {**result, "status": "missing", "reason": "The expected section area is in frame and blank. The footer reference was readable."}, evidence
    return {**result, "status": "unresolved", "reason": "The footer contains marks the reader could not verify. Inspect the crop or retake the photo."}, evidence
