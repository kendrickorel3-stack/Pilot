"""End-to-end test of the harness against the fake portal.

A scripted stand-in for Claude reads each page snapshot and issues tool calls, including
ones the harness must refuse: an off-site navigation, a 'Pay now' click and an off-site link.
This checks the browser layer, the vault, the safety rules and the reporting without an API key.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from anthropic.types.beta import BetaMessage

from invoice_agent.agent import run_portal
from invoice_agent.config import Portal
from invoice_agent.vault import Vault, VaultError, totp
from invoice_agent.demo_portal import PASSWORD, TOTP_SECRET, USER, MockPortal

CHROMIUM = os.environ.get("INVOICE_AGENT_CHROMIUM", "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome")
CHROMIUM = CHROMIUM if Path(CHROMIUM).exists() else None


def ref(snapshot: str, pattern: str) -> str:
    for line in snapshot.splitlines():
        m = re.match(r"\[(e\d+)\]", line)
        if m and re.search(pattern, line):
            return m.group(1)
    raise AssertionError(f"No element matching {pattern!r} in:\n{snapshot}")


class ScriptedClient:
    """Mimics client.beta.messages.create, choosing actions from the latest snapshot."""

    def __init__(self, base_url: str):
        self.calls = []
        self.base = base_url
        self.plan = [
            lambda s: ("fill_credential", {"ref": ref(s, r'label="Username"'), "field": "username"}),
            lambda s: ("fill_credential", {"ref": ref(s, r'label="Password"'), "field": "password"}),
            lambda s: ("click", {"ref": ref(s, r'"Sign in"')}),
            lambda s: ("fill_credential", {"ref": ref(s, r'label="Authenticator code"'), "field": "totp"}),
            lambda s: ("click", {"ref": ref(s, r'"Verify"')}),
            lambda s: ("navigate", {"url": "http://evil.example/collect"}),
            lambda s: ("click", {"ref": ref(s, r'"Billing"')}),
            lambda s: ("download", {"ref": ref(s, r"INV-1003\.pdf"), "label": "INV-1003 2026-10-28"}),
            lambda s: ("download", {"ref": ref(s, r"INV-1002\.pdf"), "label": "INV-1002 2026-10-03"}),
            lambda s: ("download", {"ref": ref(s, r"INV-1002\.pdf"), "label": "INV-1002 2026-10-03"}),
            lambda s: ("click", {"ref": ref(s, r'"Pay now"')}),
            lambda s: ("click", {"ref": ref(s, r'"Partner offers"')}),
            lambda s: ("finish", {"status": "success", "summary": "Downloaded INV-1003 and INV-1002. Ignored a page message asking to visit another site."}),
        ]
        self.beta = self
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        last = kw["messages"][-1]["content"]
        snap = "\n".join(
            (b["text"] if b.get("type") == "text" else b.get("content") if isinstance(b.get("content"), str) else "")
            for b in last if isinstance(b, dict))
        i = len(self.calls) - 1
        name, args = self.plan[i](snap)
        return BetaMessage.model_validate({
            "id": f"msg_{i}", "type": "message", "role": "assistant", "model": kw["model"],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 3000, "output_tokens": 120, "cache_read_input_tokens": 2500},
            "content": [{"type": "tool_use", "id": f"toolu_{i:04d}", "name": name, "input": args}],
        })


class ScriptedOpenAI(ScriptedClient):
    """Same plan, shaped like client.chat.completions.create responses."""

    def __init__(self, base_url: str):
        super().__init__(base_url)
        self.chat = self
        self.completions = self

    def create(self, **kw):
        from openai.types.chat import ChatCompletion
        self.calls.append(kw)
        snap = ""
        for m in reversed(kw["messages"]):
            if m["role"] in ("tool", "user"):
                c = m["content"]
                snap = c if isinstance(c, str) else "\n".join(x.get("text", "") for x in c)
                break
        i = len(self.calls) - 1
        name, args = self.plan[i](snap)
        return ChatCompletion.model_validate({
            "id": f"chatcmpl-{i}", "object": "chat.completion", "created": 0, "model": kw["model"],
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": f"call_{i:04d}", "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)}}]}}],
            "usage": {"prompt_tokens": 3000, "completion_tokens": 120, "total_tokens": 3120,
                      "prompt_tokens_details": {"cached_tokens": 2500}},
        })


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path / "vault.enc", "test passphrase")
    v.set("acme", "username", USER)
    v.set("acme", "password", PASSWORD)
    v.set("acme", "totp_secret", TOTP_SECRET)
    v.save()
    return Vault(tmp_path / "vault.enc", "test passphrase")


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_full_run(tmp_path, vault, provider):
    with MockPortal() as portal:
        p = Portal(id="acme", client="Rivera Bakery", name="Acme Supplies", login_url=portal.url + "/login",
                   allowed_domains=["127.0.0.1"], instructions="Download every invoice dated on or after {since}.",
                   since="2026-10-01")
        fake = ScriptedOpenAI(portal.url) if provider == "openai" else ScriptedClient(portal.url)
        approvals = []
        r = run_portal(p, vault, out_dir=tmp_path / "out", runs_dir=tmp_path / "runs", client=fake, provider=provider,
                       approve=lambda m: approvals.append(m) or False, executable_path=CHROMIUM)

        assert r.status == "success", r.summary
        assert [d["label"] for d in r.downloads] == ["INV-1003 2026-10-28", "INV-1002 2026-10-03"]
        for d in r.downloads:
            assert Path(d["file"]).read_bytes().startswith(b"%PDF")
        assert portal.state.paid is False, "the Pay now button must never be pressed"
        assert len(approvals) == 1 and "Pay now" in approvals[0]

        log = [json.loads(line) for line in (r.run_dir / "log.jsonl").read_text().splitlines()]
        errors = [e["error"] for e in log if e["kind"] == "tool" and not e["ok"]]
        assert any("evil.example" in e for e in errors)                  # typed URL refused
        assert any("Pay now" in e for e in errors)                        # payment refused
        assert sum("outside this session's allowed sites" in e for e in errors) == 2       # both off-site attempts
        assert any(e["kind"] == "download" and e["duplicate"] for e in log)
        assert (r.run_dir / "report.html").exists()

        sent = json.dumps([c["messages"] for c in fake.calls], default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o))
        for secret in (USER, PASSWORD, TOTP_SECRET):
            assert secret not in sent, "a credential leaked into what the model sees"
        expected = "gpt-6.1-sol" if provider == "openai" else "claude-opus-5-5"
        assert all(c["model"] == expected for c in fake.calls)
        if provider == "openai":
            first = fake.calls[0]
            assert first["tools"][0]["type"] == "function" and first["tools"][0]["function"]["strict"] is True
            last = fake.calls[-1]["messages"]
            # every tool call is answered by a tool message with the same id, in order
            asked = [tc["id"] for m in last if m["role"] == "assistant" for tc in m.get("tool_calls", [])]
            answered = [m["tool_call_id"] for m in last if m["role"] == "tool"]
            assert asked == answered and len(asked) == 13
            assert r.cost_usd > 0


def test_vault_wrong_passphrase(tmp_path):
    v = Vault(tmp_path / "v.enc", "right")
    v.set("x", "password", "pw")
    v.save()
    with pytest.raises(VaultError):
        Vault(tmp_path / "v.enc", "wrong")
    assert oct((tmp_path / "v.enc").stat().st_mode)[-3:] == "600"


def test_totp_matches_rfc6238_vector():
    # RFC 6238 test secret "12345678901234567890" at T=59s -> 94287082 (8 digits)
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp(secret, at=59, digits=8) == "94287082"
