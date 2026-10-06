"""REST API (06 §2) — see docs/API.md for the JSON contract."""
from __future__ import annotations

import csv
import io
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.backtest.data_loader import df_to_candles, load_symbol_history
from app.config import PROJECT_ROOT, config_schema, validation_details
from app.core.pipeline import SymbolPipeline, merge_candle_events
from app.db.journal import TRADE_COLS
from app.logging_setup import ring
from app.mt5.connector import MT5Error
from app.pip_engine import PipEngineError, pip_size, price_decimals
from app.risk.news import parse_csv
from app.symbols import load_bt_specs, save_bt_specs
from app.timeframes import TF_SECONDS

log = logging.getLogger("api")
router = APIRouter(prefix="/api")


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        self.status, self.code, self.message, self.details = status, code, message, details


def error_response(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


def _bot(request: Request):
    return request.app.state.bot


def _journal(request: Request):
    return request.app.state.journal


def _page(page: int | None, page_size: int | None) -> tuple[int, int]:
    return max(1, page or 1), max(1, min(500, page_size or 50))


def _filters(**kw: Any) -> dict[str, Any]:
    return {k: v for k, v in kw.items() if v not in (None, "")}


def _epoch(value: str | None) -> int | None:
    if not value:
        return None
    if value.isdigit():
        return int(value)
    v = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(v if "T" in v or " " in v else v + "T00:00:00+00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


# ── status / account / positions ────────────────────────────────────────────────────────

@router.get("/status")
def status(request: Request) -> dict:
    return _bot(request).status()


@router.get("/account")
def account(request: Request):
    bot = _bot(request)
    if not bot.conn.connected:
        raise APIError(503, "MT5_DISCONNECTED", bot.conn.message)
    acc = bot.conn.account(5.0)
    if acc is None:
        raise APIError(503, "MT5_DISCONNECTED", "account_info unavailable")
    return acc


@router.get("/positions")
def positions(request: Request) -> dict:
    return {"positions": _bot(request).positions_view}


# ── journal ─────────────────────────────────────────────────────────────────────────────

@router.get("/trades")
def trades(request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None,
           symbol: str | None = None, result: str | None = None, is_backtest: str | None = "0",
           backtest_run_id: str | None = None, page: int | None = 1, page_size: int | None = 50) -> dict:
    p, ps = _page(page, page_size)
    f = _filters(**{"from": from_, "to": to, "symbol": symbol, "result": result, "is_backtest": is_backtest,
                    "backtest_run_id": backtest_run_id})
    items, total = _journal(request).list_trades(f, p, ps)
    return {"items": items, "total": total, "page": p, "page_size": ps}


def _csv_response(rows: list[dict], cols: list[str] | tuple[str, ...], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(cols), extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/trades.csv")
def trades_csv(request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None,
               symbol: str | None = None, result: str | None = None, is_backtest: str | None = "0"):
    f = _filters(**{"from": from_, "to": to, "symbol": symbol, "result": result, "is_backtest": is_backtest})
    return _csv_response(_journal(request).all_trades(f), TRADE_COLS, "trades.csv")


@router.get("/signals")
def signals(request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None,
            symbol: str | None = None, decision: str | None = None, is_backtest: str | None = "0",
            page: int | None = 1, page_size: int | None = 50) -> dict:
    p, ps = _page(page, page_size)
    f = _filters(**{"from": from_, "to": to, "symbol": symbol, "decision": decision, "is_backtest": is_backtest})
    items, total = _journal(request).list_signals(f, p, ps)
    return {"items": items, "total": total, "page": p, "page_size": ps}


@router.get("/risk_events")
def risk_events(request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None,
                symbol: str | None = None, gate: str | None = None, result: str | None = None,
                page: int | None = 1, page_size: int | None = 50) -> dict:
    p, ps = _page(page, page_size)
    f = _filters(**{"from": from_, "to": to, "symbol": symbol, "gate": gate, "result": result})
    items, total = _journal(request).list_risk_events(f, p, ps)
    return {"items": items, "total": total, "page": p, "page_size": ps}


# ── charts ──────────────────────────────────────────────────────────────────────────────

_offline_lock = threading.Lock()


def _pipeline(request: Request, symbol: str) -> SymbolPipeline | None:
    """Live pipeline, or (when MT5 is unavailable) one replayed from the history CSVs."""
    bot = _bot(request)
    pipe = bot.pipelines.get(symbol)
    if pipe is not None or bot.conn.connected:
        return pipe
    cache: dict = request.app.state.offline_pipelines
    if symbol in cache:
        return cache[symbol]
    cfg = bot.cfg
    hist = PROJECT_ROOT / cfg.server.data_dir / "history"
    with _offline_lock:
        try:
            frames, _ = load_symbol_history(hist, symbol, cfg.all_tfs)
        except (FileNotFoundError, ValueError):
            cache[symbol] = None
            return None
        pipe = SymbolPipeline(symbol, cfg, pip_size(symbol))
        data = {tf: df_to_candles(df.tail(4000)) for tf, df in frames.items()}
        for tf, c in merge_candle_events(data, symbol):
            pipe.on_candle_closed(tf, c)
        cache[symbol] = pipe
        log.info("offline chart pipeline built for %s from history CSVs", symbol)
        return pipe


@router.get("/symbols")
def symbols(request: Request) -> dict:
    cfg = _bot(request).cfg
    return {"symbols": cfg.symbols, "timeframes": cfg.all_tfs, "execution_tf": cfg.exec_tf}


@router.get("/candles")
def candles(request: Request, symbol: str, tf: str, limit: int = 500,
            from_: str | None = Query(None, alias="from"), to: str | None = None) -> dict:
    if tf not in TF_SECONDS:
        raise APIError(400, "BAD_TF", f"unknown timeframe {tf}")
    try:
        pip = pip_size(symbol)
    except PipEngineError as exc:
        raise APIError(400, "BAD_SYMBOL", str(exc)) from exc
    bot = _bot(request)
    spec = bot.specs.get(symbol)
    digits = spec.digits if spec else price_decimals(pip)
    pipe = _pipeline(request, symbol)
    rows = pipe.candles(tf, max(1, min(limit, 20000)), _epoch(from_), _epoch(to)) if pipe and tf in pipe.tfs else []
    return {"symbol": symbol, "tf": tf, "pip_size": pip, "digits": digits, "candles": rows}


@router.get("/overlays")
def overlays(request: Request, symbol: str, tf: str) -> dict:
    bot = _bot(request)
    if symbol in bot.pipelines:
        return bot.overlays(symbol, tf)
    pipe = _pipeline(request, symbol)
    if pipe is None or tf not in pipe.tfs:
        return {"structure": None, "zones": [], "swings": [], "events": [], "ema": [], "positions": [], "trades": []}
    out = pipe.overlays(tf)
    out.update(positions=[], trades=[])
    return out


# ── bot control ─────────────────────────────────────────────────────────────────────────

@router.post("/bot/enable")
def bot_enable(request: Request) -> dict:
    _bot(request).set_enabled(True)
    return {"enabled": True}


@router.post("/bot/disable")
def bot_disable(request: Request) -> dict:
    _bot(request).set_enabled(False)
    return {"enabled": False}


@router.post("/bot/panic")
async def bot_panic(request: Request) -> dict:
    return await _bot(request).panic()


@router.post("/structure/rebuild")
async def structure_rebuild(request: Request, symbol: str | None = None) -> dict:
    bot = _bot(request)
    request.app.state.offline_pipelines.clear()
    if not bot.conn.connected:
        bot.rebuild_required = False
        return {"ok": True, "symbols": [symbol] if symbol else list(bot.cfg.symbols),
                "note": "MT5 not connected — offline chart state cleared"}
    try:
        done = await bot.rebuild([symbol] if symbol else None)
    except MT5Error as exc:
        raise APIError(503, "MT5_DISCONNECTED", str(exc)) from exc
    return {"ok": True, "symbols": done}


# ── config ──────────────────────────────────────────────────────────────────────────────

@router.get("/config")
def get_config(request: Request) -> dict:
    return request.app.state.cm.masked_dict()


@router.get("/config/schema")
def get_config_schema(request: Request) -> dict:
    out = config_schema()
    out["env_overrides"] = request.app.state.cm.env_overrides
    return out


@router.put("/config")
async def put_config(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict):
        raise APIError(422, "VALIDATION_ERROR", "body must be a JSON object")
    try:
        res = request.app.state.cm.update(body)
    except ValidationError as exc:
        raise APIError(422, "VALIDATION_ERROR", "config validation failed", validation_details(exc)) from exc
    request.app.state.offline_pipelines.clear()
    return {"ok": True, "config": request.app.state.cm.masked_dict(), "rebuild_required": res.rebuild_required,
            "restart_required": res.restart_required, "warnings": res.warnings}


@router.get("/symbol_specs")
def get_symbol_specs(request: Request) -> dict:
    bt = request.app.state.backtests
    specs = load_bt_specs(bt.specs_path)
    out = {}
    for sym, s in specs.items():
        d = {"pip_size": s.pip_size, "digits": s.digits, "pip_value_per_lot": s.pip_value_per_lot,
             "contract_size": s.contract_size, "spread_pips": s.spread_pips, "commission_per_lot": s.commission_per_lot,
             "min_lot": s.min_lot, "max_lot": s.max_lot, "lot_step": s.lot_step}
        if s.base_to_usd is not None:
            d["base_to_usd"] = s.base_to_usd
        out[sym] = d
    return {"specs": out}


@router.put("/symbol_specs")
async def put_symbol_specs(request: Request) -> dict:
    body = await request.json()
    specs = body.get("specs") if isinstance(body, dict) else None
    if not isinstance(specs, dict):
        raise APIError(422, "VALIDATION_ERROR", "body must be {\"specs\": {SYMBOL: {...}}}")
    clean = {}
    for sym, d in specs.items():
        if not isinstance(d, dict):
            raise APIError(422, "VALIDATION_ERROR", f"{sym}: spec must be an object")
        clean[sym.upper()] = {k: v for k, v in d.items() if v is not None}
    try:
        save_bt_specs(request.app.state.backtests.specs_path, clean)
    except (ValueError, TypeError) as exc:
        raise APIError(422, "VALIDATION_ERROR", str(exc)) from exc
    return get_symbol_specs(request)


# ── news ────────────────────────────────────────────────────────────────────────────────

@router.get("/news/events")
def news_events(request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None) -> dict:
    bot = _bot(request)
    import time as _t

    now = _t.time()
    start = _epoch(from_) or int(now - 86400)
    end = _epoch(to) or int(now + 7 * 86400)
    evs = bot.news.events(start, end)
    cfg = bot.cfg.news
    return {"events": [{"id": i + 1, "ts_utc": e.iso(), "currency": e.currency, "impact": e.impact,
                        "title": e.title, "source": e.source} for i, e in enumerate(evs)],
            "status": {"feed_ok": bot.news.feed_ok(now, cfg.poll_minutes),
                       "last_fetch_utc": datetime.fromtimestamp(bot.news.last_fetch_ok, tz=timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ") if bot.news.last_fetch_ok else None,
                       "source": cfg.feed_url or bot.news.source, "warning": bot.news.warning(now, cfg)}}


@router.post("/news/upload_csv")
async def news_upload(request: Request, file: UploadFile = File(...)) -> dict:
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    try:
        events = parse_csv(content)
    except ValueError as exc:
        raise APIError(422, "BAD_CSV", str(exc)) from exc
    bot = _bot(request)
    bot.news.add(events)
    _journal(request).upsert_news([(e.iso(), e.currency, e.impact, e.title, e.source) for e in events])
    log.info("news CSV uploaded: %d events", len(events))
    return {"imported": len(events)}


@router.post("/news/refresh")
async def news_refresh(request: Request) -> dict:
    bot = _bot(request)
    n = await bot.refresh_news()
    if n == 0 and bot.news.last_error:
        raise APIError(502, "NEWS_FEED_ERROR", bot.news.last_error)
    return {"ok": True, "fetched": n}


# ── backtests ───────────────────────────────────────────────────────────────────────────

@router.get("/backtests/data")
def backtest_data(request: Request) -> dict:
    return {"files": request.app.state.backtests.files()}


@router.post("/backtests/upload")
async def backtest_upload(request: Request, file: UploadFile = File(...), symbol: str = Form(...),
                          tf: str = Form(...), tz_offset_hours: float = Form(0.0)) -> dict:
    try:
        rows = request.app.state.backtests.save_upload(symbol.strip().upper(), tf.strip().upper(), await file.read(),
                                                       tz_offset_hours)
    except (ValueError, KeyError) as exc:
        raise APIError(422, "BAD_CSV", str(exc)) from exc
    request.app.state.offline_pipelines.pop(symbol.strip().upper(), None)
    return {"ok": True, "rows": rows}


@router.post("/backtests")
async def backtest_create(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict) or not body.get("date_from") or not body.get("date_to"):
        raise APIError(422, "VALIDATION_ERROR", "date_from and date_to are required")
    try:
        run_id = request.app.state.backtests.submit(body)
    except (ValueError, ValidationError) as exc:
        raise APIError(422, "VALIDATION_ERROR", str(exc)) from exc
    return {"id": run_id, "status": "queued"}


@router.get("/backtests")
def backtest_list(request: Request) -> dict:
    return {"items": request.app.state.backtests.list()}


@router.get("/backtests/{run_id}")
def backtest_get(request: Request, run_id: int) -> dict:
    r = request.app.state.backtests.get(run_id)
    if r is None:
        raise APIError(404, "NOT_FOUND", f"backtest {run_id} not found")
    return r


@router.get("/backtests/{run_id}/trades.csv")
def backtest_trades_csv(request: Request, run_id: int):
    r = request.app.state.backtests.get(run_id)
    if r is None:
        raise APIError(404, "NOT_FOUND", f"backtest {run_id} not found")
    cols = ["signal_id", "symbol", "direction", "lots", "entry_time", "entry_price", "sl", "tp", "exit_time",
            "exit_price", "exit_reason", "pnl_money", "r_multiple", "risk_money", "rr_planned", "rr_realized",
            "pillars", "pattern_type", "candle_signal"]
    return _csv_response(r["trades"], cols, f"backtest_{run_id}_trades.csv")


# ── logs ────────────────────────────────────────────────────────────────────────────────

@router.get("/logs")
def logs(level: str = "INFO", q: str = "", tail: int = 500) -> dict:
    return {"lines": ring().query(level, q, max(1, min(tail, 5000)))}


@router.get("/logs/download")
def logs_download():
    p = Path(PROJECT_ROOT) / "logs" / "bot.log"
    if not p.exists():
        raise APIError(404, "NOT_FOUND", "no log file yet")
    return FileResponse(p, media_type="text/plain", filename="bot.log")
