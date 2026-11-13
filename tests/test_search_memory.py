"""Native web search (added to the provider request, dropped gracefully if unsupported) and durable memory."""
from pathlib import Path

from invoice_agent.llm import AnthropicBrain, OpenAIBrain
from invoice_agent.policy import load_policy


# ---- memory ----
def test_memory_round_trip(tmp_path):
    folder = tmp_path / "rules"
    pol = load_policy(folder, create=True)
    assert pol.memory == "" and "your memory" not in pol.system_text().lower()
    assert pol.append_memory("The invoices live under Billing > Documents on acme.") is True
    assert pol.append_memory("   ") is False
    assert "Billing > Documents" in pol.system_text()
    assert (folder / "memory.md").exists()
    pol2 = load_policy(folder)                       # a fresh session sees the saved note
    assert "Billing > Documents" in pol2.memory
    assert "Billing > Documents" in pol2.system_text()


def test_memory_needs_a_folder():
    assert load_policy(None).append_memory("x") is False


def test_web_search_setting(tmp_path):
    (tmp_path).mkdir(exist_ok=True)
    (tmp_path / "settings.toml").write_text("[web]\nsearch = false\n")
    assert load_policy(tmp_path).web_search is False
    (tmp_path / "settings.toml").write_text("[web]\nsearch = true\n")
    assert load_policy(tmp_path).web_search is True


# ---- native web search is added to the request, and dropped if the model rejects it ----
TOOLS = [{"name": "click", "description": "c", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False, "required": []}}]


class FakeOpenAIError(Exception):
    def __init__(self, msg):
        super().__init__(msg)
        self.status_code = 400
        self.message = msg


def _resp(model):
    from openai.types.responses import Response
    return Response.model_validate({"id": "r", "object": "response", "created_at": 0, "model": model,
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [], "status": "completed",
        "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "id": "m", "content": [{"type": "output_text", "text": "hi", "annotations": []}]}],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                  "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}}})


def test_openai_includes_and_drops_web_search():
    import openai
    calls = []

    class Client:
        def __init__(self): self.responses = self
        def create(self, **kw):
            calls.append(kw)
            if len(calls) == 1:
                raise openai.BadRequestError.__new__(openai.BadRequestError) if False else _raise_ws()
            return _resp("gpt-6.1-terra")

    def _raise_ws():
        e = Exception("Web search is not supported for this model in /v1/responses")
        e.status_code = 400; e.message = str(e)
        # make it an openai.BadRequestError instance
        err = openai.BadRequestError.__new__(openai.BadRequestError)
        Exception.__init__(err, str(e)); err.status_code = 400; err.message = str(e)
        raise err

    b = OpenAIBrain(Client(), "gpt-6.1-terra", "medium", web_search=True)
    b.start("sys", TOOLS, ["go"])
    assert any(t.get("type") == "web_search" for t in b._tools())     # included at first
    turn = b.next()
    assert turn.stop == "end"
    assert any(t.get("type") == "web_search" for t in calls[0]["tools"])   # first request carried it
    assert all(t.get("type") != "web_search" for t in calls[1]["tools"])   # retried without it
    assert b._search_on is False


def test_anthropic_includes_web_search():
    b = AnthropicBrain(client=object(), model="claude-opus-5-5", effort="medium", web_search=True)
    b.start("sys", TOOLS, ["go"])
    tools = b._tools()
    assert tools[-1] == {"type": "web_search_20260209", "name": "web_search"}
    b2 = AnthropicBrain(client=object(), model="claude-opus-5-5", effort="medium", web_search=False)
    b2.start("sys", TOOLS, ["go"])
    assert all(t.get("type") != "web_search_20260209" for t in b2._tools())
