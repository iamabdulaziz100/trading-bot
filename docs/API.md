# MSC Bot — REST / WebSocket API contract

Implements `docs/spec/06_web_ui_api_spec.md`. Base URL `http://127.0.0.1:8000`.
All REST paths are prefixed with `/api`. All timestamps are **UTC**: ISO-8601 strings
(`"2026-10-06T13:00:00Z"`) unless a field is documented as `epoch` (integer seconds, used by
chart data so it can be fed straight into lightweight-charts).

Errors are always `HTTP 4xx/5xx` with body:
```json
{"error": {"code": "VALIDATION_ERROR", "message": "human readable", "details": [...]}}
```

---------------------------------------------------------------------------------------------
## Status & account

### `GET /api/status`
```jsonc
{
  "server_time_utc": "2026-10-06T13:00:05Z",
  "mt5": {
    "connected": true,
    "available": true,              // false => MetaTrader5 package missing (non-Windows host)
    "message": "connected",         // or last error text
    "server": "Broker-Demo", "login": 12345678, "trade_allowed": true,
    "server_utc_offset_sec": 10800
  },
  "bot": {
    "enabled": true,                 // UI toggle (new entries only)
    "halted": false,                 // safety halt (exception / DB failure)
    "halt_reason": null,
    "warmed_up": true,
    "started_at": "2026-10-06T08:00:00Z",
    "last_cycle_utc": "2026-10-06T13:00:01Z",
    "rebuild_required": false        // structure params changed -> POST /api/structure/rebuild
  },
  "daily": {
    "day": "2026-10-06",
    "start_balance": 10000.0,
    "pnl": -120.5,                   // equity - start_balance
    "pnl_pct": -1.205,
    "limit_pct": 3.0,
    "limit_money": 300.0,
    "used_fraction": 0.40,           // 0..1+ (loss / limit), 0 when in profit -> progress bar
    "cutoff_hit": false,
    "trades_today": 1
  },
  "positions": {"open": 1, "max": 3},
  "account": {"balance": 10000.0, "equity": 9879.5, "margin": 33.1,
              "free_margin": 9846.4, "currency": "USD"},          // null when disconnected
  "news": {
    "enabled": true, "feed_ok": true, "last_fetch_utc": "2026-10-06T12:30:00Z",
    "warning": null,                 // non-null => show persistent warning banner
    "blackouts": [
      {"symbol": "EURUSD", "currency": "USD", "title": "Non-Farm Payrolls",
       "event_time_utc": "2026-10-06T13:30:00Z", "blocked_until_utc": "2026-10-06T14:00:00Z",
       "seconds_remaining": 3595}
    ]
  },
  "symbols": [
    {"symbol": "EURUSD", "paused": false, "reason": null,
     "trend": {"1W": 1, "1D": 1, "4H": -1},          // +1 bullish, -1 bearish, 0 undefined
     "alignment": "LONG",                            // LONG | SHORT | NEUTRAL_FILTER
     "last_candle_utc": "2026-10-06T12:00:00Z"}
  ],
  "warnings": ["News feed down — feed_failure_mode=allow"]
}
```

### `GET /api/account`
`{"balance","equity","margin","free_margin","margin_level","currency","leverage","login","server","name"}`
— `503 MT5_DISCONNECTED` when not connected.

### `GET /api/positions`
```jsonc
{"positions": [
  {"ticket": 123, "signal_id": "EURUSD-1H-1759755600", "symbol": "EURUSD", "direction": "BUY",
   "lots": 0.33, "entry_price": 1.10012, "sl": 1.09700, "tp": 1.10636,
   "current_price": 1.10100, "profit": 29.04,       // floating P/L, account ccy
   "risk_money": 100.0, "floating_r": 0.29, "pillars": 3,
   "open_time_utc": "2026-10-06T12:00:03Z"}
]}
```

---------------------------------------------------------------------------------------------
## Journal

### `GET /api/trades`
Query: `from`, `to` (ISO date or datetime), `symbol`, `result` (`win|loss|open`),
`is_backtest` (`0|1`, default `0`), `page` (1-based, default 1), `page_size` (default 50, max 500).
```jsonc
{"items": [Trade], "total": 120, "page": 1, "page_size": 50}
```
`Trade`:
```jsonc
{"id": 1, "signal_id": "EURUSD-1H-1759755600", "symbol": "EURUSD", "direction": "BUY",
 "status": "OPEN",                     // PENDING | OPEN | CLOSED | FAILED | ABORTED
 "lots": 0.33, "entry_time": "...", "entry_price": 1.10012, "sl": 1.097, "tp": 1.10636,
 "exit_time": null, "exit_price": null,
 "exit_reason": null,                  // TP | SL | MANUAL_EXTERNAL | PANIC | RR_SLIPPAGE_ABORT | SAFETY_CLOSE | END_OF_TEST
 "pnl_money": null, "r_multiple": null, "risk_money": 100.0,
 "pillars": 3, "pattern_type": "BREAK_RETEST", "candle_signal": "BULL_ENGULF",
 "ticket": 123, "is_backtest": false, "created_at": "..."}
```
### `GET /api/trades.csv` — same filters, CSV download.

### `GET /api/signals`
Query: `from`, `to`, `symbol`, `decision` (`EXECUTED|REJECTED`), `is_backtest`, `page`, `page_size`.
`{"items": [Signal], "total", "page", "page_size"}`. `Signal`:
```jsonc
{"id": 1, "ts": "2026-10-06T12:00:00Z", "symbol": "EURUSD", "timeframe": "1H",
 "direction": "BUY",
 "pillar1": true, "pillar2": true, "pillar3": false, "pillar4": true,
 "ema_ok": true, "pillar_count": 3,
 "candle_signal": "BULL_ENGULF",       // or null
 "pattern": "BREAK_RETEST",            // or null
 "decision": "EXECUTED",               // EXECUTED | REJECTED
 "reason": "LOW_CONFLUENCE",           // why rejected (null when executed)
 "signal_id": "EURUSD-1H-1759755600",
 "details": {"pillar1": "1W=+1 1D=+1 4H=-1 -> LONG", "pillar2": "1D zone 1.0990-1.1010 (4 touches)",
             "pillar3": "no unexpired pattern", "pillar4": "BULL_ENGULF",
             "entry": 1.1001, "sl": 1.097, "tp": 1.1063, "rr": 2.0},
 "is_backtest": false}
```

### `GET /api/risk_events`
Query: `from`, `to`, `symbol`, `gate`, `page`, `page_size`.
Item: `{"id","ts","symbol","signal_id","gate","result","details"}` where
`gate ∈ SIZING|DAILY_LOSS|NEWS|CONCURRENCY|SYMBOL_DUP|RR_CAP|RR_SLIPPAGE|ENABLED|MAX_TRADES_DAY|EXECUTION`,
`result ∈ PASS|BLOCK`.

---------------------------------------------------------------------------------------------
## Charts

### `GET /api/symbols`
`{"symbols": ["EURUSD", ...], "timeframes": ["1W","1D","4H","1H"], "execution_tf": "1H"}`

### `GET /api/candles?symbol=EURUSD&tf=1H&limit=500`  (optional `from`, `to`)
```jsonc
{"symbol": "EURUSD", "tf": "1H", "pip_size": 0.0001, "digits": 5,
 "candles": [{"time": 1759752000, "open": 1.1, "high": 1.101, "low": 1.099, "close": 1.1005}]}
```
`time` is **epoch seconds UTC of candle open**.

### `GET /api/overlays?symbol=EURUSD&tf=1H`
```jsonc
{
  "structure": {"state": "BULLISH", "active_HH": 1.12, "active_HL": 1.09,
                "active_LH": null, "active_LL": null},
  "zones": [   // AOIs of all zone timeframes for the symbol (drawn as horizontal bands)
    {"tf": "1D", "z_min": 1.0990, "z_max": 1.1010, "touches": 4, "valid": true,
     "confined": true, "side": "support"}      // support (below price) | resistance (above)
  ],
  "swings": [{"time": 1759752000, "price": 1.1012, "kind": "HIGH", "label": "HH"}],  // HH|HL|LH|LL
  "events": [{"time": 1759752000, "type": "BOS_BULLISH", "price": 1.1012}],
       // BOS_BULLISH | BOS_BEARISH | INIT_BULLISH | INIT_BEARISH | LIQUIDITY_SWEEP
  "ema": [{"time": 1759752000, "value": 1.0991}],     // EMA50, only for the execution TF
  "positions": [{"direction": "BUY", "entry_price": 1.1, "sl": 1.097, "tp": 1.106,
                 "open_time": 1759752000}],
  "trades": [{"time": 1759752000, "price": 1.1, "direction": "BUY", "kind": "entry"},
             {"time": 1759790000, "price": 1.106, "direction": "BUY", "kind": "exit",
              "exit_reason": "TP"}]
}
```

---------------------------------------------------------------------------------------------
## Bot control

| Request | Response |
|---|---|
| `POST /api/bot/enable` | `{"enabled": true}` |
| `POST /api/bot/disable` | `{"enabled": false}` |
| `POST /api/bot/panic` | `{"closed": [123], "failed": [], "enabled": false}` — UI must double-confirm first |
| `POST /api/structure/rebuild?symbol=EURUSD` (symbol optional = all) | `{"ok": true, "symbols": ["EURUSD"]}` |

---------------------------------------------------------------------------------------------
## Config

### `GET /api/config`
The full `config.yaml` as JSON (see `docs/spec/07_configuration_schema.md`). `mt5.password`
is masked as `"********"` when set.

### `GET /api/config/schema`
```jsonc
{"sections": [
  {"key": "structure", "title": "Structure engine", "fields": [
    {"path": "structure.pivot_window_n", "type": "int",   // int|float|bool|str|enum|list|dict
     "default": 5, "description": "Symmetric N-bar body pivot window", "spec_ref": "02 §C",
     "min": 2, "max": 20, "warn_min": null, "warn_max": null,
     "options": null,                 // for enum: allowed values
     "restart_required": false, "rebuild_required": true}
  ]}
]}
```
Sections: `mt5, universe, structure, aoi, patterns, candles, confluence, trade, risk, news, backtest, server`.

### `PUT /api/config`
Body = full config JSON (as returned by GET; a masked password is kept unchanged).
200 → `{"ok": true, "config": {...}, "rebuild_required": bool, "restart_required": bool, "warnings": ["..."]}`
422 → `{"error": {"code": "VALIDATION_ERROR", "message": "...", "details": [{"path": "risk.risk_per_trade_pct", "msg": "..."}]}}`

### `GET /api/symbol_specs` / `PUT /api/symbol_specs`
Backtest symbol spec table (`config/bt_symbol_specs.yaml`):
`{"specs": {"EURUSD": {"pip_size": 0.0001, "digits": 5, "pip_value_per_lot": "auto", "contract_size": 100000,
"spread_pips": 1.0, "commission_per_lot": 0.0, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01}}}`
(`pip_value_per_lot` is a number or `"auto"`). PUT body has the same shape.

---------------------------------------------------------------------------------------------
## News

- `GET /api/news/events?from&to` →
  `{"events": [{"id","ts_utc","currency","impact","title","source"}], "status": {"feed_ok","last_fetch_utc","source","warning"}}`
  (default window: now−1d … now+7d)
- `POST /api/news/upload_csv` multipart, field `file`; CSV columns `date,time_utc,currency,impact,title`
  → `{"imported": 12}`
- `POST /api/news/refresh` → `{"ok": true, "fetched": 57}`

---------------------------------------------------------------------------------------------
## Backtests

- `GET /api/backtests/data` → `{"files": [{"symbol": "EURUSD", "tf": "1H", "rows": 12000, "from": "...", "to": "..."}]}`
  (CSV files available in `data/history/`)
- `POST /api/backtests/upload` multipart: `file`, `symbol`, `tf` → `{"ok": true, "rows": 12000}`
- `POST /api/backtests` body:
  ```jsonc
  {"symbols": ["EURUSD"], "date_from": "2024-01-01", "date_to": "2025-12-31",
   "initial_balance": 10000, "spread_pips": null, "slippage_pips": 0.5, "commission_per_lot": 0,
   "risk_per_trade_pct": null}          // null => use config defaults
  ```
  → `{"id": 7, "status": "queued"}`
- `GET /api/backtests` → `{"items": [{"id","ts","status","progress","params","metrics"}]}` (no big arrays)
- `GET /api/backtests/{id}` →
  ```jsonc
  {"id": 7, "ts": "...", "status": "running",   // queued | running | done | error
   "progress": 0.42, "error": null, "params": {...},
   "config_hash": "ab12…", "config_snapshot": {...},
   "metrics": {"trades": 40, "wins": 18, "losses": 22, "win_rate": 0.45,
               "net_pnl": 1234.5, "net_pnl_pct": 12.3, "gross_profit": 0, "gross_loss": 0,
               "profit_factor": 1.6, "expectancy_r": 0.31, "avg_rr_realized": 2.1,
               "max_drawdown_pct": 6.2, "max_drawdown_money": 650.0, "max_drawdown_duration_days": 34.0,
               "sharpe_daily": 1.1, "start_balance": 10000, "final_balance": 11234.5,
               "pillars_distribution": {"3": 30, "4": 10}, "rejected_signals": 210},
   "equity": [{"time": 1704067200, "equity": 10000.0}],        // epoch, per closed trade
   "drawdown": [{"time": 1704067200, "dd_pct": 0.0}],
   "per_symbol": [{"symbol": "EURUSD", "trades": 20, "win_rate": 0.5, "net_pnl": 500.0, "sum_r": 4.0}],
   "per_month": [{"month": "2024-01", "trades": 3, "win_rate": 0.33, "net_pnl": -50.0, "sum_r": -0.5}],
   "trades": [Trade],                   // full list (is_backtest=true), also exit_reason etc.
   "rejected": [Signal],                // last ≤ 2000 rejected checklist evaluations
   "notes": ["News filter not applied (no historical calendar bundled)"]}
  ```
- `GET /api/backtests/{id}/trades.csv` — CSV download.

---------------------------------------------------------------------------------------------
## Logs

- `GET /api/logs?level=INFO&q=text&tail=500` → `{"lines": [{"ts","level","module","message"}]}`
- `GET /api/logs/download` → current `logs/bot.log`.

---------------------------------------------------------------------------------------------
## WebSocket `/ws`

Server → client JSON messages `{"channel": "...", "payload": ...}`:

| channel | payload |
|---|---|
| `status` | same object as `GET /api/status` (pushed on change + every 5 s) |
| `positions` | `{"positions": [...]}` (≤ 1/s) |
| `signal` | `Signal` |
| `trade` | `Trade` |
| `log` | `{"ts","level","module","message"}` (INFO+) |
| `backtest` | `{"id","status","progress","metrics"?}` |

Client reconnects with exponential backoff 1 s → 30 s and re-syncs via REST after reconnect.
