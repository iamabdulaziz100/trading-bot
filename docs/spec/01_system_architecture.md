# 01 — System Architecture

## 1. Process Layout
Single Python process hosting: MT5 connector, strategy pipeline, execution engine, backtester (on-demand), SQLite journal, and a FastAPI server (REST + WebSocket) that also serves the built React frontend. Local-only: bind to `127.0.0.1`.

```
                        ┌──────────────────────── BOT PROCESS ────────────────────────┐
                        │                                                             │
  MT5 Terminal ◄──────► │  mt5_connector ──► data_feed (OHLC cache, candle-close      │
  (demo acct)  orders   │                       events)                               │
                        │        │                                                    │
                        │        ▼                                                    │
                        │  structure_engine (swings→structure state→AOI per TF)       │
                        │        │                                                    │
                        │        ▼                                                    │
                        │  pattern_engine (B&R, H&S) + candle_engine (star/engulfing) │
                        │        │                                                    │
                        │        ▼                                                    │
                        │  confluence_engine (4-pillar checklist → TradeSignal)       │
                        │        │                                                    │
                        │        ▼                                                    │
                        │  risk_manager (sizing, daily-loss cutoff, news blackout,    │
                        │        │         concurrency limits)                        │
                        │        ▼                                                    │
                        │  execution_engine (order_send, SL/TP, retry, journal)       │
                        │        │                                                    │
                        │  journal_db (SQLite)  ◄──  logger (rotating files)          │
                        │        │                                                    │
                        │  FastAPI (REST + WS) ──► serves React UI on 127.0.0.1:8000  │
                        │                                                             │
                        │  backtester (reuses strategy pipeline, event-driven, offline)│
                        └─────────────────────────────────────────────────────────────┘
```

## 2. Module Responsibilities

| Module | Responsibility | Key outputs |
|--------|----------------|-------------|
| `mt5_connector` | Init/login to terminal, watchdog/reconnect, symbol info, tick & order APIs | connection state |
| `data_feed` | Pull OHLC per symbol/timeframe, incremental updates, in-memory cache, emit `CandleClosed` events | closed-candle events |
| `structure_engine` | ZigZag swings ("Snake Trick"), structure state machine (BULLISH/BEARISH), BOS/CHoCH events, AOI zones | `StructureState`, `AOI[]` per symbol+TF |
| `pattern_engine` | Break & Retest state tracker, Head & Shoulders / Inverted H&S detector | `PatternEvent` |
| `candle_engine` | Doji/hammer/shooting-star/engulfing/morning-star/evening-star classifiers | `CandleSignal` |
| `confluence_engine` | 4-pillar checklist scoring → trade decision (direction, entry, SL, TP, pillar count) | `TradeSignal` |
| `risk_manager` | % balance sizing, daily-loss cutoff, news blackout window check, max concurrent trades | approved/rejected `OrderPlan` |
| `execution_engine` | Build and send MT5 order with SL/TP, filling-mode handling, retries, idempotency | MT5 deal/position |
| `journal_db` | Trades, signals (incl. rejected), daily stats, config history, backtest runs | SQLite rows |
| `api` | REST endpoints + WebSocket push (status, trades, signals, config, logs, backtests) | JSON |
| `web_ui` | React SPA: Dashboard, Charts, Journal, Backtests, Settings, Logs | — |
| `backtester` | Event-driven replay of historical candles through the SAME strategy pipeline | `BacktestResult` |

## 3. Core Data Flow (live trading loop)
1. `data_feed` polls MT5 (or uses tick-driven check) and detects a **newly closed candle** on any configured timeframe for any configured symbol.
2. On `CandleClosed(symbol, tf)`: `structure_engine` updates swings/structure/AOI for that TF.
3. If TF is an execution timeframe (default 1H): `pattern_engine` and `candle_engine` evaluate; `confluence_engine` scores the 4 pillars.
4. If pillar count ≥ `min_pillars` (default 3) → `TradeSignal` sent to `risk_manager`.
5. `risk_manager` gates: bot enabled? news blackout? daily loss cutoff hit? max concurrent trades? position already open on symbol? → computes lot size → `OrderPlan`.
6. `execution_engine` places the market order with SL/TP, records everything to `journal_db`, pushes updates over WebSocket.
7. "Set & forget": positions are never modified; MT5 SL/TP close them.

## 4. Threading/Async Model
- One asyncio event loop. MT5 API calls are synchronous and blocking → run them in a `ThreadPoolExecutor` (max_workers=2) with a global MT5 lock (the MT5 Python API is not thread-safe).
- Candle polling cadence: every 5 seconds, check for new closed candles (cheap `copy_rates_from_pos` of last 2 candles per symbol+TF).
- WebSocket broadcasts are fire-and-forget; UI reconnects with backoff.

## 5. State & Persistence
- **SQLite** (`data/bot.db`): trades, signals, journal events, daily_stats, config history, backtest runs/results summary.
- **Config**: `config.yaml` (file = source of truth at startup; UI edits write through to both DB and YAML).
- **Logs**: rotating file logs (`logs/bot.log`), plus `journal_events` table for UI display.
- **Crash recovery**: on startup, reconcile MT5 open positions (by magic number) against DB `trades` table; resume monitoring without altering positions (set & forget).

## 6. News Calendar Source `[DECISION]`
Default: fetch high-impact events from a public economic calendar feed (e.g., ForexFactory weekly JSON/XML mirror) once per hour, cache locally, map affected currencies → symbols (block a symbol if EITHER currency of the pair has a high-impact event within the blackout window). Fallback if feed is down: user-uploaded CSV (`date,time_utc,currency,impact,title`) + fail-safe config `news_feed_failure_mode: block_all | allow` (default `block_all` for majors during the configured news hours is too aggressive — default is `allow` with a loud UI warning; owner tunes).

## 7. Timezone Handling
All internal timestamps **UTC**. MT5 server time is converted to UTC at the connector boundary (offset auto-detected and logged). Daily-loss cutoff and day boundaries use **broker server day** by default (config `day_boundary: server|utc`).

## 8. Deployment
- Windows machine or Windows VPS with MT5 terminal installed and logged into the demo account.
- Start script: `run_bot.bat` → activates venv, starts `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
- Auto-start option: Windows Task Scheduler entry (documented, not scripted).
