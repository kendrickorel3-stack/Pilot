"""Web app: paste a link and login details, watch the agent work live, and chat with it.

    python -m invoice_agent.server --open          # local use; paste your API key on the setup screen
    python -m invoice_agent.server --demo          # only the scripted demo (no AI)

When reachable from other machines, set INVOICE_AGENT_ACCESS_CODE; every request must carry it,
because each session spends your AI credit and runs a browser on your server.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .llm import DEFAULT_MODEL, OPENAI_MODELS, active_model, active_provider
from .session import AgentSession

STATIC = Path(__file__).parent / "static"
MAX_SESSIONS = int(os.environ.get("INVOICE_AGENT_MAX_SESSIONS", "3"))
IDLE_LIMIT = 30 * 60


class StartBody(BaseModel):
    url: str = Field(min_length=3, max_length=2000)
    username: str = Field(default="", max_length=500)
    password: str = Field(default="", max_length=500)
    totp_secret: str = Field(default="", max_length=200)
    task: str = Field(default="", max_length=4000)
    extra_domains: str = Field(default="", max_length=1000)
    demo: bool = False


class KeyBody(BaseModel):
    provider: str = Field(default="openai", pattern="^(openai|anthropic)$")
    api_key: str = Field(min_length=10, max_length=400)
    model: str = Field(default="", max_length=100)


class TextBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DecideBody(BaseModel):
    allow: bool


class InputBody(BaseModel):
    type: str = Field(pattern="^(click|scroll|type|key|back)$")
    x: int = Field(default=0, ge=0, le=4000)
    y: int = Field(default=0, ge=0, le=4000)
    dy: int = Field(default=0, ge=-3000, le=3000)
    text: str = Field(default="", max_length=500)
    key: str = Field(default="", max_length=20)


class ControlBody(BaseModel):
    on: bool


def load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE loader for the local settings file (values never logged)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def save_settings(path: Path, values: dict[str, str]) -> None:
    lines = [l for l in (path.read_text().splitlines() if path.exists() else [])
             if l.split("=", 1)[0].strip() not in values]
    lines += [f"{k}={v}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def has_key() -> bool:
    return active_provider() is not None


def verify_key(provider: str, key: str, model: str) -> str | None:
    """Return None if the key works and the model is available, else a message for the user."""
    try:
        if provider == "openai":
            import openai
            try:
                openai.OpenAI(api_key=key, timeout=20, max_retries=0).models.retrieve(model)
            except openai.AuthenticationError:
                return "OpenAI rejected that key. Check it at platform.openai.com/api-keys."
            except openai.NotFoundError:
                return f"That key works, but your OpenAI account can't use {model}. Pick another model."
            except openai.APIConnectionError:
                return None  # offline right now; save anyway, a run will report problems
        else:
            import anthropic
            try:
                anthropic.Anthropic(api_key=key, timeout=20, max_retries=0).models.retrieve(model)
            except anthropic.AuthenticationError:
                return "Anthropic rejected that key. Check it at console.anthropic.com."
            except anthropic.NotFoundError:
                return f"That key works, but your account can't use {model}."
            except anthropic.APIConnectionError:
                return None
    except Exception as e:  # any other provider error: let the user see it
        return f"Couldn't check the key: {type(e).__name__}: {str(e)[:200]}"
    return None


def create_app(*, demo: bool = False, access_code: str = "", executable_path: str | None = None,
               data_dir: Path = Path("."), env_file: Path | None = None, verify: bool = True) -> FastAPI:
    sessions: dict[str, AgentSession] = {}

    @asynccontextmanager
    async def lifespan(_app):
        yield
        for s in sessions.values():
            s.stop()
        if portal:
            portal.__exit__()

    app = FastAPI(title="Browser agent", docs_url=None, redoc_url=None, lifespan=lifespan)
    # The built-in test portal is always available, so anyone can try the demo without a key.
    from .demo_portal import PASSWORD, TOTP_SECRET, USER, MockPortal
    portal = MockPortal().__enter__()
    demo_defaults = {"url": portal.url + "/login", "username": USER, "password": PASSWORD,
                     "totp_secret": TOTP_SECRET, "task": "Download every invoice from October 2026.", "extra_domains": ""}

    def check(request: Request) -> None:
        if not access_code:
            return
        given = request.headers.get("x-access-code") or request.query_params.get("code") or ""
        if not hmac.compare_digest(given.encode(), access_code.encode()):
            raise HTTPException(401, "Access code required.")

    def get(sid: str) -> AgentSession:
        s = sessions.get(sid)
        if not s:
            raise HTTPException(404, "That session has ended or never existed.")
        return s

    def reap() -> None:
        now = time.time()
        for sid, s in list(sessions.items()):
            if not s.is_alive() and now - s.created > 600:
                sessions.pop(sid, None)
            elif s.is_alive() and now - s.created > IDLE_LIMIT and s.status == "waiting for you":
                s.stop()

    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    def config(request: Request):
        local = (request.client.host if request.client else "") in ("127.0.0.1", "::1", "localhost", "testclient")
        prov = active_provider()
        return {"demo": demo_defaults, "demo_only": demo, "needs_code": bool(access_code), "has_key": has_key(),
                "can_save_key": bool(env_file) and local and not demo, "provider": prov,
                "model": active_model(prov) if prov else None, "openai_models": OPENAI_MODELS}

    @app.post("/api/settings")
    def settings(body: KeyBody, request: Request):
        check(request)
        host = request.client.host if request.client else ""
        if not env_file or host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(403, "The API key can only be set from the computer running Pilot.")
        key, prov = body.api_key.strip(), body.provider
        if prov == "anthropic" and not key.startswith("sk-ant-"):
            raise HTTPException(400, "That doesn't look like an Anthropic API key. They start with sk-ant-.")
        if prov == "openai" and (not key.startswith("sk-") or key.startswith("sk-ant-")):
            raise HTTPException(400, "That doesn't look like an OpenAI API key. They start with sk-.")
        model = body.model.strip() or DEFAULT_MODEL[prov]
        if verify:
            problem = verify_key(prov, key, model)
            if problem:
                raise HTTPException(400, problem)
        values = {"PILOT_PROVIDER": prov, ("OPENAI_API_KEY" if prov == "openai" else "ANTHROPIC_API_KEY"): key}
        if prov == "openai":
            values["OPENAI_MODEL"] = model
        save_settings(env_file, values)
        os.environ.update(values)
        return {"ok": True, "provider": prov, "model": model}

    @app.post("/api/check")
    def check_code(request: Request):
        check(request)
        return {"ok": True}

    @app.post("/api/sessions")
    def start(body: StartBody, request: Request):
        check(request)
        reap()
        if sum(s.is_alive() for s in sessions.values()) >= MAX_SESSIONS:
            raise HTTPException(429, f"{MAX_SESSIONS} sessions are already running. Stop one first.")
        client = None
        if demo or body.demo:
            from .demo import DemoClient
            client = DemoClient()
            body = body.model_copy(update=demo_defaults)  # the demo only ever runs against the built-in portal
        elif not has_key():
            raise HTTPException(503, "Add your OpenAI or Anthropic API key first, or try the demo.")
        try:
            s = AgentSession(url=body.url, username=body.username, password=body.password, totp_secret=body.totp_secret,
                             task=body.task, extra_domains=[d for d in body.extra_domains.replace(",", " ").split() if d],
                             client=client, runs_dir=data_dir / "runs", out_dir=data_dir / "out",
                             executable_path=executable_path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        sessions[s.id] = s
        s.start()
        return {"id": s.id, "allowed": s.portal.allowed_domains}

    @app.get("/api/sessions/{sid}/events")
    async def events(sid: str, request: Request):
        check(request)
        s = get(sid)

        async def stream():
            n, last_frame = 0, -1
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                new, fid = await asyncio.to_thread(s.events_since, n, 1.0)
                for e in new:
                    yield f"data: {json.dumps(e, default=str)}\n\n"
                n += len(new)
                if fid != last_frame:
                    last_frame = fid
                    yield f"data: {json.dumps({'kind': 'frame', 'id': fid})}\n\n"
                if not s.is_alive() and n >= len(s.events):
                    yield f"data: {json.dumps({'kind': 'closed'})}\n\n"
                    return
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/sessions/{sid}/frame")
    def frame(sid: str, request: Request):
        check(request)
        s = get(sid)
        if not s.frame:
            raise HTTPException(404, "No picture yet.")
        return Response(s.frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/sessions/{sid}/message")
    def message(sid: str, body: TextBody, request: Request):
        check(request)
        get(sid).say(body.text.strip())
        return {"ok": True}

    @app.post("/api/sessions/{sid}/answer")
    def answer(sid: str, body: TextBody, request: Request):
        check(request)
        get(sid).answer(body.text.strip())
        return {"ok": True}

    @app.post("/api/sessions/{sid}/decide")
    def decide(sid: str, body: DecideBody, request: Request):
        check(request)
        get(sid).decide(body.allow)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/input")
    def user_input(sid: str, body: InputBody, request: Request):
        check(request)
        s = get(sid)
        if not s.control.is_set() and s.is_alive():
            s.set_control(True)  # touching the page takes control, so you and the agent don't fight
        s.user_input(body.model_dump())
        return {"ok": True}

    @app.post("/api/sessions/{sid}/control")
    def control(sid: str, body: ControlBody, request: Request):
        check(request)
        get(sid).set_control(body.on)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/stop")
    def stop(sid: str, request: Request):
        check(request)
        get(sid).stop()
        return {"ok": True}

    @app.get("/api/sessions/{sid}/files/{name}")
    def file(sid: str, name: str, request: Request):
        check(request)
        s = get(sid)
        for d in s.downloads:
            p = Path(d["file"])
            if p.name == name and p.exists():
                return FileResponse(p, filename=p.name)
        raise HTTPException(404, "No such file in this session.")

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the browser agent web app.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    ap.add_argument("--demo", action="store_true", help="Scripted demo against a built-in fake portal; no API key needed")
    ap.add_argument("--chromium", default=os.environ.get("INVOICE_AGENT_CHROMIUM"))
    ap.add_argument("--data", default=os.environ.get("INVOICE_AGENT_DATA", "."), help="Where runs/, out/ and settings are written")
    ap.add_argument("--open", action="store_true", help="Open the app in your web browser")
    a = ap.parse_args()
    Path(a.data).mkdir(parents=True, exist_ok=True)
    env_file = Path(a.data) / ".env"
    load_env_file(env_file)
    code = os.environ.get("INVOICE_AGENT_ACCESS_CODE", "")
    if a.host not in ("127.0.0.1", "localhost") and not code:
        raise SystemExit("Refusing to listen on a public address without INVOICE_AGENT_ACCESS_CODE set.")
    import threading
    import uvicorn
    import webbrowser
    url = f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '127.0.0.1') else a.host}:{a.port}"
    print(f"Pilot is running at {url}  (press Ctrl+C to stop)")
    if a.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(demo=a.demo, access_code=code, executable_path=a.chromium, data_dir=Path(a.data),
                           env_file=env_file), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
