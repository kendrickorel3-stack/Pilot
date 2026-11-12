"""Web API: key setup is local-only and validated; the demo runs without a key."""
import os
import time

from fastapi.testclient import TestClient

from invoice_agent.server import create_app

CHROMIUM = "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome"


def test_setup_and_demo(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    env = tmp_path / ".env"
    app = create_app(executable_path=CHROMIUM if os.path.exists(CHROMIUM) else None, data_dir=tmp_path, env_file=env)
    with TestClient(app) as c:
        cfg = c.get("/api/config").json()
        assert cfg["has_key"] is False and cfg["can_save_key"] is True
        r = c.post("/api/sessions", json={"url": "https://example.com"})
        assert r.status_code == 503                                   # real run needs a key
        assert c.post("/api/settings", json={"anthropic_api_key": "not-a-real-key"}).status_code == 400
        assert c.post("/api/settings", json={"anthropic_api_key": "sk-ant-test-0000000000"}).status_code == 200
        assert "ANTHROPIC_API_KEY=sk-ant-test-0000000000" in env.read_text()
        assert oct(env.stat().st_mode)[-3:] == "600"
        assert c.get("/api/config").json()["has_key"] is True
        # demo ignores whatever URL is sent and only drives the built-in portal
        sid = c.post("/api/sessions", json={"url": "https://bank.example", "demo": True}).json()["id"]
        end = time.time() + 60
        while time.time() < end:
            r = c.get(f"/api/sessions/{sid}/frame")
            if r.status_code == 200:
                break
            time.sleep(0.3)
        assert r.headers["content-type"] == "image/jpeg"
        c.post(f"/api/sessions/{sid}/stop")
    os.environ.pop("ANTHROPIC_API_KEY", None)
