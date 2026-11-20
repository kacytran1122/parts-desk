"""Settings from the environment. Values are never logged or returned by the API."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

E164 = re.compile(r"\+[1-9][0-9]{7,14}")
MIN_TOKEN_LENGTH = 24
MESSAGING_KEYS = ("CHERT_TENANT", "CHERT_API_KEY", "CHERT_WEBHOOK_SECRET")
FACETIME_KEYS = ("CHERT_PROJECT_ID", "CHERT_PROJECT_KEY", "CHERT_FACETIME_LINE_ID",
                 "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ops_token: str = ""
    live: bool = False
    test_phone: str = ""
    chert_tenant: str = ""
    chert_api_key: str = ""
    chert_webhook_secret: str = ""
    chert_project_id: str = ""
    chert_project_key: str = ""
    chert_facetime_line_id: str = ""
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        def value(name: str) -> str:
            return env.get(name, "").strip()

        return cls(
            data_dir=Path(value("PARTS_DESK_DATA_DIR") or "data"),
            ops_token=value("PARTS_DESK_OPS_TOKEN"),
            live=value("PARTS_DESK_LIVE") == "1",
            test_phone=value("PARTS_DESK_TEST_PHONE"),
            chert_tenant=value("CHERT_TENANT"),
            chert_api_key=value("CHERT_API_KEY"),
            chert_webhook_secret=value("CHERT_WEBHOOK_SECRET"),
            chert_project_id=value("CHERT_PROJECT_ID"),
            chert_project_key=value("CHERT_PROJECT_KEY"),
            chert_facetime_line_id=value("CHERT_FACETIME_LINE_ID"),
            livekit_url=value("LIVEKIT_URL"),
            livekit_api_key=value("LIVEKIT_API_KEY"),
            livekit_api_secret=value("LIVEKIT_API_SECRET"),
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "parts-desk.sqlite3"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def phone_ready(self) -> bool:
        return self.live and bool(E164.fullmatch(self.test_phone))

    @property
    def messaging_ready(self) -> bool:
        return self.phone_ready and bool(self.chert_api_key and self.chert_webhook_secret and self.chert_tenant)

    @property
    def facetime_ready(self) -> bool:
        return self.phone_ready and self._secure_livekit_url() and all((
            self.chert_project_id, self.chert_project_key, self.chert_facetime_line_id,
            self.livekit_api_key, self.livekit_api_secret,
        ))

    def _secure_livekit_url(self) -> bool:
        try:
            url = urlsplit(self.livekit_url)
            return (url.scheme == "wss" and bool(url.hostname) and url.port in {None, 443} and
                    url.username is None and url.password is None and not url.fragment)
        except ValueError:
            return False

    def problems(self) -> tuple[str, ...]:
        """Configuration problems that block a safe start (no values included)."""
        issues = []
        if self.ops_token and len(self.ops_token) < MIN_TOKEN_LENGTH:
            issues.append(f"PARTS_DESK_OPS_TOKEN must be at least {MIN_TOKEN_LENGTH} characters.")
        if self.live and not self.ops_token:
            issues.append("PARTS_DESK_OPS_TOKEN is required when PARTS_DESK_LIVE=1.")
        if self.live and not E164.fullmatch(self.test_phone):
            issues.append("PARTS_DESK_TEST_PHONE must be one +E.164 number in live mode.")
        if self.live:
            for key in (*MESSAGING_KEYS, *FACETIME_KEYS):
                if not getattr(self, key.lower()):
                    issues.append(f"{key} is required when PARTS_DESK_LIVE=1.")
            if self.livekit_url and not self._secure_livekit_url():
                issues.append("LIVEKIT_URL must use wss with the default port or port 443 and no embedded credentials.")
        return tuple(issues)
