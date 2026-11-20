from pathlib import Path

import pytest

from scripts import chert_setup
from test_api import make_client


def test_health_is_public_but_readiness_is_private(tmp_path: Path):
    client, _ = make_client(tmp_path)
    with client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert client.get("/api/config").status_code == 401


def test_register_saves_secret_privately_without_logging_it(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("PARTS_DESK_LIVE=0\nCHERT_WEBHOOK_SECRET=\n")
    monkeypatch.setenv("CHERT_API_KEY", "test-only-key")
    monkeypatch.setenv("CHERT_TENANT", "test-tenant")
    monkeypatch.delenv("CHERT_WEBHOOK_SECRET", raising=False)
    calls = []

    def api(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {"subscriptions": []}
        return {"subscription": {"secret": "private-test-subscription-secret"}}

    monkeypatch.setattr(chert_setup, "api", api)
    url = "https://example.com/api/chert/messages"
    assert chert_setup.register(url, env) == 0
    assert calls[-1][2] == {"url": url, "events": ["message.received"], "version": "2026-11-16"}
    assert env.stat().st_mode & 0o777 == 0o600
    assert "private-test-subscription-secret" in env.read_text()
    assert "private-test-subscription-secret" not in capsys.readouterr().out
    assert "PARTS_DESK_LIVE=0" in env.read_text()


def test_register_does_not_duplicate_existing_subscription(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("")
    monkeypatch.setenv("CHERT_API_KEY", "test-only-key")
    monkeypatch.setenv("CHERT_TENANT", "test-tenant")
    monkeypatch.delenv("CHERT_WEBHOOK_SECRET", raising=False)
    url = "https://example.com/api/chert/messages"

    def api(method, path, body=None):
        assert method == "GET"
        return {"subscriptions": [{"url": url}]}

    monkeypatch.setattr(chert_setup, "api", api)
    with pytest.raises(chert_setup.SetupError, match="already has a subscription"):
        chert_setup.register(url, env)


def test_register_rejects_insecure_callback_before_request(tmp_path):
    with pytest.raises(chert_setup.SetupError, match="https"):
        chert_setup.register("http://example.com/api/chert/messages", tmp_path / ".env")
