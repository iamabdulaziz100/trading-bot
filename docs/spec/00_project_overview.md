# 00 — Project Overview: MSC Bot (Market Structure & Confluence Bot)

## 1. Purpose
Build a **fully automated trading bot** implementing the "Market Structure & Confluence" price-action strategy, as formalized in the owner's **"Part 1: Algorithmic Architecture & Mathematical Specifications"** document — the AUTHORITATIVE strategy definition (body-only N=5 pivots, argmin/argmax Snake Trick traceback, two-trough neckline, exact candle-trigger formulas, R-multiple TP capped at macro boundary, strict set & forget). Doc `02_strategy_engine_spec.md` restates it as engineering requirements. The bot connects to **MetaTrader 5** via the official Python API, runs a **local web-based UI** (dashboard, charts, journal, settings, logs), and trades a **demo MT5 account first**.

## 2. Locked Product Decisions (from owner)

| # | Decision | Value |
|---|----------|-------|
| 1 | Execution mode | **Fully automated** — bot places/manages orders on MT5 with no manual approval |
| 2 | Instruments | **Forex majors/minors only** (includes JPY pairs → pip = 0.01 for JPY, else 0.0001) |
| 3 | Risk model | **Fixed % of account balance per trade** (default 1%) |
| 4 | Risk limits | **News blackout** (no new trades around high-impact news) + **Max daily loss cutoff** (halt trading for the day) |
| 5 | Backtesting | **Yes** — built-in backtesting module on historical data |
| 6 | Swing detection | Configurable minimum swing size (pips/ATR multiplier), tuned by owner |
| 7 | Web UI scope | **Full suite**: dashboard, live charts, trade journal, settings, logs, backtest runner |
| 8 | Environment | **MT5 demo account first**; live-ready but demo-validated |

## 3. Non-Goals (v1)
- No multi-account / prop-firm copier features.
- No manual structure marking in UI (bot is fully algorithmic; config-tunable).
- No trade management after entry beyond SL/TP ("set & forget" per strategy). No breakeven moves, no trailing stops, no partial closes (leave as disabled config flags for v2).
- No crypto, indices, or metals symbol handling (pip-value logic is forex-only).

## 4. High-Level Components
```
┌─────────────┐   OHLC/ticks/orders   ┌──────────────┐
│  MT5        │◄────────────────────►│  Bot Core    │
│  Terminal   │   MetaTrader5 API    │  (Python)    │
└─────────────┘                      └──────┬───────┘
                                            │ REST + WebSocket
                                     ┌──────▼───────┐
                                     │  Web UI      │
                                     │  (Browser)   │
                                     └──────────────┘
```
- **Bot Core** (Python, FastAPI): strategy engine, risk manager, execution engine, backtester, SQLite journal.
- **Web UI** (React + Lightweight Charts): served locally by the same FastAPI process.

## 5. Suggested Tech Stack (defaults; coding agent may deviate with justification)
- Python 3.11+, `MetaTrader5` pip package (requires **Windows** or Windows VPS — the MT5 Python API talks to a locally installed MT5 terminal).
- FastAPI + Uvicorn (REST + WebSocket), SQLite via SQLAlchemy or sqlite3.
- React + Vite + TypeScript frontend, `lightweight-charts` for charting.
- Pandas/NumPy for indicator math. Pytest for tests.
- All config in a single `config.yaml` + UI-editable settings persisted to DB.

## 6. Milestones
| Milestone | Deliverable | Exit criteria |
|-----------|-------------|----------------|
| M1 | MT5 connector + data feed | Fetches OHLC for configured symbols/TFs; reconnects on drop |
| M2 | Structure engine (body-pivots, state machine, snake trace, AOI) | Passes golden-dataset unit tests (see 08_testing) |
| M3 | Pattern + candle engines | B&R, H&S, star/engulfing detection pass fixture tests |
| M4 | Confluence + risk + execution | Opens/closes trades on demo per rules; daily-loss cutoff and news blackout verified |
| M5 | Backtester | Reproduces a live demo period trade-for-trade within tolerance |
| M6 | Web UI full suite | All pages functional against live bot |
| M7 | Hardening | 2–4 week unattended demo run, acceptance checklist green |

## 7. Reading Order for the Coding Agent
1. This file → 2. `01_system_architecture.md` → 3. `02_strategy_engine_spec.md` (core) → 4. `03_risk_management_spec.md` → 5. `04_mt5_integration_spec.md` → 6. `05_backtesting_spec.md` → 7. `06_web_ui_api_spec.md` → 8. `07_configuration_schema.md` → 9. `08_testing_and_acceptance.md`.

## 8. Ambiguity Resolution Log
The strategy is defined by the owner's formal mathematical spec (authoritative). Remaining gaps that still required a judgment call are marked **`[DECISION]`** with the chosen default and exposed in `config.yaml`: H&S neckline alignment threshold value, AOI merge tolerance, news-feed failure mode, backtest fill assumptions. Everything else traces to an explicit formula in the formal spec.
