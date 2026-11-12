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
    for k in ("OPENAI_API_KEY", "PILOT_PROVIDER", "OPENAI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    app = create_app(executable_path=CHROMIUM if os.path.exists(CHROMIUM) else None, data_dir=tmp_path, env_file=env,
                     verify=False)
    with TestClient(app) as c:
        cfg = c.get("/api/config").json()
        assert cfg["has_key"] is False and cfg["can_save_key"] is True
        r = c.post("/api/sessions", json={"url": "https://example.com"})
        assert r.status_code == 503                                   # real run needs a key
        assert c.post("/api/settings", json={"provider": "openai", "api_key": "sk-ant-wrong-provider"}).status_code == 400
        assert c.post("/api/settings", json={"provider": "anthropic", "api_key": "not-a-real-key"}).status_code == 400
        r = c.post("/api/settings", json={"provider": "openai", "api_key": "sk-test-0000000000", "model": "gpt-6.1-terra"})
        assert r.status_code == 200 and r.json()["model"] == "gpt-6.1-terra"
        text = env.read_text()
        assert "OPENAI_API_KEY=sk-test-0000000000" in text and "PILOT_PROVIDER=openai" in text
        assert oct(env.stat().st_mode)[-3:] == "600"
        cfg = c.get("/api/config").json()
        assert cfg["has_key"] is True and cfg["provider"] == "openai" and cfg["model"] == "gpt-6.1-terra"
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
    for k in ("OPENAI_API_KEY", "PILOT_PROVIDER", "OPENAI_MODEL"):
        os.environ.pop(k, None)
