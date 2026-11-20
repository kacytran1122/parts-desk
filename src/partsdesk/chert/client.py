"""Chert HTTP clients: authenticated attachment download and FaceTime call control."""
from __future__ import annotations

import re
from typing import Any

import httpx

from partsdesk.vision.images import MAX_BYTES, CaptureError, image_type

MESSAGING_BASE = "https://console.trychert.com/api/v1"
FACETIME_BASE = "https://facetime.trychert.com/api/v1/calls"
IMAGE_TYPES = {"image/jpeg", "image/png"}
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}")


class ChertError(Exception):
    """A Chert request failed. Messages never include credentials."""


def _require_id(value: str) -> str:
    if not _ID.fullmatch(value or ""):
        raise ChertError("Invalid Chert identifier.")
    return value


class ChertMessaging:
    def __init__(self, http: httpx.AsyncClient, api_key: str, tenant: str = "",
                 base: str = MESSAGING_BASE) -> None:
        self._http, self._base = http, base
        self._headers = {"Authorization": f"Bearer {api_key}"}
        if tenant:
            self._headers["x-chert-tenant"] = tenant

    async def download_attachment(self, attachment_id: str, max_bytes: int = MAX_BYTES) -> bytes:
        url = f"{self._base}/attachments/{_require_id(attachment_id)}/content"
        try:
            async with self._http.stream("GET", url, headers=self._headers) as response:
                response.raise_for_status()
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if kind and kind not in IMAGE_TYPES | {"application/octet-stream"}:
                    raise ChertError("The attachment is not a JPEG or PNG image.")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise ChertError("The attachment exceeds 12 MB.")
                result = bytes(data)
                image_type(result)
                return result
        except CaptureError as error:
            raise ChertError("The Chert attachment bytes must be JPEG or PNG.") from error
        except httpx.HTTPError as error:
            raise ChertError("Could not retrieve the Chert attachment.") from error


class ChertFaceTime:
    def __init__(self, http: httpx.AsyncClient, project_id: str, project_key: str,
                 base: str = FACETIME_BASE) -> None:
        self._http, self._base = http, base
        self._headers = {"Authorization": f"Bearer {project_key}", "X-Chert-Project-Id": project_id}

    async def _json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._http.request(method, url, headers={**self._headers, **kwargs.pop("headers", {})},
                                                **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ChertError(f"Chert FaceTime {method} failed ({type(error).__name__}).") from error

    async def create_call(self, body: dict[str, Any], idempotency_key: str) -> str:
        """Create the outbound call. A retry reuses the same key and request body."""
        headers = {**self._headers, "Idempotency-Key": idempotency_key}
        response = None
        for attempt in range(2):
            try:
                response = await self._http.post(self._base, headers=headers, json=body)
            except httpx.TransportError as error:
                if attempt == 1:
                    raise ChertError("Chert FaceTime is unreachable.") from error
                continue
            if response.status_code < 500:
                break
        if response is None or response.status_code != 201:
            status = response.status_code if response is not None else "none"
            raise ChertError(f"Chert call allocation returned HTTP {status}. Check line readiness and recipient.")
        try:
            return _require_id(str(response.json()["data"]["id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ChertError("Chert returned an unexpected call response.") from error

    async def status(self, call_id: str) -> str:
        payload = await self._json("GET", f"{self._base}/{_require_id(call_id)}")
        return str((payload.get("data") or {}).get("status", "")).lower()

    async def events(self, call_id: str) -> dict[str, Any]:
        return await self._json("GET", f"{self._base}/{_require_id(call_id)}/events")

    async def end(self, call_id: str, idempotency_key: str) -> None:
        await self._json("POST", f"{self._base}/{_require_id(call_id)}/end",
                         headers={"Idempotency-Key": idempotency_key}, json={})
