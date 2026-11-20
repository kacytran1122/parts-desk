"""Bounded CPU OCR. One shared RapidOCR engine, serialized by a lock."""
from __future__ import annotations

import sys
import threading

import numpy as np

_lock = threading.Lock()
_engine = None
MIN_CONFIDENCE = 0.75


def read_lines(image: np.ndarray) -> tuple[tuple[str, float], ...]:
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        result, _ = _engine(image)
    return tuple((str(text), round(float(score), 3)) for _, text, score in (result or []))


def confident(lines: tuple[tuple[str, float], ...]) -> tuple[str, ...]:
    return tuple(text for text, score in lines if score >= MIN_CONFIDENCE)


def warm() -> None:
    read_lines(np.full((64, 256, 3), 255, dtype=np.uint8))


if __name__ == "__main__":  # pragma: no cover - devcontainer prewarm hook
    if "--warm" in sys.argv:
        warm()
