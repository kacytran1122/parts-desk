"""Credential preflight and one-time webhook provisioning; never prints secrets.

uv run --extra facetime --env-file .env python scripts/chert_setup.py check
uv run --extra facetime --env-file .env python scripts/chert_setup.py register-webhook https://HOST/api/chert/messages
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from partsdesk.config import FACETIME_KEYS, MESSAGING_KEYS, Settings

BASE = "https://console.trychert.com/api/v1"


class SetupError(Exception):
    """A safe, operator-facing error with no credential values."""


def api(method: str, path: str, body: dict | None = None) -> dict:
    request = Request(BASE + path, method=method, headers={
        "Authorization": "Bearer " + os.environ["CHERT_API_KEY"],
        "x-chert-tenant": os.environ["CHERT_TENANT"],
        "Content-Type": "application/json",
    }, data=json.dumps(body).encode() if body is not None else None)
    # No redirect following: never forward credentials to another origin.
    from urllib.request import HTTPRedirectHandler, build_opener

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    with build_opener(NoRedirect).open(request, timeout=20) as response:
        return json.load(response)


def save_secret(path: Path, value: str) -> None:
    lines = path.read_text().splitlines()
    lines = [line for line in lines if not line.startswith("CHERT_WEBHOOK_SECRET=")]
    lines.append("CHERT_WEBHOOK_SECRET=" + json.dumps(value))
    fd, temporary = tempfile.mkstemp(prefix=".partsdesk-env-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def register(url: str, env_file: Path) -> int:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != "/api/chert/messages"):
        raise SetupError("Use https://YOUR-HOST/api/chert/messages without credentials or query parameters.")
    if env_file.is_symlink() or not env_file.is_file():
        raise SetupError("The private env file must already exist and must not be a symlink.")
    env_file.chmod(0o600)
    if not all(os.environ.get(key, "").strip() for key in ("CHERT_TENANT", "CHERT_API_KEY")):
        raise SetupError("Fill CHERT_TENANT and CHERT_API_KEY first, then run with --env-file .env.")
    if os.environ.get("CHERT_WEBHOOK_SECRET", "").strip():
        raise SetupError("A webhook secret is already configured; inspect the existing subscription before changing it.")
    subscriptions = api("GET", "/webhook-subscriptions")["subscriptions"]
    if any(item.get("url") == url for item in subscriptions):
        raise SetupError("This endpoint already has a subscription. Recover its saved secret; no duplicate was created.")
    # POST is deliberately not retried: Chert does not support idempotency here.
    result = api("POST", "/webhook-subscriptions", {
        "url": url, "events": ["message.received"], "version": "2026-11-16",
    })["subscription"]
    secret = result.get("secret")
    if not isinstance(secret, str) or not secret:
        raise SetupError("Chert did not return a subscription secret; inspect the subscription in its dashboard.")
    save_secret(env_file, secret)
    print("Webhook registered. Secret saved privately to the env file; copy it to the host environment.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "register-webhook"))
    parser.add_argument("url", nargs="?")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    if args.action == "register-webhook":
        if not args.url:
            parser.error("register-webhook needs the public HTTPS callback URL")
        return register(args.url, args.env_file)
    keys = ("PARTS_DESK_OPS_TOKEN", "PARTS_DESK_TEST_PHONE", *MESSAGING_KEYS, *FACETIME_KEYS)
    for key in keys:
        print(f"{key}: {'set' if os.environ.get(key, '').strip() else 'MISSING'}")
    # Validate intended live configuration without starting a call or sending a message.
    problems = Settings.from_env({**os.environ, "PARTS_DESK_LIVE": "1"}).problems()
    for problem in problems:
        print(problem)
    print("No calls placed or messages sent.")
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SetupError as error:
        print(str(error))
        raise SystemExit(1) from None
    except HTTPError as error:
        print(f"Chert returned HTTP {error.code}. No response body or credentials were logged.")
        raise SystemExit(1) from None
    except (URLError, OSError, ValueError, KeyError):
        # Avoid exception representations that could contain upstream response secrets.
        print("Setup could not finish. Check required fields, private file permissions, and existing subscriptions.")
        raise SystemExit(1) from None
