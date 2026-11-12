"""Hosted mode: with an access code, setup and rules work remotely only when the code is given."""
from fastapi.testclient import TestClient

from invoice_agent.server import create_app


def test_access_code_gates_admin(tmp_path, monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PILOT_PROVIDER", "OPENAI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    app = create_app(access_code="s3cret-code", data_dir=tmp_path, env_file=tmp_path / ".env", verify=False)
    with TestClient(app, client=("203.0.113.9", 5000)) as c:          # a remote visitor, not localhost
        assert c.get("/api/config").json()["needs_code"] is True
        body = {"provider": "openai", "api_key": "sk-test-1234567890", "model": "gpt-6.1-sol"}
        assert c.post("/api/settings", json=body).status_code == 401
        assert c.get("/api/rules").status_code == 401
        h = {"x-access-code": "s3cret-code"}
        assert c.get("/api/config", headers=h).json()["can_save_key"] is True
        assert c.post("/api/settings", json=body, headers=h).status_code == 200
        assert c.get("/api/rules", headers=h).status_code == 200
        assert c.post("/api/check", headers={"x-access-code": "wrong"}).status_code == 401
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_no_code_means_local_only(tmp_path):
    app = create_app(data_dir=tmp_path, env_file=tmp_path / ".env", verify=False)
    with TestClient(app, client=("203.0.113.9", 5000)) as c:
        assert c.get("/api/config").json()["can_save_key"] is False
        assert c.get("/api/rules").status_code == 403
