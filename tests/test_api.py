import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from partsdesk.api.app import create_app
from partsdesk.chert.webhook import sign
from partsdesk.config import Settings

TOKEN = "test-operator-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FIXTURES = Path(__file__).resolve().parents[1] / "src/partsdesk/static/fixtures"
PHONE = "+15550100"
LIVE = {"PARTS_DESK_LIVE": "1", "PARTS_DESK_TEST_PHONE": PHONE, "CHERT_API_KEY": "k",
        "CHERT_WEBHOOK_SECRET": "whsec", "CHERT_TENANT": "tenant", "CHERT_PROJECT_ID": "p",
        "CHERT_PROJECT_KEY": "pk", "CHERT_FACETIME_LINE_ID": "line", "LIVEKIT_URL": "wss://lk",
        "LIVEKIT_API_KEY": "a", "LIVEKIT_API_SECRET": "b"}


def make_client(tmp_path: Path, env: dict | None = None, transport: httpx.MockTransport | None = None):
    settings = Settings.from_env({"PARTS_DESK_DATA_DIR": str(tmp_path), "PARTS_DESK_OPS_TOKEN": TOKEN, **(env or {})})
    app = create_app(settings, http_transport=transport)
    return TestClient(app), app


def post(client: TestClient, path: str, body: dict | None = None):
    return client.post(path, headers=AUTH, json=body or {})


def start_with_confirmed_tag(client: TestClient) -> str:
    key = post(client, "/api/inspections").json()["id"]
    pending = post(client, f"/api/inspections/{key}/fixtures", {"variant": "tag"}).json()["pending"]
    post(client, f"/api/inspections/{key}/captures/{pending['id']}/confirm")
    return key


def test_operator_token_is_required(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        assert client.post("/api/inspections").status_code == 401
        assert client.post("/api/inspections", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert post(client, "/api/inspections").status_code == 200


def test_repeated_bad_tokens_are_throttled(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        codes = [client.get("/api/config", headers={"Authorization": "Bearer nope"}).status_code for _ in range(12)]
        assert codes[:10] == [401] * 10 and codes[-1] == 429


def test_local_drill_without_token_allows_same_origin_only(tmp_path: Path) -> None:
    settings = Settings.from_env({"PARTS_DESK_DATA_DIR": str(tmp_path)})
    local = TestClient(create_app(settings), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    rebound = TestClient(create_app(settings), base_url="http://attacker.example", client=("127.0.0.1", 50001))
    remote = TestClient(create_app(settings), client=("10.0.0.5", 50000))
    with local, rebound, remote:
        assert local.post("/api/inspections", json={}).status_code == 200
        assert local.post("/api/inspections", json={}, headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
        assert local.post("/api/inspections", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
        assert local.post("/api/inspections", json={}, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 401
        assert rebound.post("/api/inspections", json={}).status_code == 401  # DNS rebinding: Host is not local
        assert remote.post("/api/inspections", json={}).status_code == 401


def test_live_mode_refuses_to_start_without_a_strong_token(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        create_app(Settings.from_env({"PARTS_DESK_DATA_DIR": str(tmp_path), **LIVE}))
    with pytest.raises(RuntimeError):
        create_app(Settings.from_env({"PARTS_DESK_DATA_DIR": str(tmp_path), "PARTS_DESK_OPS_TOKEN": "short"}))


def test_drill_judging_case_over_http(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        key = start_with_confirmed_tag(client)
        body = post(client, f"/api/inspections/{key}/fixtures", {"variant": "missing"}).json()
        assert body["stage"] == "confirming_certificate" and body["pending"]["visibility"] == "section_missing"
        capture_id = body["pending"]["id"]
        body = post(client, f"/api/inspections/{key}/captures/{capture_id}/confirm").json()
        results = {item["check"]: item["result"] for item in body["findings"]}
        assert results == {"identifier_agreement": "match", "issuer_template": "issuer_verification_needed",
                           "faa_notice_scope": "part_named_in_notice"}
        assert body["review_status"] == "inspector_review_needed" and "00482" in body["issuer_draft"]
        assert client.get(f"/api/inspections/{key}", headers=AUTH).headers["cache-control"] == "no-store"

        crop = client.get(f"/api/inspections/{key}/captures/{capture_id}/crop", headers=AUTH)
        assert crop.headers["content-type"] == "image/png"
        assert crop.headers["cache-control"] == "no-store" and crop.headers["x-content-type-options"] == "nosniff"

        report = client.get(f"/api/inspections/{key}/report", headers=AUTH)
        assert "attachment" in report.headers["content-disposition"]
        assert report.json()["exported_at"] and report.json()["certificate"]["original_sha256"]


def test_correction_is_recorded_alongside_raw_value(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        key = start_with_confirmed_tag(client)
        pending = post(client, f"/api/inspections/{key}/fixtures", {"variant": "mismatch"}).json()["pending"]
        body = post(client, f"/api/inspections/{key}/captures/{pending['id']}/confirm", {"serial": "00482"}).json()
        assert body["certificate"]["raw"]["serial"] == "00428"
        assert body["certificate"]["corrected_fields"] == ["serial"]


def test_conflicts_and_validation_errors(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        key = post(client, "/api/inspections").json()["id"]
        first = post(client, f"/api/inspections/{key}/fixtures", {"variant": "tag"}).json()["pending"]["id"]
        post(client, f"/api/inspections/{key}/fixtures", {"variant": "tag"})
        stale = post(client, f"/api/inspections/{key}/captures/{first}/confirm")
        assert stale.status_code == 409 and "active candidate" in stale.json()["detail"]
        assert post(client, f"/api/inspections/{key}/fixtures", {"variant": "missing"}).status_code == 409
        assert post(client, f"/api/inspections/{key}/fixtures", {"variant": "../etc"}).status_code == 422
        assert client.get("/api/inspections/000000000000", headers=AUTH).status_code == 404
        assert client.get("/api/inspections/not-an-id", headers=AUTH).status_code == 422
        upload = client.post(f"/api/inspections/{key}/uploads/tag", headers=AUTH,
                             files={"photo": ("x.gif", b"GIF89a", "image/gif")})
        assert upload.status_code == 422


def test_upload_events_and_close(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        key = post(client, "/api/inspections").json()["id"]
        upload = client.post(f"/api/inspections/{key}/uploads/tag", headers=AUTH,
                             files={"photo": ("tag.png", (FIXTURES / "tag.png").read_bytes(), "image/png")})
        assert upload.json()["pending"]["source"] == "inspector_upload"
        events = client.get(f"/api/inspections/{key}/events?after=1", headers=AUTH).json()
        assert events["events"][0]["seq"] == 2 and events["last_seq"] >= 2
        assert post(client, f"/api/inspections/{key}/close").json()["stage"] == "closed"


def test_retake_returns_to_capture_stage(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        key = post(client, "/api/inspections").json()["id"]
        pending = post(client, f"/api/inspections/{key}/fixtures", {"variant": "tag"}).json()["pending"]
        body = post(client, f"/api/inspections/{key}/captures/{pending['id']}/retake").json()
        assert body["stage"] == "awaiting_tag" and body["pending"] is None


def test_live_only_routes_are_unavailable_in_drill_mode(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        key = post(client, "/api/inspections").json()["id"]
        assert post(client, f"/api/inspections/{key}/facetime").status_code == 503
        assert post(client, f"/api/inspections/{key}/expect-photo", {"kind": "tag"}).status_code == 503
        assert client.post("/api/chert/messages", content=b"{}").status_code == 503
        config = client.get("/api/config", headers=AUTH).json()
        assert config == {"mode": "drill", "facetime_ready": False, "messaging_ready": False}


def signed_webhook(client: TestClient, body: dict, secret: str = "whsec"):
    raw = json.dumps(body).encode()
    return client.post("/api/chert/messages", content=raw,
                       headers={"x-webhook-signature": sign(secret, raw, int(time.time())),
                                "content-type": "application/json"})


def message(event_id: str, sender: str = PHONE) -> dict:
    sent = datetime.fromtimestamp(time.time() + 1, tz=UTC).isoformat()
    return {"event_type": "message.received", "event_id": event_id, "partner_id": "tenant",
            "data": {"chat": {"is_group": False},
                     "message": {"direction": "inbound", "sender_handle": {"handle": sender}, "sent_at": sent,
                                 "parts": [{"type": "media", "attachment_id": "att_1"}]}}}


def test_signed_imessage_photo_lands_on_the_armed_case(tmp_path: Path) -> None:
    downloads = []

    def chert(request: httpx.Request) -> httpx.Response:
        downloads.append(request.headers["authorization"])
        return httpx.Response(200, headers={"content-type": "image/png"},
                              content=(FIXTURES / "missing.png").read_bytes())

    client, _ = make_client(tmp_path, LIVE, httpx.MockTransport(chert))
    with client:
        key = start_with_confirmed_tag(client)
        assert signed_webhook(client, message("evt_early")).json() == {"accepted": False}
        post(client, f"/api/inspections/{key}/expect-photo", {"kind": "certificate"})
        assert signed_webhook(client, message("evt_1", sender="+15559999")).json() == {"accepted": False}
        assert signed_webhook(client, message("evt_1"), secret="wrong").status_code == 401
        assert signed_webhook(client, message("evt_1")).json() == {"accepted": True}
        assert signed_webhook(client, message("evt_1")).json() == {"accepted": True, "duplicate": True}
        body = client.get(f"/api/inspections/{key}", headers=AUTH).json()
        assert body["pending"]["source"] == "chert_imessage" and downloads == ["Bearer k"]


def test_facetime_is_refused_on_a_closed_inspection(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, LIVE)
    with client:
        key = post(client, "/api/inspections").json()["id"]
        post(client, f"/api/inspections/{key}/close")
        refused = post(client, f"/api/inspections/{key}/facetime")
        assert refused.status_code == 409 and "closed" in refused.json()["detail"]


def test_capture_tag_from_live_frame(tmp_path: Path) -> None:
    client, app = make_client(tmp_path, LIVE)
    with client:
        key = post(client, "/api/inspections").json()["id"]
        assert post(client, f"/api/inspections/{key}/capture-tag").status_code == 409
        assert client.get(f"/api/inspections/{key}/preview", headers=AUTH).status_code == 204
        app.state.frames.put(key, (FIXTURES / "tag.png").read_bytes())
        body = post(client, f"/api/inspections/{key}/capture-tag").json()
        assert body["pending"]["source"] == "facetime_frame" and body["pending"]["raw"]["serial"] == "00482"
        assert client.get(f"/api/inspections/{key}/preview", headers=AUTH).status_code == 200


def test_index_has_security_headers(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        page = client.get("/")
        assert page.status_code == 200 and "Parts Desk" in page.text
        assert "default-src 'self'" in page.headers["content-security-policy"]
        assert page.headers["x-frame-options"] == "DENY"
