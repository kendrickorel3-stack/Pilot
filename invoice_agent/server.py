"""Web app: paste a link and login details, watch the agent work live, and chat with it.

    python -m invoice_agent.server                 # real agent (needs ANTHROPIC_API_KEY)
    python -m invoice_agent.server --demo          # scripted demo against a built-in fake portal

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


class TextBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DecideBody(BaseModel):
    allow: bool


def create_app(*, demo: bool = False, access_code: str = "", executable_path: str | None = None,
               data_dir: Path = Path(".")) -> FastAPI:
    sessions: dict[str, AgentSession] = {}

    @asynccontextmanager
    async def lifespan(_app):
        yield
        for s in sessions.values():
            s.stop()
        if portal:
            portal.__exit__()

    app = FastAPI(title="Browser agent", docs_url=None, redoc_url=None, lifespan=lifespan)
    portal = None
    if demo:
        from .demo_portal import PASSWORD, TOTP_SECRET, USER, MockPortal
        portal = MockPortal().__enter__()
        app.state.demo_defaults = {"url": portal.url + "/login", "username": USER, "password": PASSWORD,
                                   "totp_secret": TOTP_SECRET, "task": "Download every invoice from October 2026.",
                                   "extra_domains": ""}
    else:
        app.state.demo_defaults = None

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
        return {"demo": app.state.demo_defaults, "needs_code": bool(access_code)}

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
        if demo:
            from .demo import DemoClient
            client = DemoClient()
        elif not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise HTTPException(503, "No Anthropic API key is set on the server. Set ANTHROPIC_API_KEY, or start with --demo.")
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
    ap.add_argument("--data", default=os.environ.get("INVOICE_AGENT_DATA", "."), help="Where runs/ and out/ are written")
    a = ap.parse_args()
    code = os.environ.get("INVOICE_AGENT_ACCESS_CODE", "")
    if a.host not in ("127.0.0.1", "localhost") and not code:
        raise SystemExit("Refusing to listen on a public address without INVOICE_AGENT_ACCESS_CODE set.")
    import uvicorn
    uvicorn.run(create_app(demo=a.demo, access_code=code, executable_path=a.chromium, data_dir=Path(a.data)),
                host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
