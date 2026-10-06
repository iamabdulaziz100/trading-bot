# 06 — Web UI & API Specification

Local single-user app. FastAPI serves REST + WebSocket on `127.0.0.1:8000` and the built React SPA at `/`. No authentication (localhost binding IS the security boundary; config `api_token` optional if user re-binds to LAN — default warn against it).

## 1. Pages (React + TypeScript + lightweight-charts)

### 1.1 Dashboard
- Status cards: MT5 connection (green/red), bot enabled/disabled, today's P/L vs daily-loss limit (progress bar), open positions count vs cap, active news blackouts (symbols + countdown).
- Open positions table: symbol, dir, entry, SL, TP, lots, floating P/L, R at risk, pillars.
- Today's closed trades mini-table + equity sparkline (30 days).
- Buttons: Enable/Disable bot, Panic Close All (double confirm).

### 1.2 Charts
- Symbol + timeframe selector; candlestick chart via `lightweight-charts`.
- Overlays: AOI zones (rectangles, colored by side/validity), structure labels (HH/HL/LH/LL markers at confirmed swings), BOS/CHoCH event flags, 50 EMA line, open trade SL/TP lines, executed trade markers.
- "Bodies-only" toggle reproducing the strategy's no-wick chart view (§4.2 of strategy) — implemented as a line/area series of closes.
- Data endpoint serves candles + computed overlays (server-side computed = same engine objects as trading).

### 1.3 Signals / Journal
- Every confluence evaluation at an AOI: timestamp, symbol, direction, pillars checked (1–4 with per-pillar pass/fail detail), candle signal, pattern, decision (EXECUTED / REJECTED: reason), links to chart state.
- Trade journal: full history incl. backtest-flagged rows; filters (date, symbol, result); CSV export.

### 1.4 Backtests
- Form: symbols, date range, balance, spread/slippage/commission overrides → run with live progress → results view: metric cards (from 05 §5), equity curve chart, drawdown chart, per-symbol/per-month tables, trade list with CSV download, config snapshot display.

### 1.5 Settings
- All `config.yaml` parameters grouped (Strategy / Risk / News / MT5 / Backtest / UI) with validation + descriptions; save → hot-reload where safe (structure params require "rebuild state" action — button provided).
- Symbol spec table editor (pip values for backtests).
- News feed status + manual CSV upload; upcoming events list.

### 1.6 Logs
- Live tail of bot log (WebSocket), level filter, text search; download current log file.

## 2. REST API (JSON; prefix `/api`)

| Method & Path | Purpose |
|---|---|
| `GET /api/status` | connection, enabled, day P/L, limits state, blackout state |
| `GET /api/account` | balance, equity, margin |
| `GET /api/positions` | open bot positions |
| `GET /api/trades?from&to&symbol&result` | trade journal (paginated) |
| `GET /api/signals?...` | confluence evaluations incl. rejected |
| `GET /api/candles?symbol&tf&from&to` | OHLC |
| `GET /api/overlays?symbol&tf` | AOIs, swings, structure labels, EMA, events |
| `GET /api/config` / `PUT /api/config` | read/update config (validated; writes YAML+DB) |
| `POST /api/bot/enable` / `POST /api/bot/disable` | toggle trading |
| `POST /api/bot/panic` | close all + disable |
| `POST /api/structure/rebuild?symbol` | recompute structure state after param change |
| `GET /api/news/events` / `POST /api/news/upload_csv` | calendar |
| `POST /api/backtests` / `GET /api/backtests/{id}` / `GET /api/backtests/{id}/trades.csv` | backtest lifecycle |
| `GET /api/logs?level&q&tail` | log query |

Error format: `{error: {code, message, details?}}` with HTTP 4xx/5xx. All mutations journaled.

## 3. WebSocket (`/ws`)
Server→client push, JSON `{channel, payload}`:
- `status` — connection/limit changes (immediate).
- `positions` — open-position updates (≤1/s throttle).
- `signal` — new confluence evaluation / execution.
- `trade` — open/close events.
- `log` — log lines (INFO+).
- `backtest` — progress %, completion.
Client auto-reconnects with 1s→30s backoff; on reconnect, full state resync via REST.

## 4. Database Schema (SQLite)

```sql
trades(id, signal_id UNIQUE, symbol, direction, lots, entry_time, entry_price,
       sl, tp, exit_time, exit_price, exit_reason, pnl_money, r_multiple,
       pillars, pattern_type, candle_signal, is_backtest, created_at)
signals(id, ts, symbol, timeframe, direction, pillar1..pillar4, ema_ok,
        pillar_count, candle_signal, pattern, decision, reason, is_backtest)
risk_events(id, ts, symbol, gate, result, details)
news_events(id, ts_utc, currency, impact, title, source)
daily_stats(day, start_balance, end_balance, pnl, trades, wins, cutoff_hit)
config_history(id, ts, json_snapshot, actor)
backtest_runs(id, ts, params_json, status, metrics_json, equity_json)
journal_events(id, ts, level, module, message, context_json)
```

## 5. UX Notes
- Dark theme default. All money in account currency; pips shown with JPY-correct decimals.
- Every settings field shows its default and "restart required?" badge.
- Dashboard must be readable at a glance from 2m away (large status colors).
