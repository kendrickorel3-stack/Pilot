"""Rules folder: defaults when missing, safe fallback when broken, loosening/tightening, notes, editor API."""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from invoice_agent.agent import run_portal
from invoice_agent.browser import ActionError, Browser
from invoice_agent.config import Portal
from invoice_agent.demo_portal import PASSWORD, TOTP_SECRET, USER, MockPortal
from invoice_agent.policy import DEFAULT_ASK, HARD_FLOOR, load_policy
from invoice_agent.server import create_app
from invoice_agent.vault import Vault, totp

CHROMIUM = "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome"
CHROMIUM = CHROMIUM if Path(CHROMIUM).exists() else None


def write(folder: Path, settings: str = "", instructions: str = "", sites: dict | None = None) -> Path:
    (folder / "sites").mkdir(parents=True, exist_ok=True)
    if settings:
        (folder / "settings.toml").write_text(settings)
    if instructions:
        (folder / "instructions.md").write_text(instructions)
    for name, text in (sites or {}).items():
        (folder / "sites" / f"{name}.md").write_text(text)
    return folder


def billing(br, url):
    br.navigate(url + "/login")
    br.page.fill("#u", USER)
    br.page.fill("#p", PASSWORD)
    br.page.click("button")
    br.page.fill("#c", totp(TOTP_SECRET))
    br.page.click("button")
    br.navigate("/billing")
    return br.page.locator("button", has_text="Pay now")


def test_missing_folder_means_safe_defaults(tmp_path):
    p = load_policy(tmp_path / "nope")
    assert p.ask_before == DEFAULT_ASK and p.max_cost_usd == 2.0 and not p.problems
    assert HARD_FLOOR in p.system_text()


def test_broken_settings_fall_back_to_defaults(tmp_path):
    p = load_policy(write(tmp_path, settings="[approval\nask_before = ["))
    assert p.problems and p.ask_before == DEFAULT_ASK
    p = load_policy(write(tmp_path, settings="[limits]\nmax_cost_usd = 0\nmax_steps = 'lots'"))
    assert p.max_cost_usd == 2.0 and p.max_steps == 80 and len(p.problems) == 2   # a cost cap always exists


def test_ensure_writes_editable_defaults_once(tmp_path):
    p = load_policy(tmp_path / "rules", create=True)
    assert (tmp_path / "rules" / "settings.toml").exists() and (tmp_path / "rules" / "sites" / "example.com.md").exists()
    assert p.ask_before == DEFAULT_ASK and not p.problems                      # written defaults == built-in defaults
    (tmp_path / "rules" / "instructions.md").write_text("mine")
    load_policy(tmp_path / "rules", create=True)
    assert (tmp_path / "rules" / "instructions.md").read_text() == "mine"     # never overwritten


@pytest.mark.parametrize("settings,approve,expect_paid,expect_asked", [
    ("", False, False, 1),                                                         # default: asks, declined
    ('[approval]\nask_before = []', False, True, 0),                               # you loosened it
    ('[approval]\nauto_approve_sites = ["127.0.0.1"]', False, True, 0),            # auto-approved on this site
    ('[approval]\nnever_allow = ["pay now"]', True, False, 0),                    # blocked even if you'd approve
])
def test_approval_rules(tmp_path, settings, approve, expect_paid, expect_asked):
    pol = load_policy(write(tmp_path / "r", settings=settings))
    asked = []
    with MockPortal() as portal, Browser(["127.0.0.1"], tmp_path, executable_path=CHROMIUM, policy=pol,
                                          approve=lambda m: asked.append(m) or approve) as br:
        btn = billing(br, portal.url)
        ref = br.page.evaluate("() => (document.querySelector('form[action=\"/pay\"] button'), null)")
        br.snapshot()
        r = br.page.locator("form[action='/pay'] button").get_attribute("data-agent-ref")
        try:
            br.click(r)
        except ActionError:
            pass
        assert portal.state.paid is expect_paid and len(asked) == expect_asked
        if "auto_approve" in settings:
            assert br.gate_log and "Auto-approved" in br.gate_log[0]


def test_blocked_and_extra_sites(tmp_path):
    pol = load_policy(write(tmp_path / "r", settings='[sites]\nblocked = ["127.0.0.1"]'))
    with Browser(["127.0.0.1"], tmp_path, executable_path=CHROMIUM, policy=pol) as br:
        with pytest.raises(ActionError):
            br.navigate("http://127.0.0.1:9/")
    assert "localhost" in Browser(["a.com"], tmp_path, policy=load_policy(write(tmp_path / "r2", settings='[sites]\nalso_allow = ["localhost"]'))).allowed


def test_site_notes_reach_the_model(tmp_path):
    folder = write(tmp_path / "r", instructions="Always say hello.", sites={"127.0.0.1": "The Pay now button is a trap."})
    pol = load_policy(folder)
    assert pol.site_notes("127.0.0.1")[0] == "127.0.0.1"
    write(folder, sites={"example.com": "general", "billing.example.com": "specific"})
    assert pol.site_notes("x.billing.example.com")[1] == "specific" and pol.site_notes("www.example.com")[1] == "general"

    class Finisher:
        def __init__(self):
            self.kw = None
            self.beta = self.messages = self

        def create(self, **kw):
            from anthropic.types.beta import BetaMessage
            self.kw = kw
            return BetaMessage.model_validate({"id": "m", "type": "message", "role": "assistant", "model": kw["model"],
                "stop_reason": "tool_use", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
                "content": [{"type": "tool_use", "id": "t1", "name": "finish", "input": {"status": "success", "summary": "ok"}}]})

    v = Vault(tmp_path / "v.enc", "x")
    with MockPortal() as portal:
        fake = Finisher()
        run_portal(Portal(id="p", client="c", name="n", login_url=portal.url + "/login", allowed_domains=["127.0.0.1"],
                          instructions="t"), v, out_dir=tmp_path / "o", runs_dir=tmp_path / "runs", client=fake,
                   executable_path=CHROMIUM, policy=pol)
    sent = json.dumps(fake.kw["messages"], default=str)
    assert "The Pay now button is a trap." in sent
    assert "Always say hello." in fake.kw["system"] and "never see passwords" in fake.kw["system"]


def test_rules_api(tmp_path, monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PILOT_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    app = create_app(executable_path=CHROMIUM, data_dir=tmp_path, env_file=tmp_path / ".env", verify=False)
    with TestClient(app) as c:
        r = c.get("/api/rules").json()
        assert "ask_before" in r["settings"] and r["problems"] == []
        assert c.put("/api/rules", json={"file": "settings.toml", "text": "[broken"}).status_code == 400
        assert c.put("/api/rules", json={"file": "../evil.md", "text": "x"}).status_code == 400
        assert c.put("/api/rules", json={"file": "sites/acme.com.md", "text": "notes"}).status_code == 200
        assert c.get("/api/rules").json()["sites"] == {"sites/acme.com.md": "notes", "sites/example.com.md": c.get("/api/rules").json()["sites"]["sites/example.com.md"]}
        assert c.delete("/api/rules", params={"file": "sites/acme.com.md"}).status_code == 200
        assert c.delete("/api/rules", params={"file": "settings.toml"}).status_code == 400
        assert c.get("/api/config").json()["can_edit_rules"] is True
