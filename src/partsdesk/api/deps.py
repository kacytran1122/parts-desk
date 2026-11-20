"""Request-scoped access to app singletons, path constraints, and per-client rate limits."""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Annotated

from fastapi import HTTPException, Path, Request

from partsdesk.config import Settings
from partsdesk.live.frames import FrameStore
from partsdesk.live.manager import LiveCalls
from partsdesk.service import InspectionService

InspectionId = Annotated[str, Path(pattern=r"^[a-f0-9]{12}$")]
CaptureId = Annotated[str, Path(pattern=r"^[a-f0-9]{16}$")]

WINDOW_SECONDS = 60.0
WRITE_LIMIT = 60
WEBHOOK_LIMIT = 120
FAILED_LOGIN_LIMIT = 10


class SlidingWindow:
    """Counts hits per key inside a rolling window. Empty keys are dropped."""

    def __init__(self, limit: int, window: float = WINDOW_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._limit, self._window, self._clock = limit, window, clock
        self._hits: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        now, hits = self._clock(), self._hits.get(key, deque())
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits

    def exceeded(self, key: str) -> bool:
        return len(self._recent(key)) >= self._limit

    def record(self, key: str) -> None:
        self._hits[key] = self._recent(key)
        self._hits[key].append(self._clock())

    def allow(self, key: str) -> bool:
        if self.exceeded(key):
            return False
        self.record(key)
        return True


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_service(request: Request) -> InspectionService:
    return request.app.state.service


def get_frames(request: Request) -> FrameStore:
    return request.app.state.frames


def get_calls(request: Request) -> LiveCalls:
    return request.app.state.calls


def mode(settings: Settings) -> str:
    return "live" if settings.live else "drill"


def limit_writes(request: Request) -> None:
    if not request.app.state.write_limiter.allow(client_key(request)):
        raise HTTPException(429, "Too many requests. Wait a moment and retry.")


def limit_webhook(request: Request) -> None:
    if not request.app.state.webhook_limiter.allow(client_key(request)):
        raise HTTPException(429, "Too many webhook deliveries.")
