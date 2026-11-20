"""Operator authentication. Only the signed Chert webhook is reachable without it.

Without a token, only a direct localhost drill is trusted: the socket peer and the Host
header must both be local and no proxy headers may be present (defeats DNS rebinding and
tunnels). Failed attempts are throttled per client.
"""
from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request

from partsdesk.api.deps import client_key, get_settings
from partsdesk.config import Settings

LOCAL_PEERS = {"127.0.0.1", "::1"}
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
FORWARDING_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")


def _local_drill(request: Request, settings: Settings) -> bool:
    return (not settings.live and not settings.ops_token and request.client is not None
            and request.client.host in LOCAL_PEERS and request.url.hostname in LOCAL_HOSTNAMES
            and not any(name in request.headers for name in FORWARDING_HEADERS))


def require_operator(request: Request, settings: Settings = Depends(get_settings)) -> str:
    failures, key = request.app.state.failed_logins, client_key(request)
    if failures.exceeded(key):
        raise HTTPException(429, "Too many failed sign-in attempts. Wait a minute and retry.")
    if settings.ops_token:
        supplied = request.headers.get("authorization", "")
        if hmac.compare_digest(supplied.encode(), f"Bearer {settings.ops_token}".encode()):
            return "operator"
    elif _local_drill(request, settings):
        origin = request.headers.get("origin")
        if request.method != "GET" and origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-site requests are not accepted.")
        return "local-operator"
    failures.record(key)
    raise HTTPException(401, "Enter the inspector access token.")
