"""Latest caller frame per inspection, held in memory only and dropped when stale."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

MAX_FRAME_AGE_SECONDS = 3.0


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    at: float


class FrameStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._frames: dict[str, Frame] = {}
        self._clock = clock

    def put(self, inspection_id: str, jpeg: bytes) -> None:
        self._frames = {**self._frames, inspection_id: Frame(jpeg, self._clock())}

    def clear(self, inspection_id: str) -> None:
        self._frames = {key: value for key, value in self._frames.items() if key != inspection_id}

    def latest(self, inspection_id: str, max_age: float = MAX_FRAME_AGE_SECONDS) -> bytes | None:
        frame = self._frames.get(inspection_id)
        if frame is None or self._clock() - frame.at > max_age:
            return None
        return frame.jpeg
