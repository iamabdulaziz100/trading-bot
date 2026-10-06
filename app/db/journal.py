"""SQLite journal (06 §4). Journal integrity is a hard requirement: a write that still fails
after 3 attempts raises :class:`JournalError` and the bot halts new entries (03 §6, 04 §7)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id TEXT NOT NULL UNIQUE,
  symbol TEXT, direction TEXT, lots REAL,
  entry_time TEXT, entry_price REAL, sl REAL, tp REAL,
  exit_time TEXT, exit_price REAL, exit_reason TEXT,
  pnl_money REAL, r_multiple REAL,
  pillars INTEGER, pattern_type TEXT, candle_signal TEXT,
  is_backtest INTEGER NOT NULL DEFAULT 0, created_at TEXT,
  status TEXT, ticket INTEGER, risk_money REAL, timeframe TEXT,
  planned_entry REAL, rr_planned REAL, backtest_run_id INTEGER, details_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_trades_status ON trades(status, is_backtest);
CREATE INDEX IF NOT EXISTS ix_trades_entry ON trades(entry_time);
CREATE TABLE IF NOT EXISTS signals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, symbol TEXT, timeframe TEXT, direction TEXT,
  pillar1 INTEGER, pillar2 INTEGER, pillar3 INTEGER, pillar4 INTEGER, ema_ok INTEGER,
  pillar_count INTEGER, candle_signal TEXT, pattern TEXT, decision TEXT, reason TEXT,
  is_backtest INTEGER NOT NULL DEFAULT 0, signal_id TEXT, details_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_signals_ts ON signals(ts);
CREATE TABLE IF NOT EXISTS risk_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, symbol TEXT, signal_id TEXT, gate TEXT, result TEXT, details TEXT
);
CREATE INDEX IF NOT EXISTS ix_risk_ts ON risk_events(ts);
CREATE TABLE IF NOT EXISTS news_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TEXT, currency TEXT, impact TEXT, title TEXT, source TEXT,
  UNIQUE(ts_utc, currency, title)
);
CREATE TABLE IF NOT EXISTS daily_stats(
  day TEXT PRIMARY KEY, start_balance REAL, end_balance REAL, pnl REAL,
  trades INTEGER, wins INTEGER, cutoff_hit INTEGER
);
CREATE TABLE IF NOT EXISTS config_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, json_snapshot TEXT, actor TEXT
);
CREATE TABLE IF NOT EXISTS backtest_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, params_json TEXT, status TEXT,
  metrics_json TEXT, equity_json TEXT, progress REAL DEFAULT 0, error TEXT,
  result_json TEXT, config_hash TEXT
);
CREATE TABLE IF NOT EXISTS journal_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, module TEXT, message TEXT, context_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_journal_ts ON journal_events(ts);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""

TRADE_COLS = ("id", "signal_id", "symbol", "direction", "status", "lots", "entry_time", "entry_price", "sl",
              "tp", "exit_time", "exit_price", "exit_reason", "pnl_money", "r_multiple", "risk_money", "pillars",
              "pattern_type", "candle_signal", "ticket", "is_backtest", "created_at", "timeframe",
              "planned_entry", "rr_planned", "backtest_run_id")


class JournalError(RuntimeError):
    pass


class DuplicateSignal(JournalError):
    pass


def iso(ts: float | datetime | None = None) -> str:
    if ts is None:
        dt = datetime.fromtimestamp(time.time(), tz=timezone.utc)
    elif isinstance(ts, datetime):
        dt = ts.astimezone(timezone.utc)
    else:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_date(value: str | None, end: bool = False) -> str | None:
    """Accept YYYY-MM-DD or ISO datetime; return a sortable ISO string bound."""
    if not value:
        return None
    v = value.strip()
    if len(v) == 10:
        return v + ("T23:59:59Z" if end else "T00:00:00Z")
    return v.replace("+00:00", "Z")


class Journal:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── low level ──────────────────────────────────────────────────────────────────────
    def _write(self, sql: str, params: tuple | list = ()) -> int:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with self._lock:
                    cur = self._conn.execute(sql, params)
                    self._conn.commit()
                    return cur.lastrowid or cur.rowcount
            except sqlite3.IntegrityError as exc:
                if "UNIQUE" in str(exc) and "signal_id" in str(exc):
                    raise DuplicateSignal(str(exc)) from exc
                raise JournalError(str(exc)) from exc
            except sqlite3.Error as exc:
                last_exc = exc
                time.sleep(0.2 * (attempt + 1))
        raise JournalError(f"DB write failed after 3 attempts: {last_exc}")

    def _query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # ── kv ─────────────────────────────────────────────────────────────────────────────
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        rows = self._query("SELECT value FROM kv WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def kv_set(self, key: str, value: str) -> None:
        self._write("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value))

    # ── trades ─────────────────────────────────────────────────────────────────────────
    def insert_trade(self, row: dict[str, Any]) -> int:
        row = dict(row)
        row.setdefault("created_at", iso())
        row.setdefault("is_backtest", 0)
        cols = [c for c in row if c != "id"]
        sql = f"INSERT INTO trades({','.join(cols)}) VALUES({','.join('?' * len(cols))})"
        return self._write(sql, [row[c] for c in cols])

    def insert_trades_bulk(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        cols = [c for c in rows[0] if c != "id"]
        sql = f"INSERT OR REPLACE INTO trades({','.join(cols)}) VALUES({','.join('?' * len(cols))})"
        with self._lock:
            self._conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])
            self._conn.commit()

    def update_trade(self, signal_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        self._write(f"UPDATE trades SET {sets} WHERE signal_id=?", [*fields.values(), signal_id])

    def get_trade(self, signal_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM trades WHERE signal_id=?", (signal_id,))
        return self._trade_out(rows[0]) if rows else None

    def trade_by_ticket(self, ticket: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM trades WHERE ticket=? AND is_backtest=0", (ticket,))
        return self._trade_out(rows[0]) if rows else None

    def open_trades(self) -> list[dict[str, Any]]:
        return [self._trade_out(r) for r in
                self._query("SELECT * FROM trades WHERE is_backtest=0 AND status IN ('OPEN','PENDING')")]

    @staticmethod
    def _trade_out(r: dict[str, Any]) -> dict[str, Any]:
        out = {k: r.get(k) for k in TRADE_COLS}
        out["is_backtest"] = bool(out.get("is_backtest"))
        return out

    def _trade_filters(self, f: dict[str, Any]) -> tuple[str, list[Any]]:
        where, params = ["is_backtest=?"], [1 if str(f.get("is_backtest", "0")) in ("1", "true", "True") else 0]
        if f.get("from"):
            where.append("COALESCE(entry_time, created_at) >= ?")
            params.append(_norm_date(f["from"]))
        if f.get("to"):
            where.append("COALESCE(entry_time, created_at) <= ?")
            params.append(_norm_date(f["to"], end=True))
        if f.get("symbol"):
            where.append("symbol=?")
            params.append(f["symbol"])
        res = f.get("result")
        if res == "win":
            where.append("status='CLOSED' AND pnl_money > 0")
        elif res == "loss":
            where.append("status='CLOSED' AND pnl_money <= 0")
        elif res == "open":
            where.append("status IN ('OPEN','PENDING')")
        if f.get("backtest_run_id"):
            where.append("backtest_run_id=?")
            params.append(int(f["backtest_run_id"]))
        return " AND ".join(where), params

    def list_trades(self, f: dict[str, Any], page: int = 1, page_size: int = 50) -> tuple[list[dict], int]:
        w, p = self._trade_filters(f)
        total = self._query(f"SELECT COUNT(*) AS n FROM trades WHERE {w}", p)[0]["n"]
        rows = self._query(f"SELECT * FROM trades WHERE {w} ORDER BY COALESCE(entry_time, created_at) DESC, id DESC "
                           f"LIMIT ? OFFSET ?", [*p, page_size, (page - 1) * page_size])
        return [self._trade_out(r) for r in rows], total

    def all_trades(self, f: dict[str, Any]) -> list[dict]:
        w, p = self._trade_filters(f)
        return [self._trade_out(r) for r in
                self._query(f"SELECT * FROM trades WHERE {w} ORDER BY COALESCE(entry_time, created_at), id", p)]

    def trades_closed_between(self, start_iso: str, end_iso: str) -> list[dict]:
        return self._query("SELECT * FROM trades WHERE is_backtest=0 AND status='CLOSED' AND exit_time>=? AND exit_time<?",
                           (start_iso, end_iso))

    def count_trades_since(self, start_iso: str) -> int:
        return self._query("SELECT COUNT(*) AS n FROM trades WHERE is_backtest=0 AND entry_time>=? "
                           "AND status IN ('OPEN','CLOSED')", (start_iso,))[0]["n"]

    # ── signals ────────────────────────────────────────────────────────────────────────
    def insert_signal(self, row: dict[str, Any]) -> int:
        row = dict(row)
        details = row.pop("details", None)
        row["details_json"] = json.dumps(details, default=str) if details is not None else None
        for k in ("pillar1", "pillar2", "pillar3", "pillar4", "is_backtest"):
            row[k] = int(bool(row.get(k)))
        if row.get("ema_ok") is not None:
            row["ema_ok"] = int(bool(row["ema_ok"]))
        cols = list(row)
        return self._write(f"INSERT INTO signals({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                           [row[c] for c in cols])

    @staticmethod
    def _signal_out(r: dict[str, Any]) -> dict[str, Any]:
        out = dict(r)
        for k in ("pillar1", "pillar2", "pillar3", "pillar4", "is_backtest"):
            out[k] = bool(out.get(k))
        if out.get("ema_ok") is not None:
            out["ema_ok"] = bool(out["ema_ok"])
        dj = out.pop("details_json", None)
        out["details"] = json.loads(dj) if dj else {}
        return out

    def list_signals(self, f: dict[str, Any], page: int = 1, page_size: int = 50) -> tuple[list[dict], int]:
        where, params = ["is_backtest=?"], [1 if str(f.get("is_backtest", "0")) in ("1", "true", "True") else 0]
        if f.get("from"):
            where.append("ts >= ?")
            params.append(_norm_date(f["from"]))
        if f.get("to"):
            where.append("ts <= ?")
            params.append(_norm_date(f["to"], end=True))
        if f.get("symbol"):
            where.append("symbol=?")
            params.append(f["symbol"])
        if f.get("decision"):
            where.append("decision=?")
            params.append(f["decision"])
        w = " AND ".join(where)
        total = self._query(f"SELECT COUNT(*) AS n FROM signals WHERE {w}", params)[0]["n"]
        rows = self._query(f"SELECT * FROM signals WHERE {w} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                           [*params, page_size, (page - 1) * page_size])
        return [self._signal_out(r) for r in rows], total

    def get_signal(self, row_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM signals WHERE id=?", (row_id,))
        return self._signal_out(rows[0]) if rows else None

    # ── risk events ────────────────────────────────────────────────────────────────────
    def insert_risk_events(self, ts: str, symbol: str, signal_id: str, gates: list[tuple[str, str, str]]) -> None:
        for gate, result, details in gates:
            self._write("INSERT INTO risk_events(ts,symbol,signal_id,gate,result,details) VALUES(?,?,?,?,?,?)",
                        (ts, symbol, signal_id, gate, result, details))

    def list_risk_events(self, f: dict[str, Any], page: int = 1, page_size: int = 50) -> tuple[list[dict], int]:
        where, params = ["1=1"], []
        if f.get("from"):
            where.append("ts >= ?")
            params.append(_norm_date(f["from"]))
        if f.get("to"):
            where.append("ts <= ?")
            params.append(_norm_date(f["to"], end=True))
        for k in ("symbol", "gate", "result"):
            if f.get(k):
                where.append(f"{k}=?")
                params.append(f[k])
        w = " AND ".join(where)
        total = self._query(f"SELECT COUNT(*) AS n FROM risk_events WHERE {w}", params)[0]["n"]
        rows = self._query(f"SELECT * FROM risk_events WHERE {w} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                           [*params, page_size, (page - 1) * page_size])
        return rows, total

    # ── news ───────────────────────────────────────────────────────────────────────────
    def upsert_news(self, events: list[tuple[str, str, str, str, str]]) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT INTO news_events(ts_utc,currency,impact,title,source) VALUES(?,?,?,?,?) "
                "ON CONFLICT(ts_utc,currency,title) DO UPDATE SET impact=excluded.impact, source=excluded.source",
                events)
            self._conn.commit()

    def news_since(self, start_iso: str) -> list[dict]:
        return self._query("SELECT * FROM news_events WHERE ts_utc >= ? ORDER BY ts_utc", (start_iso,))

    # ── daily stats ────────────────────────────────────────────────────────────────────
    def upsert_daily(self, day: str, **fields: Any) -> None:
        existing = self._query("SELECT day FROM daily_stats WHERE day=?", (day,))
        if existing:
            sets = ",".join(f"{k}=?" for k in fields)
            self._write(f"UPDATE daily_stats SET {sets} WHERE day=?", [*fields.values(), day])
        else:
            cols = ["day", *fields]
            self._write(f"INSERT INTO daily_stats({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                        [day, *fields.values()])

    def get_daily(self, day: str) -> dict | None:
        rows = self._query("SELECT * FROM daily_stats WHERE day=?", (day,))
        return rows[0] if rows else None

    # ── config history ─────────────────────────────────────────────────────────────────
    def insert_config_history(self, snapshot: dict[str, Any], actor: str) -> None:
        self._write("INSERT INTO config_history(ts,json_snapshot,actor) VALUES(?,?,?)",
                    (iso(), json.dumps(snapshot, default=str), actor))

    # ── backtests ──────────────────────────────────────────────────────────────────────
    def create_backtest(self, params: dict[str, Any], cfg_hash: str) -> int:
        return self._write("INSERT INTO backtest_runs(ts,params_json,status,progress,config_hash) VALUES(?,?,?,?,?)",
                           (iso(), json.dumps(params, default=str), "queued", 0.0, cfg_hash))

    def update_backtest(self, run_id: int, **fields: Any) -> None:
        conv = {}
        for k, v in fields.items():
            conv[k] = json.dumps(v, default=str) if k.endswith("_json") and not isinstance(v, str) and v is not None else v
        sets = ",".join(f"{k}=?" for k in conv)
        self._write(f"UPDATE backtest_runs SET {sets} WHERE id=?", [*conv.values(), run_id])

    def get_backtest(self, run_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM backtest_runs WHERE id=?", (run_id,))
        return rows[0] if rows else None

    def list_backtests(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._query("SELECT id, ts, params_json, status, progress, metrics_json, error, config_hash "
                           "FROM backtest_runs ORDER BY id DESC LIMIT ?", (limit,))

    # ── journal events ─────────────────────────────────────────────────────────────────
    def insert_journal_event(self, ts: str, level: str, module: str, message: str, context: dict | None = None) -> None:
        with self._lock:  # no retry/raise: called from the logging handler
            self._conn.execute("INSERT INTO journal_events(ts,level,module,message,context_json) VALUES(?,?,?,?,?)",
                               (ts, level, module, message, json.dumps(context) if context else None))
            self._conn.commit()
