"""FastAPI application: REST + WebSocket + the built React UI, all on 127.0.0.1:8000 (01 §1).

Run with ``python -m app`` (reads host/port from config.yaml) or
``uvicorn app.main:app --host 127.0.0.1 --port 8000``.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.routes import APIError, error_response, router
from app.api.ws import WSHub
from app.backtest.service import BacktestService
from app.bot import TradingBot
from app.config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, ConfigManager
from app.db.journal import Journal
from app.logging_setup import setup_logging

log = logging.getLogger("main")
WEB_DIST = PROJECT_ROOT / "web" / "dist"

FALLBACK_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>MSC Bot</title>
<style>body{background:#0f141b;color:#d7dee8;font-family:system-ui;margin:3rem}code{color:#8fd}</style></head>
<body><h1>MSC Bot API is running</h1><p>The web UI build (<code>web/dist</code>) was not found.
Build it with <code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code>.</p>
<p>API: <a href="/api/status" style="color:#8cf">/api/status</a> · docs: <a href="/docs" style="color:#8cf">/docs</a></p>
</body></html>"""


def create_app(config_path: Path | str | None = None, *, start_bot: bool = True, db_path: Path | str | None = None,
               log_dir: Path | None = None) -> FastAPI:
    cm = ConfigManager(Path(config_path) if config_path else DEFAULT_CONFIG_PATH)
    cfg = cm.config
    data_dir = PROJECT_ROOT / cfg.server.data_dir
    journal = Journal(db_path or (data_dir / "bot.db"))
    rb = setup_logging(log_dir or (PROJECT_ROOT / "logs"), cfg.server.log_level, journal)
    hub = WSHub()
    bot = TradingBot(cm, journal, hub)
    backtests = BacktestService(cm, journal, hub, PROJECT_ROOT)
    rb.subscribers.append(lambda rec: hub.publish("log", rec))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.bind(asyncio.get_running_loop())
        if cfg.server.host not in ("127.0.0.1", "localhost") and not cfg.server.api_token:
            log.warning("server.host=%s without api_token — the UI is reachable from the network WITHOUT auth",
                        cfg.server.host)
        if start_bot:
            await bot.start()
        try:
            yield
        finally:
            if start_bot:
                await bot.stop()

    app = FastAPI(title="MSC Bot", version=__version__, lifespan=lifespan)
    app.state.cm, app.state.journal, app.state.hub = cm, journal, hub
    app.state.bot, app.state.backtests = bot, backtests
    app.state.offline_pipelines = {}

    @app.exception_handler(APIError)
    async def _api_error(_: Request, exc: APIError):
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        details = [{"path": ".".join(str(p) for p in e.get("loc", ()) if p not in ("body", "query")),
                    "msg": e.get("msg", "invalid")} for e in exc.errors()]
        return error_response(422, "VALIDATION_ERROR", "request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        return error_response(exc.status_code, "HTTP_ERROR", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("API error")
        return error_response(500, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")

    @app.middleware("http")
    async def _token_guard(request: Request, call_next):
        token = cm.config.server.api_token
        path = request.url.path
        if token and path.startswith("/api"):
            supplied = (request.headers.get("x-api-token") or request.cookies.get("msc_token")
                        or request.query_params.get("token"))
            if supplied != token:
                return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED",
                                                                        "message": "missing/invalid api token"}})
        response = await call_next(request)
        if token and request.query_params.get("token") == token:
            response.set_cookie("msc_token", token, httponly=True, samesite="strict")
        return response

    app.include_router(router)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        token = cm.config.server.api_token
        if token and (ws.cookies.get("msc_token") or ws.query_params.get("token")) != token:
            await ws.close(code=4401)
            return
        await hub.connect(ws)
        try:
            await ws.send_json({"channel": "status", "payload": bot.status()})
            while True:
                await ws.receive_text()  # client pings / keepalive; server push only
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            hub.disconnect(ws)

    if (WEB_DIST / "index.html").exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def _fallback() -> str:
            return FALLBACK_PAGE

    return app


_app: FastAPI | None = None


def __getattr__(name: str):
    """``uvicorn app.main:app`` — the app is created lazily on first access (keeps imports side-effect free)."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
