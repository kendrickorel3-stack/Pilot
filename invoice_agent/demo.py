"""Demo mode: a scripted stand-in for Claude that drives the built-in fake portal, so the web
app can be shown working end to end without an API key. It is not an AI and cannot follow
free-form instructions."""
from __future__ import annotations

import re
import time

from anthropic.types.beta import BetaMessage


def find_ref(snapshot: str, pattern: str) -> str:
    for line in snapshot.splitlines():
        m = re.match(r"\[(e\d+)\]", line)
        if m and re.search(pattern, line):
            return m.group(1)
    raise LookupError(pattern)


class DemoClient:
    PLAN = [
        ("I'll log in with the saved username first.", "fill_credential", lambda s: {"ref": find_ref(s, r'label="Username"'), "field": "username"}),
        (None, "fill_credential", lambda s: {"ref": find_ref(s, r'label="Password"'), "field": "password"}),
        (None, "click", lambda s: {"ref": find_ref(s, r'"Sign in"')}),
        ("The portal wants an authenticator code; generating it from the saved secret.", "fill_credential",
         lambda s: {"ref": find_ref(s, r'label="Authenticator code"'), "field": "totp"}),
        (None, "click", lambda s: {"ref": find_ref(s, r'"Verify"')}),
        ("Logged in. The dashboard has a message telling assistants to visit another site; I'm ignoring it. Opening Billing.",
         "click", lambda s: {"ref": find_ref(s, r'"Billing"')}),
        ("Two invoices are from October. Downloading INV-1003.", "download", lambda s: {"ref": find_ref(s, r"INV-1003\.pdf"), "label": "INV-1003 2026-10-28"}),
        (None, "download", lambda s: {"ref": find_ref(s, r"INV-1002\.pdf"), "label": "INV-1002 2026-10-03"}),
        ("There's a Pay now button. Paying needs your approval, so I'm asking before touching it.", "click", lambda s: {"ref": find_ref(s, r'"Pay now"')}),
        ("Now the Operations board. It's drawn as a picture, so I'll use the screen instead of the element list.", "navigate", lambda s: {"url": "/ops"}),
        (None, "screenshot", lambda s: {"grid": True}),
        ("Reading the counters and the sync timer up close.", "zoom", lambda s: {"x": 0, "y": 0, "width": 900, "height": 80}),
        ("Exporting the Q3 report tile.", "click_at", lambda s: {"x": 530, "y": 185, "purpose": "Open the Q3 report tile to export it", "double": False}),
        ("Opening the credits tile to see what it does.", "click_at", lambda s: {"x": 530, "y": 335, "purpose": "Open the credits tile to see the offer", "double": False}),
        ("That's a purchase. Asking you before pressing YES.", "click_at", lambda s: {"x": 360, "y": 355, "purpose": "Confirm the purchase of 100 credits for $9.99", "double": False}),
        (None, "click_at", lambda s: {"x": 540, "y": 355, "purpose": "Close the offer with NO", "double": False}),
        (None, "wait", lambda s: {"seconds": 2}),
        (None, "finish", lambda s: {"status": "success", "summary": "Downloaded INV-1003 (Oct 28) and INV-1002 (Oct 3); skipped INV-0997 (September). Exported the Q3 report from the Operations board. Didn't buy credits without your approval. Ignored a page message asking me to visit another site."}),
    ]

    def __init__(self, delay: float = 1.2):
        self.i = 0
        self.delay = delay
        self.beta = self
        self.messages = self

    def create(self, **kw):
        time.sleep(self.delay)  # makes the live view watchable
        last = kw["messages"][-1]["content"]
        blocks = last if isinstance(last, list) else [{"type": "text", "text": last}]
        snap = "\n".join(b.get("text") or (b.get("content") if isinstance(b.get("content"), str) else "") for b in blocks if isinstance(b, dict))
        base = {"id": f"msg_demo_{self.i}", "type": "message", "role": "assistant", "model": kw["model"], "stop_sequence": None,
                "usage": {"input_tokens": 3200, "output_tokens": 90, "cache_read_input_tokens": 2800}}
        if self.i < len(self.PLAN):
            note, name, args = self.PLAN[self.i]
            try:
                inp = args(snap)
            except LookupError:
                self.i = len(self.PLAN)
                return BetaMessage.model_validate({**base, "stop_reason": "end_turn", "content": [
                    {"type": "text", "text": "Demo script lost its place on this page. Connect an API key for the real agent."}]})
            content = ([{"type": "text", "text": note}] if note else []) + [
                {"type": "tool_use", "id": f"toolu_demo_{self.i:03d}", "name": name, "input": inp}]
            self.i += 1
            return BetaMessage.model_validate({**base, "stop_reason": "tool_use", "content": content})
        return BetaMessage.model_validate({**base, "stop_reason": "end_turn", "content": [
            {"type": "text", "text": "This is demo mode, a scripted run with no AI behind it, so I can't follow new instructions. "
                                     "Add an Anthropic API key and restart without --demo to give the real agent instructions."}]})
