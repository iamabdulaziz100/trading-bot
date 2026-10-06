# MSC Bot — Market Structure & Confluence trading bot for MetaTrader 5

A fully automated forex bot implementing the **Market Structure & Confluence** price-action
strategy specified in [`docs/spec/`](docs/spec/00_project_overview.md): body-only N-bar swing pivots,
the "Snake Trick" structure state machine, multi-timeframe trend alignment, AOI clustering, Break &
Retest / Head & Shoulders patterns, exact candlestick triggers and a 4-pillar confluence checklist.
Trades are "set & forget" bracket orders (SL + TP attached, never modified afterwards).

It connects to **MetaTrader 5** through the official Python API, runs a local **web UI**
(dashboard, live charts, journal, backtests, settings, logs) on `http://127.0.0.1:8000`, and
includes an event-driven **backtester** that replays history through the exact same strategy code.

> **Demo first.** The spec's go-live checklist (`docs/spec/08_testing_and_acceptance.md` §5) asks for
> a 2–4 week unattended demo run before any live capital. Trading involves risk of loss; this is a
> probabilistic strategy with no guaranteed edge.

---

## Requirements

* **Windows** 10/11 or a Windows VPS (the `MetaTrader5` Python package only works on Windows and
  talks to a locally installed terminal).
* **MetaTrader 5 terminal** installed and logged in to your **demo** account, with
  *Tools → Options → Expert Advisors → Allow algorithmic trading* enabled and the **Algo Trading**
  toolbar button switched on.
* **Python 3.11 or 3.12, 64-bit** (must match the 64-bit terminal) — <https://www.python.org/downloads/windows/>.
  Tick "Add python.exe to PATH" / install the `py` launcher.
* Node.js is **not** needed: the built UI is committed in `web/dist/`.

## Install & run (Windows)

```bat
git clone https://github.com/iamabdulaziz100/trading-bot.git
cd trading-bot
setup_windows.bat        :: creates .venv, installs requirements, copies .env.example → .env
```

1. Open MT5 and log in to the demo account (or put the account in `.env` — see below).
2. Check `config.yaml`: the `symbols` must match your broker's symbol names exactly
   (some brokers use suffixes such as `EURUSDm` or `EURUSD.pro`).
3. Start the bot:

```bat
run_bot.bat              :: = python -m app  (opens http://127.0.0.1:8000)
```

On start the bot connects to MT5 (retry with back-off), detects the broker's UTC offset, warms up
the structure state from history (≈ 320 W1 / 600 D1 / 2200 H4 / 3000 H1 bars per symbol),
reconciles any open bot positions, and then checks for newly closed candles every 5 seconds.

### Credentials (`.env`)

Leave everything empty to use the account that is already logged in to the terminal, or fill in
`.env` (git-ignored, never uploaded):

```ini
MSC_MT5_LOGIN=12345678
MSC_MT5_PASSWORD=your-password
MSC_MT5_SERVER=Broker-Server-Name
MSC_MT5_PATH=            ; optional: C:\Program Files\MetaTrader 5\terminal64.exe
```

### Auto-start (optional)

Windows Task Scheduler → *Create Task* → trigger *At log on* → action *Start a program*:
`C:\path\to\trading-bot\run_bot.bat`, *Start in*: `C:\path\to\trading-bot`. Make sure MT5 also
starts at log-on (put a shortcut to `terminal64.exe` in `shell:startup`). On restart the bot
reconciles open positions by magic number and never alters them.

---

## Web UI

| Page | What it shows / does |
|---|---|
| **Dashboard** | MT5 connection, bot enabled/halted, today's P/L vs the daily-loss limit, open positions vs cap, news blackouts with countdowns, per-symbol trend (1W/1D/4H) and alignment, open positions with floating P/L and R, today's trades, 30-day equity sparkline. **Enable/Disable** (new entries only) and **Panic Close All** (double confirmation; closes bot positions only and disables the bot). |
| **Charts** | Candles for any symbol/TF with AOI zones, HH/HL/LH/LL swing labels, BOS/CHoCH and sweep markers, EMA50, open SL/TP lines, trade markers, and a "bodies only" view. |
| **Journal** | Every confluence evaluation at an AOI with per-pillar pass/fail (P1 trend, P2 AOI, P3 pattern, P4 trigger, EMA), decision and reason; trade history with CSV export; risk-gate events. |
| **Backtests** | Upload/inspect history, run backtests with spread/slippage/commission/risk overrides, live progress, metrics, equity & drawdown charts, per-symbol/per-month tables, trade list + CSV, rejected-signal log. |
| **Settings** | Every `config.yaml` key grouped by section with defaults, spec references, validation and *rebuild/restart required* badges; backtest symbol-spec editor; news feed status, CSV upload and upcoming events. |
| **Logs** | Live log tail with level filter, search and download. |

API contract: [`docs/API.md`](docs/API.md) (REST under `/api`, WebSocket `/ws`, Swagger at `/docs`).
The server binds to `127.0.0.1` only; if you ever bind it to a LAN address set `server.api_token`.

---

## Strategy in one screen

For each symbol, on every closed candle (all times UTC):

1. **Structure** per TF (1W, 1D, 4H, and the 1H execution TF): body-only pivots with an N = 5
   window (confirmed N bars later), CLOSE-only breaks, Snake-Trick traceback of HL/LH, wick
   pierces = liquidity sweeps.
2. **Pillar 1 — trend alignment**: Long if (1W ∧ 1D bullish) ∨ (1D ∧ 4H bullish); short mirror.
3. **Pillar 2 — AOI**: price touching a valid 1W/1D zone (≥ 3 pivot touches, 5–60 pips wide)
   inside the active structural range, on the correct side.
4. **Pillar 3 — pattern**: an unexpired Break & Retest or confirmed+retested (inverse) H&S neckline.
5. **Pillar 4 — trigger**: engulfing (both prior bodies), morning/evening star, hammer/shooting star.
6. ≥ 3 pillars incl. a trigger → SL = trigger wick ∓ 15 pips, TP = 2R capped at the HTF HH/LL
   (rejected if the cap leaves < 2R) → risk gates (news blackout, daily-loss cutoff, concurrency,
   one trade per symbol, 1 % sizing) → bracket market order.

Details: [`docs/spec/02_strategy_engine_spec.md`](docs/spec/02_strategy_engine_spec.md) and the
judgment calls in [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Safety model

* SL and TP are attached to every order; if the broker drops them the bot modifies **once**, and if
  that fails it **closes the position immediately** (no naked positions). After placement the bot
  never modifies a position.
* One execution per signal id (unique DB constraint) — retries/crashes cannot duplicate orders.
* Realized R:R after slippage < `min_rr − rr_slippage_tolerance` → immediate close (`RR_SLIPPAGE_ABORT`).
* Daily loss ≤ −3 % of the day-start balance (realized + floating) → no new entries until the next
  day boundary. News blackout ±30 min around high-impact events for either currency.
* Unhandled exception or DB write failure → trading halted (open positions keep server-side SL/TP).
* Manual positions (other magic numbers) are never touched; `PANIC` closes bot positions only.

---

## Backtesting

1. Export broker candles on the Windows machine (recommended):
   ```bat
   .venv\Scripts\python tools\export_mt5_history.py --years 6
   ```
   This writes UTC CSVs to `data/history/{SYMBOL}_{TF}.csv` (1W, 1D, 4H, 1H).
   Alternatively upload CSVs on the Backtests page (canonical `time_utc,open,high,low,close`,
   MT5 "Export bars" or Dukascopy format). Missing higher TFs are resampled from 1H (noted in the report).
2. Run from the Backtests page (or `POST /api/backtests`). Fills: next-candle open with spread and
   slippage, SL-first when a candle spans both SL and TP, gap fills at the open, same risk code as
   live. Spreads/pip values per symbol: `config/bt_symbol_specs.yaml` (Settings page editor).
3. Historical news is not bundled, so backtests ignore the news filter unless you upload a news CSV
   and set `backtest.apply_news_filter: true`.

## Configuration

Everything lives in [`config.yaml`](config.yaml) (UI edits write back to it, comments preserved,
history kept in the DB). Full generated reference: [`docs/CONFIG_REFERENCE.md`](docs/CONFIG_REFERENCE.md)
(regenerate with `python tools/gen_config_reference.py`). Changing structure/AOI/pattern/candle
parameters requires **Rebuild structure** (button in the UI); MT5/server keys require a restart.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

120+ tests: golden datasets for every formula in spec 08 (pip engine, body pivots, snake trick,
structure, 27-combo alignment matrix, AOI, exact candle ratios, B&R, H&S incl. the right-shoulder
guard, 16 pillar combinations, trade params, sizing, risk gates), backtest acceptance (no-lookahead
perturbation test, hand-verified metrics, SL-first pessimism, no `is_backtest` branches in the
strategy), config/API tests, and the **live trading path against an in-memory fake MT5**
(warm-up, polling, filling-mode fallback, SL/TP verification, monitor, idempotency) including a
**live-vs-backtest parity check**. They run on any OS; the real MT5 integration tests (spec 08 §2,
I1–I10) must be done on the Windows demo account.

### Development on Linux/macOS

Everything except the MT5 connection runs anywhere: `python -m app` starts the API/UI (MT5 shows as
unavailable; charts fall back to the CSV history), backtests and tests work normally. UI work:
see [`web/README.md`](web/README.md).

## Project layout

```
app/
  core/        strategy engine — series, indicators, swings, structure, alignment, aoi,
               patterns, candles, confluence, trade_params, pipeline (shared by live + backtest)
  risk/        sizing, daily-loss tracker, news calendar, risk manager (gates)
  mt5/         connector (MetaTrader5 API), execution engine, position monitor
  backtest/    CSV loader/resampler, simulator, metrics, background service
  api/         REST routes, WebSocket hub
  db/          SQLite journal
  bot.py       live orchestrator · main.py FastAPI app · config.py schema/validation
config.yaml, config/bt_symbol_specs.yaml
web/           React + Vite + TypeScript UI (built bundle in web/dist)
tools/         export_mt5_history.py, gen_config_reference.py
tests/         pytest suite (+ fake MT5)
docs/          spec pack, API contract, decisions, config reference
```
