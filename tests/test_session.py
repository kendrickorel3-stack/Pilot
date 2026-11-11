"""Interactive session: approval prompt, live frames, chat after finishing, credential hygiene."""
import json
import time
from pathlib import Path

from invoice_agent.demo import DemoClient
from invoice_agent.demo_portal import PASSWORD, TOTP_SECRET, USER, MockPortal
from invoice_agent.session import AgentSession, base_domain

CHROMIUM = "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome"
CHROMIUM = CHROMIUM if Path(CHROMIUM).exists() else None


def wait_for(s, kind, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        for e in s.events:
            if e["kind"] == kind:
                return e
        time.sleep(0.1)
    raise AssertionError(f"no {kind} event; got {[e['kind'] for e in s.events]}")


def test_session_flow(tmp_path):
    with MockPortal() as portal:
        client = DemoClient(delay=0)
        sent = []
        orig = client.create
        client.create = lambda **kw: (sent.append(json.dumps(kw["messages"], default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o))), orig(**kw))[1]
        s = AgentSession(url=portal.url + "/login", username=USER, password=PASSWORD, totp_secret=TOTP_SECRET,
                         task="Download October invoices.", client=client, runs_dir=tmp_path / "runs",
                         out_dir=tmp_path / "out", executable_path=CHROMIUM)
        assert s.portal.allowed_domains == ["127.0.0.1"]
        s.start()
        wait_for(s, "approval")
        assert s.frame_id > 0 and s.frame[:2] == b"\xff\xd8"          # live JPEG frames flowing
        s.decide(False)
        done = wait_for(s, "done")
        assert done["status"] == "success"
        assert portal.state.paid is False
        s.say("Now open Account settings.")
        wait_for(s, "agent")
        s.stop()
        s.join(20)
        assert not s.is_alive()
        assert s.status == "ended"
        assert len(s.downloads) == 2
        for secret in (USER, PASSWORD, TOTP_SECRET):
            assert all(secret not in m for m in sent)
        assert s.creds.all_secret_values() == []                      # wiped at the end


def test_base_domain():
    assert base_domain("portal.acme.com") == "acme.com"
    assert base_domain("billing.shop.co.uk") == "shop.co.uk"
    assert base_domain("10.0.0.1") == "10.0.0.1"
    assert base_domain("localhost") == "localhost"
