"""App construction: settings check, service wiring, error mapping, security headers, static UI.

Run with: uv run uvicorn --factory partsdesk.api.app:create_app --host 127.0.0.1 --port 8000
Use a single worker; the call guard and caller preview live in process memory.
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from partsdesk.api import routes_chert, routes_inspections, routes_live
from partsdesk.api.auth import require_operator
from partsdesk.api.deps import FAILED_LOGIN_LIMIT, WEBHOOK_LIMIT, WRITE_LIMIT, SlidingWindow
from partsdesk.chert.client import ChertError
from partsdesk.config import Settings
from partsdesk.live.frames import FrameStore
from partsdesk.live.manager import CallBusy, LiveCalls
from partsdesk.rules import GE_FOOTER_RULE
from partsdesk.service import (CorrectionError, InspectionService, NotFound, StaleCapture, SupersededRequest,
                               WorkflowError)
from partsdesk.storage.evidence import EvidenceStore
from partsdesk.vision.images import CaptureError

STATIC = Path(__file__).resolve().parents[1] / "static"
HTTP_TIMEOUT_SECONDS = 12.0
log = logging.getLogger("partsdesk")

SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; "
                                "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
                                "form-action 'self'"),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

ERRORS: dict[type[Exception], int] = {
    NotFound: 404, WorkflowError: 409, StaleCapture: 409, SupersededRequest: 409, CallBusy: 409,
    CaptureError: 422, CorrectionError: 422, ChertError: 502,
}
NO_STORE = {"Cache-Control": "no-store"}


def _install_error_handlers(app: FastAPI) -> None:
    for error_type, status in ERRORS.items():
        async def handler(request: Request, error: Exception, status: int = status) -> JSONResponse:
            log.info("%s %s -> %s (%s)", request.method, request.url.path, status, type(error).__name__)
            return JSONResponse({"detail": str(error) or "Request failed."}, status_code=status, headers=NO_STORE)

        app.add_exception_handler(error_type, handler)

    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Internal error. The inspection record is unchanged."}, status_code=500,
                            headers={**SECURITY_HEADERS, **NO_STORE})

    app.add_exception_handler(Exception, unexpected)


def create_app(settings: Settings | None = None, *,
               http_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings.from_env(os.environ)
    problems = settings.problems()
    if problems:
        raise RuntimeError("Parts Desk configuration: " + " ".join(problems))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.http = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, transport=http_transport)
        app.state.calls = LiveCalls()
        try:
            yield
        finally:
            await app.state.calls.shutdown()
            await app.state.http.aclose()

    app = FastAPI(title="Parts Desk", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.service = InspectionService(settings.db_path, EvidenceStore(settings.evidence_dir), GE_FOOTER_RULE)
    app.state.frames = FrameStore()
    app.state.write_limiter = SlidingWindow(WRITE_LIMIT)
    app.state.webhook_limiter = SlidingWindow(WEBHOOK_LIMIT)
    app.state.failed_logins = SlidingWindow(FAILED_LOGIN_LIMIT)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # noqa: ANN001, ANN202 - Starlette signature
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    _install_error_handlers(app)
    app.include_router(routes_inspections.router)
    app.include_router(routes_live.router)
    app.include_router(routes_chert.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config", dependencies=[Depends(require_operator)])
    async def config() -> dict:
        return {"mode": "live" if settings.live else "drill", "facetime_ready": settings.facetime_ready,
                "messaging_ready": settings.messaging_ready}

    return app
