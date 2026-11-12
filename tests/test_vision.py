"""Screen vision on a canvas-only page: see, read, click by position, purchase guard, and taking control."""
import time
from pathlib import Path

from invoice_agent.browser import ActionError, Browser
from invoice_agent.demo import DemoClient
from invoice_agent.demo_portal import PASSWORD, TOTP_SECRET, USER, MockPortal, TILE_BOX
from invoice_agent.session import AgentSession
from invoice_agent.vault import totp

CHROMIUM = "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome"
CHROMIUM = CHROMIUM if Path(CHROMIUM).exists() else None


def login(br, url):
    br.navigate(url + "/login")
    br.page.fill("#u", USER)
    br.page.fill("#p", PASSWORD)
    br.page.click("button")
    br.page.fill("#c", totp(TOTP_SECRET))
    br.page.click("button")


def centre(box):
    x, y, w, h = box
    return x + w // 2, y + h // 2


def test_canvas_vision(tmp_path):
    asked = []
    with MockPortal() as portal, Browser(["127.0.0.1"], tmp_path, executable_path=CHROMIUM,
                                          approve=lambda m: asked.append(m) or False) as br:
        login(br, portal.url)
        br.navigate("/ops")
        snap = br.snapshot()
        assert "Drawn areas" in snap and "x=0 y=0 w=900 h=560" in snap
        full, gridded, crop = br.look(), br.look(grid=True), br.look(region=(0, 0, 900, 80))
        assert full[:2] == b"\xff\xd8" and gridded != full and len(crop) < len(full)
        assert br.page.locator("[data-agent-grid]").count() == 0       # grid overlay removed after the shot

        br.click_at(*centre(TILE_BOX(2)), purpose="Export the Q3 report")
        time.sleep(0.4)
        assert portal.state.exports == ["Q3 report"]

        br.click_at(*centre(TILE_BOX(5)), purpose="Open the credits tile")
        try:
            br.click_at(360, 355, purpose="Confirm the purchase of 100 credits")
            raise AssertionError("purchase should have been blocked")
        except ActionError as e:
            assert "Blocked" in str(e)
        assert portal.state.bought is False and len(asked) == 1

        ticks = []
        br.on_tick = lambda: ticks.append(1)
        br.wait(1.2)
        assert ticks
        for bad in [(-1, 5), (5000, 5)]:
            try:
                br.click_at(*bad, purpose="x")
                raise AssertionError("off-screen click should fail")
            except ActionError:
                pass
        try:
            br.press_key("Control+W")
            raise AssertionError("unsupported key should fail")
        except ActionError:
            pass


def test_demo_session_with_vision_and_control(tmp_path):
    with MockPortal() as portal:
        s = AgentSession(url=portal.url + "/login", username=USER, password=PASSWORD, totp_secret=TOTP_SECRET,
                         task="demo", client=DemoClient(delay=0), runs_dir=tmp_path / "r", out_dir=tmp_path / "o",
                         executable_path=CHROMIUM)
        s.start()
        approvals = 0
        end = time.time() + 120
        while time.time() < end and not any(e["kind"] == "done" for e in s.events):
            if s.status == "needs approval":
                s.decide(False)
                approvals += 1
                time.sleep(0.3)
            time.sleep(0.1)
        assert any(e["kind"] == "done" for e in s.events), [e["kind"] for e in s.events][-10:]
        assert approvals == 2                                         # Pay now + the credits YES
        assert portal.state.exports == ["Q3 report"] and not portal.state.bought and not portal.state.paid
        # take control, go back a page, hand back
        s.set_control(True)
        s.user_input({"type": "back"})
        time.sleep(1.5)
        assert any(e["kind"] == "control" and e["on"] for e in s.events)
        s.set_control(False)
        s.stop()
        s.join(20)
        assert not s.is_alive()
