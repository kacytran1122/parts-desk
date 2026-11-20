"""Stateless analysis for the public training workspace."""
import base64
import hashlib
import sys
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vision import CaptureError, decode_image, decode_tag, inspect_photo

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
LIMIT = 4 * 1024 * 1024


@app.get("/api/health")
def health():
    return {"status": "ready", "storage": "browser", "mode": "training"}


def analyze(data, kind):
    image = decode_image(data)
    if kind == "tag":
        result, footer = decode_tag(image), None
    else:
        result, footer = inspect_photo(image)
    return {**result, "digest": hashlib.sha256(data).hexdigest(),
            "footer": base64.b64encode(footer).decode() if footer else None}


@app.post("/api/analyze/{kind}")
async def capture(kind: Literal["tag", "certificate"], request: Request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > LIMIT:
            raise HTTPException(413, "Use a photo under 4 MB for the public workspace.")
    try:
        return await run_in_threadpool(analyze, bytes(data), kind)
    except CaptureError as error:
        raise HTTPException(422, str(error)) from error
