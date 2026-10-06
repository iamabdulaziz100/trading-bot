"""Backtest runs in a background worker; progress is pushed over the WebSocket and results are
stored in ``backtest_runs`` with a config snapshot hash for reproducibility (05 §7)."""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.api.ws import WSHub
from app.backtest.data_loader import (
    available_files,
    df_to_candles,
    history_path,
    load_symbol_history,
    parse_csv_bytes,
    write_canonical,
)
from app.backtest.simulator import BacktestEngine, BTParams, date_epoch
from app.config import BotConfig, ConfigManager, config_hash
from app.db.journal import Journal
from app.risk.news import NewsCalendar, NewsEvent
from app.symbols import default_bt_spec, load_bt_specs
from app.timeframes import TF_SECONDS, WARMUP_BARS

log = logging.getLogger("backtest")


class BacktestService:
    def __init__(self, cm: ConfigManager, journal: Journal, hub: WSHub, root: Path):
        self.cm = cm
        self.journal = journal
        self.hub = hub
        self.root = root
        self.history_dir = root / cm.config.server.data_dir / "history"
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backtest")
        self.progress: dict[int, float] = {}
        self._lock = threading.Lock()

    @property
    def specs_path(self) -> Path:
        return self.root / self.cm.config.backtest.symbol_specs_file

    # ── data ───────────────────────────────────────────────────────────────────────────
    def files(self) -> list[dict]:
        return available_files(self.history_dir)

    def save_upload(self, symbol: str, tf: str, content: bytes, tz_offset_hours: float = 0.0) -> int:
        if tf not in TF_SECONDS:
            raise ValueError(f"unknown timeframe {tf}")
        df = parse_csv_bytes(content, tz_offset_hours)
        if df.empty:
            raise ValueError("CSV contains no rows")
        write_canonical(df, history_path(self.history_dir, symbol.upper(), tf))
        log.info("history uploaded: %s %s (%d rows)", symbol, tf, len(df))
        return len(df)

    # ── runs ───────────────────────────────────────────────────────────────────────────
    def submit(self, body: dict[str, Any]) -> int:
        cfg = self.cm.config
        symbols = [s.upper() for s in (body.get("symbols") or cfg.symbols)]
        params = BTParams(symbols=symbols, date_from=str(body["date_from"])[:10], date_to=str(body["date_to"])[:10],
                          initial_balance=body.get("initial_balance"), spread_pips=body.get("spread_pips"),
                          slippage_pips=body.get("slippage_pips"), commission_per_lot=body.get("commission_per_lot"),
                          risk_per_trade_pct=body.get("risk_per_trade_pct"))
        if date_epoch(params.date_from) >= date_epoch(params.date_to, end=True):
            raise ValueError("date_from must be before date_to")
        run_cfg = self._run_config(cfg, params)
        run_id = self.journal.create_backtest(dict(body, symbols=symbols), config_hash(run_cfg))
        self.progress[run_id] = 0.0
        self.pool.submit(self._run, run_id, params, run_cfg)
        return run_id

    @staticmethod
    def _run_config(cfg: BotConfig, params: BTParams) -> BotConfig:
        data = cfg.model_dump()
        if params.risk_per_trade_pct is not None:
            data["risk"]["risk_per_trade_pct"] = float(params.risk_per_trade_pct)
            data["risk"]["risk_per_trade_pct_max"] = max(data["risk"]["risk_per_trade_pct_max"],
                                                         float(params.risk_per_trade_pct))
        return BotConfig.model_validate(data)

    def _load_data(self, cfg: BotConfig, params: BTParams):
        tfs = cfg.all_tfs
        start = date_epoch(params.date_from)
        end = date_epoch(params.date_to, end=True)
        data, notes = {}, []
        for sym in params.symbols:
            frames, n = load_symbol_history(self.history_dir, sym, tfs)
            notes.extend(n)
            per_tf = {}
            for tf, df in frames.items():
                before = df[df["time"] + TF_SECONDS[tf] <= start].tail(WARMUP_BARS[tf])
                during = df[(df["time"] + TF_SECONDS[tf] > start) & (df["time"] <= end)]
                per_tf[tf] = df_to_candles(before) + df_to_candles(during)
                if len(before) < WARMUP_BARS[tf] // 4:
                    notes.append(f"{sym} {tf}: only {len(before)} warm-up bars before {params.date_from} "
                                 f"(live uses {WARMUP_BARS[tf]})")
            data[sym] = per_tf
        specs_table = load_bt_specs(self.specs_path)
        specs = {}
        for sym in params.symbols:
            if sym not in specs_table:
                notes.append(f"{sym}: no entry in {cfg.backtest.symbol_specs_file} — using defaults")
            specs[sym] = specs_table.get(sym) or default_bt_spec(sym)
        return data, specs, notes

    def _news(self) -> NewsCalendar:
        cal = NewsCalendar()
        rows = self.journal.news_since("1970-01-01T00:00:00Z")
        cal.add([NewsEvent(int(datetime.strptime(r["ts_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                               .timestamp()), r["currency"], r["impact"], r["title"], r["source"] or "db") for r in rows])
        return cal

    def _run(self, run_id: int, params: BTParams, cfg: BotConfig) -> None:
        t0 = time.time()
        try:
            self.journal.update_backtest(run_id, status="running")
            self.hub.publish("backtest", {"id": run_id, "status": "running", "progress": 0.0})
            data, specs, notes = self._load_data(cfg, params)
            news = self._news() if cfg.backtest.apply_news_filter else None
            engine = BacktestEngine(cfg, params, data, specs, news, notes)
            last_push = [0.0]

            def progress(p: float) -> None:
                self.progress[run_id] = p
                if time.time() - last_push[0] > 0.5:
                    last_push[0] = time.time()
                    self.hub.publish("backtest", {"id": run_id, "status": "running", "progress": round(p, 4)})

            result = engine.run(progress)
            result["config_snapshot"] = cfg.model_dump(mode="json")
            result["config_snapshot"]["mt5"]["password"] = ""
            result["elapsed_sec"] = round(time.time() - t0, 2)
            self.journal.update_backtest(run_id, status="done", progress=1.0, metrics_json=result["metrics"],
                                         equity_json=result["equity"], result_json=result)
            rows = []
            for t in result["trades"]:
                r = {k: t.get(k) for k in ("symbol", "direction", "status", "lots", "entry_time", "entry_price", "sl",
                                           "tp", "exit_time", "exit_price", "exit_reason", "pnl_money", "r_multiple",
                                           "risk_money", "pillars", "pattern_type", "candle_signal", "timeframe",
                                           "planned_entry", "rr_planned", "created_at")}
                r.update(signal_id=f"BT{run_id}-{t['signal_id']}", is_backtest=1, backtest_run_id=run_id)
                rows.append(r)
            self.journal.insert_trades_bulk(rows)
            self.progress[run_id] = 1.0
            log.info("backtest %d done in %.1fs: %d trades, net %.2f", run_id, time.time() - t0,
                     result["metrics"]["trades"], result["metrics"]["net_pnl"])
            self.hub.publish("backtest", {"id": run_id, "status": "done", "progress": 1.0, "metrics": result["metrics"]})
        except Exception as exc:
            log.exception("backtest %d failed", run_id)
            self.journal.update_backtest(run_id, status="error", error=f"{type(exc).__name__}: {exc}")
            self.hub.publish("backtest", {"id": run_id, "status": "error", "progress": self.progress.get(run_id, 0)})

    # ── read models ────────────────────────────────────────────────────────────────────
    def list(self) -> list[dict[str, Any]]:
        out = []
        for r in self.journal.list_backtests():
            out.append({"id": r["id"], "ts": r["ts"], "status": r["status"],
                        "progress": self.progress.get(r["id"], r["progress"] or 0.0),
                        "params": json.loads(r["params_json"] or "{}"),
                        "metrics": json.loads(r["metrics_json"]) if r["metrics_json"] else None,
                        "error": r["error"]})
        return out

    def get(self, run_id: int) -> dict[str, Any] | None:
        r = self.journal.get_backtest(run_id)
        if r is None:
            return None
        res = json.loads(r["result_json"]) if r["result_json"] else {}
        return {"id": r["id"], "ts": r["ts"], "status": r["status"],
                "progress": self.progress.get(r["id"], r["progress"] or 0.0), "error": r["error"],
                "params": json.loads(r["params_json"] or "{}"), "config_hash": r["config_hash"],
                "config_snapshot": res.get("config_snapshot"), "metrics": res.get("metrics"),
                "equity": res.get("equity", []), "drawdown": res.get("drawdown", []),
                "per_symbol": res.get("per_symbol", []), "per_month": res.get("per_month", []),
                "trades": res.get("trades", []), "rejected": res.get("rejected", []),
                "risk_events": res.get("risk_events", []), "notes": res.get("notes", []),
                "elapsed_sec": res.get("elapsed_sec")}
