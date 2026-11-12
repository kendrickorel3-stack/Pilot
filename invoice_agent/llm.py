"""AI providers behind one small interface, so the agent loop doesn't care who runs the model.

A Brain keeps the conversation in its provider's own format and returns plain Turns:
the model's short notes plus the tool calls it wants. Tool results come back in a
provider-neutral form: a string, or a list holding one image block for screenshots.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

PROVIDERS = ("openai", "anthropic")
DEFAULT_MODEL = {"openai": "gpt-6.1-sol", "anthropic": "claude-opus-5-5"}
OPENAI_MODELS = ["gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-astra"]

# USD per 1M tokens: (input, cached input, output). OpenAI rates from developers.openai.com/api/docs/pricing
# (standard tier, short context, Nov 2026). Unknown models are costed at the most expensive rate so the
# per-session cost cap still protects you.
PRICES = {
    "gpt-6-astra": (10.00, 1.00, 50.00),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6.1-terra": (1.00, 0.05, 5.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "claude-opus-5-5": (4.00, 0.20, 20.00),
}
FALLBACK_PRICE = (10.00, 1.00, 50.00)


@dataclass
class Call:
    id: str
    name: str
    input: dict


@dataclass
class Turn:
    notes: list[str]
    calls: list[Call]
    stop: str  # "tool_use" | "end" | "refusal" | "truncated"


class BrainError(Exception):
    """The provider failed in a way the run can't recover from."""


class BrainBusy(BrainError):
    """Rate limited: wait and try again."""


@dataclass
class Brain:
    model: str
    tokens: dict = field(default_factory=lambda: {"input": 0, "cached": 0, "output": 0})

    @property
    def usd(self) -> float:
        p_in, p_cached, p_out = PRICES.get(self.model, FALLBACK_PRICE)
        return ((self.tokens["input"] * p_in + self.tokens["cached"] * p_cached + self.tokens["output"] * p_out)
                / 1_000_000)

    def start(self, system: str, tools: list[dict], user_texts: list[str]) -> None: ...
    def add_results(self, results: list[tuple[str, object, bool]], extra_texts: list[str] | None = None) -> None: ...
    def add_user_text(self, text: str) -> None: ...
    def next(self) -> Turn: ...


# ---------------------------------------------------------------- Anthropic (Claude)
class AnthropicBrain(Brain):
    def __init__(self, client=None, model: str = DEFAULT_MODEL["anthropic"], effort: str = "medium"):
        super().__init__(model=model)
        import anthropic
        self._sdk = anthropic
        self.client = client or anthropic.Anthropic()
        self.effort = effort
        self.messages: list[dict] = []

    def start(self, system, tools, user_texts):
        self.system, self.tools = system, tools
        self.messages = [{"role": "user", "content": [{"type": "text", "text": t} for t in user_texts]}]

    def add_results(self, results, extra_texts=None):
        content = [{"type": "tool_result", "tool_use_id": cid, "content": c, "is_error": err} for cid, c, err in results]
        content += [{"type": "text", "text": t} for t in (extra_texts or [])]
        self.messages.append({"role": "user", "content": content})

    def add_user_text(self, text):
        last = self.messages[-1]
        if last["role"] == "user" and isinstance(last["content"], list):
            last["content"].append({"type": "text", "text": text})
        else:
            self.messages.append({"role": "user", "content": [{"type": "text", "text": text}]})

    def next(self) -> Turn:
        a = self._sdk
        try:
            resp = self.client.beta.messages.create(
                model=self.model, max_tokens=16000, system=self.system, tools=self.tools, messages=self.messages,
                output_config={"effort": self.effort}, cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        except a.RateLimitError as e:
            raise BrainBusy("The AI service is busy.") from e
        except a.APIStatusError as e:
            raise BrainError(f"AI service error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise BrainError("Could not reach the AI service.") from e
        u = resp.usage
        if u:
            self.tokens["input"] += (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
            self.tokens["cached"] += getattr(u, "cache_read_input_tokens", 0) or 0
            self.tokens["output"] += u.output_tokens or 0
        self.messages.append({"role": "assistant", "content": resp.content})
        notes = [b.text for b in resp.content if b.type == "text" and b.text.strip()]
        calls = [Call(b.id, b.name, b.input if isinstance(b.input, dict) else {}) for b in resp.content if b.type == "tool_use"]
        stop = {"refusal": "refusal", "max_tokens": "truncated"}.get(resp.stop_reason, "tool_use" if calls else "end")
        return Turn(notes, calls, stop)


# ---------------------------------------------------------------- OpenAI
class OpenAIBrain(Brain):
    """OpenAI Responses API. (Chat completions refuses function tools together with reasoning on
    newer models, so Responses is required for an agent that reasons and uses tools.)

    Nothing is stored at OpenAI (store=False): each request carries the conversation, including the
    model's encrypted reasoning items, which keeps reasoning intact across tool calls."""

    def __init__(self, client=None, model: str = DEFAULT_MODEL["openai"], effort: str = "medium"):
        super().__init__(model=model)
        import openai
        self._sdk = openai
        self.client = client or openai.OpenAI()
        self.effort = effort
        self._send_effort = True
        self.items: list[dict] = []

    def start(self, system, tools, user_texts):
        self.instructions = system
        self.tools = [{"type": "function", "name": t["name"], "description": t["description"],
                       "parameters": t["input_schema"], "strict": True} for t in tools]
        self.items = [_user([{"type": "input_text", "text": t} for t in user_texts])]

    def add_results(self, results, extra_texts=None):
        images = []
        for cid, content, is_error in results:
            if isinstance(content, list):  # function outputs are text; screenshots follow as a user message
                imgs = [b for b in content if b.get("type") == "image"]
                images += imgs
                text = "\n\n".join(b["text"] for b in content if b.get("type") == "text")
                if imgs:
                    text += ("\n\n" if text else "") + f"[Screenshot {len(images)} is attached in the next message.]"
            else:
                text = str(content)
            if is_error:
                text = "ERROR: " + text
            self.items.append({"type": "function_call_output", "call_id": cid, "output": text})
        parts = [{"type": "input_image", "detail": "auto",
                  "image_url": f"data:{b['source']['media_type']};base64,{b['source']['data']}"} for b in images]
        parts += [{"type": "input_text", "text": t} for t in (extra_texts or [])]
        if parts:
            self.items.append(_user(parts))

    def add_user_text(self, text):
        self.items.append(_user([{"type": "input_text", "text": text}]))

    def next(self) -> Turn:
        o = self._sdk
        kw = dict(model=self.model, instructions=self.instructions, input=self.items, tools=self.tools,
                  tool_choice="auto", parallel_tool_calls=True, store=False,
                  include=["reasoning.encrypted_content"], prompt_cache_key="pilot-agent")
        if self._send_effort:
            kw["reasoning"] = {"effort": self.effort}
        try:
            resp = self.client.responses.create(**kw)
        except o.BadRequestError as e:
            if self._send_effort and "reasoning" in str(e).lower():
                self._send_effort = False  # model without reasoning settings: retry plainly
                return self.next()
            raise BrainError(f"AI service error {e.status_code}: {e.message}") from e
        except o.RateLimitError as e:
            raise BrainBusy("The AI service is busy.") from e
        except o.APIStatusError as e:
            raise BrainError(f"AI service error {e.status_code}: {e.message}") from e
        except o.APIConnectionError as e:
            raise BrainError("Could not reach the AI service.") from e
        if getattr(resp, "error", None):
            raise BrainError(f"AI service error: {resp.error.message}")
        u = resp.usage
        if u:
            cached = (u.input_tokens_details.cached_tokens if u.input_tokens_details else 0) or 0
            self.tokens["input"] += (u.input_tokens or 0) - cached
            self.tokens["cached"] += cached
            self.tokens["output"] += u.output_tokens or 0
        notes, calls, refused = [], [], False
        for item in resp.output:
            # feed every output item back next turn (reasoning, messages, calls), as the API expects
            self.items.append(item.model_dump(exclude_none=True, by_alias=True))
            if item.type == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append(Call(item.call_id, item.name, args if isinstance(args, dict) else {}))
            elif item.type == "message":
                for c in item.content or []:
                    if c.type == "output_text" and c.text.strip():
                        notes.append(c.text.strip())
                    elif c.type == "refusal":
                        refused = True
                        notes.append(c.refusal)
        reason = resp.incomplete_details.reason if resp.status == "incomplete" and resp.incomplete_details else None
        if refused or reason == "content_filter":
            return Turn(notes, [], "refusal")
        if reason == "max_output_tokens" and not calls:
            return Turn(notes, [], "truncated")
        return Turn(notes, calls, "tool_use" if calls else "end")


def _user(content: list[dict]) -> dict:
    return {"type": "message", "role": "user", "content": content}


# ---------------------------------------------------------------- selection
def active_provider() -> str | None:
    chosen = os.environ.get("PILOT_PROVIDER", "").lower()
    if chosen in PROVIDERS and _key_for(chosen):
        return chosen
    for p in PROVIDERS:  # OpenAI first when both keys are present and nothing is chosen
        if _key_for(p):
            return p
    return None


def active_model(provider: str | None = None) -> str:
    provider = provider or active_provider() or "openai"
    env = os.environ.get("OPENAI_MODEL" if provider == "openai" else "ANTHROPIC_MODEL", "")
    return env or DEFAULT_MODEL[provider]


def _key_for(provider: str) -> bool:
    if provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def make_brain(provider: str | None = None, model: str | None = None, client=None, effort: str = "medium") -> Brain:
    provider = provider or active_provider()
    if provider is None:
        raise BrainError("No AI key is set. Add an OpenAI or Anthropic API key.")
    model = model or active_model(provider)
    if provider == "openai":
        return OpenAIBrain(client, model, effort)
    return AnthropicBrain(client, model, effort)
