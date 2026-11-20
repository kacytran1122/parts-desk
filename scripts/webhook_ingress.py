"""Forward only Chert's signed webhook from a public port to the private worksheet."""
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response

from partsdesk.chert.webhook import MAX_BODY_BYTES

PATH = "/api/chert/messages"
APP_WEBHOOK = "http://127.0.0.1:8000" + PATH


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(timeout=30) as client:
        app.state.http = client
        yield


relay = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@relay.post(PATH)
async def webhook(request: Request) -> Response:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "Webhook body too large.")
    signature = request.headers.get("x-webhook-signature")
    if not signature:
        raise HTTPException(401, "Missing webhook signature.")
    try:
        upstream = await request.app.state.http.post(
            APP_WEBHOOK,
            content=bytes(body),
            headers={"x-webhook-signature": signature, "content-type": "application/json"},
        )
    except httpx.HTTPError as error:
        raise HTTPException(503, "Webhook worker is unavailable.") from error
    return Response(upstream.content, status_code=upstream.status_code,
                    media_type="application/json", headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    uvicorn.run(relay, host="127.0.0.1", port=8789, proxy_headers=False)
