# Implementation decisions & spec conflicts

The spec pack (`docs/spec/`) is implemented as written wherever it gives an explicit formula or
value. This file lists every place where a judgment call was needed (marked `[DECISION]` in code)
and every conflict found inside the spec, with the choice made and how to change it.

## ⚠ Spec conflict — risk defaults (please confirm)

`03_risk_management_spec.md` §3 lists `risk_per_trade_pct` as **"5% to 10.0%"** and
`max_daily_loss_pct` as **"30.0%"**, while its own text says the cutoff fires at **−3 %**, and
`07_configuration_schema.md` (the config contract) sets **1.0 %** and **3.0 %** with a **hard
validation cap of 5 %** on risk per trade (UI warning above 2 %).

**Implemented:** the `07` values — `risk_per_trade_pct: 1.0`, `max_daily_loss_pct: 3.0`, hard cap
5 %. A 10 % risk per trade would be rejected by the spec's own validation rule and would wipe a
demo account in a handful of losses. If the higher numbers were intended, change them in
`config.yaml` / the Settings page (the 5 % hard cap lives in `app/config.py → RiskConfig`).

## Strategy engine (02)

| Topic | Decision |
|---|---|
| **Pivot ties** (§C) | With body pivots, the next candle very often opens exactly at the previous close, so two adjacent bars share the same body extreme and the strictly-greater formula on *both* sides produces **no pivot at all** at many real turning points. Default `structure.pivot_tie_rule: first_of_equal` keeps the left comparison strict and allows equality on the right (one pivot = first bar of the plateau). Data without ties gives identical results. `strict` = formula exactly as written. |
| **Bump filter** (§C) | Distance from the candidate to the last confirmed *opposite* pivot must be ≥ `max(min_swing_atr_mult × ATR(14), min_swing_pips × pip)`. `min_swing_atr_mult: 0` disables the filter entirely (pure N-window). |
| **Structure initialisation** (§E) | State stays UNDEFINED until one confirmed swing high H and one swing low L exist; the first close above H (below L) sets BULLISH (BEARISH). The snake range for that first break is `[min(H.index, L.index), t]`. |
| **Snake fallback** (§D) | argmin/argmax bar not a confirmed pivot → nearest confirmed pivot of that kind inside K (by bar distance, then more extreme price). None → keep the previous boundary and journal `SNAKE_NO_PIVOT`. On a BOS where no pivot exists the previous HH (LL) becomes the new LH (HL). |
| **Opposite boundaries after a shift** | A bearish shift clears HH/HL (and vice versa) so stale levels are never used for confinement or TP caps. |
| **"Macro" structure** (§G.3, §L) | Confinement and the TP cap use the structure of the **zone's own timeframe** when its state agrees with the trade direction, else the **1D** structure (always aligned: both §F clauses require T_1D = direction). |
| **Pillar gating** (§M) | Exactly as in the §M pseudocode: NEUTRAL_FILTER and "no valid AOI interaction" are skips (logged at DEBUG, not journaled); every evaluation *at* an AOI is journaled with per-pillar pass/fail. A TradeSignal needs ≥ `min_pillars` **and** a pillar-4 trigger, because the trigger candle defines entry and SL (§M: `csig is None → return`). |
| **Interacting zone choice** | Among valid, confined, correct-side zones whose range the candle touches: higher TF first (1W before 1D), then most touches. |
| **Correct side** (§G.4) | BUY needs the candle to close at/above `z_min` (support side); SELL needs close at/below `z_max`. |
| **AOI construction** (§G.1) | Sweep window W = `max_width_pips`, step `sweep_step_pips`; greedy: best window (tie → tightest pivot extent) becomes a candidate, its pivots are removed, repeat while count ≥ `min_touches`. Candidates whose centres are within the merge tolerance merge; over-wide zones drop their outermost pivot (farthest from the median) until ≤ 60 pips or < 3 touches (dropped). Narrow zones are padded to 5 pips and touches recounted over all pivots. Zones rebuild on every close of their TF (skipped when the pivot set is unchanged). |
| **Break & Retest arming** (§H.1) | Arming requires a *crossing* close (`close(t) > z_max` with `close(t−1) ≤ z_max`), otherwise any pull-back into a zone price has been above for months would count as a "retest". B&R trackers run on every execution-TF candle for every valid zone so breakouts are not missed while price is away from the zone; armed state survives zone rebuilds when the zone barely moves. |
| **H&S details** (§H.2) | Threshold = `neckline_threshold_atr_mult × ATR(14)` of the execution TF at detection. The neckline must be body-closed through within `head_shoulders.expiry_bars` of detection; a close beyond the head invalidates. The **PatternEvent is emitted at the neckline retest** (high ≥ neckline − tol and body ≤ neckline + tol, within `retest_window_bars`, using the B&R tolerance), so pillar 3 can only pass after confirmation + retest — the right-shoulder region can never produce a pattern. A neckline break that happened while S_R was still unconfirmed (< N bars) is honoured by replaying those bars at detection (no lookahead: all bars ≤ t). |
| **EMA seed** (§J) | SMA of the first 50 closes, then the §J recursion. |
| **Signal id** | `{symbol}-{exec_tf}-{trigger candle open epoch}` — deterministic, so a restart that re-evaluates the same candle cannot double-trade (DB unique constraint). |

## Risk & execution (03, 04)

| Topic | Decision |
|---|---|
| Ambiguous order replies | No reply / TIMEOUT / ERROR / CONNECTION / LOCKED may still have executed: the bot looks for a position or entry deal carrying the order comment `MSC|<signal_id>` before any re-send. Only clear rejections (requote, price changed/off, reject, too many requests) are retried, and each retry re-checks enabled state, zone tolerance and R:R at the new price. |
| Naked-position safety sweep | Every sync (30 s) and at startup: a bot position without SL or TP gets its journaled bracket attached once; still naked on the next pass (or no journaled bracket) → closed; a close that fails halts trading. This is the only modification the bot ever sends to an open position. |
| Session lock | An order and its verification, a position sync, panic and reconnects never interleave (`conn.op_lock` + the bot's asyncio lock). |
| Magic number | Fixed at startup; changing `mt5.magic_number` takes effect only after a restart (otherwise open positions would be orphaned). |
| Broker UTC offset | Detected only while ticks are arriving (freshest tick time must advance), re-checked hourly; after the first detection only a ±1 h DST change is accepted directly, other jumps need two consecutive readings. Any change after warm-up triggers a full structure rebuild. `MSC_MT5_SERVER_OFFSET_HOURS` fixes it. |
| Crash recovery | Trading starts only after the startup reconcile succeeded. PENDING rows are resolved from open positions or deal history by order comment (so fills/P&L during downtime are not lost). |
| Gate order | ENABLED → NEWS → DAILY_LOSS → MAX_TRADES_DAY (only if > 0) → CONCURRENCY → SYMBOL_DUP → SIZING, stop at first BLOCK; all evaluated gates journaled. Disabled/halted bots still evaluate and journal signals (as REJECTED: ENABLED). |
| Pending/stale | Pre-trade checks (TTL 300 s, price within 10 pips of the AOI edge) run in both live and backtest. |
| NO_MONEY | Symbol blocked until the next trading day (04 §7). |
| Stale feed | Flagged only while the symbol's tick is fresh (< 2 min) but the last bar is > 2×TF old, so weekends are not "stale". |
| Bot enabled at first start | Yes (fully-automated product decision); the Enable/Disable state is persisted and survives restarts. |
| v2 flags | `breakeven/trailing/partial_close_enabled: true` are rejected by validation (not implemented — strict set & forget). |
| Credentials | Via git-ignored `.env` (`MSC_MT5_LOGIN/PASSWORD/SERVER/PATH`); applied at runtime, never written back to `config.yaml`, password masked in the API. |
| `risk_per_trade_pct_max` | Only validated against `risk_per_trade_pct` when `pillar_risk_scaling` is on. |
| News feed | Default `https://nfs.faireconomy.media/ff_calendar_thisweek.json` (ForexFactory weekly mirror). Feed considered down if no successful fetch for max(2 × poll + 5 min, 3 h). Default failure mode `allow` + persistent UI warning (per 01 §6). |

## Backtester (05)

| Topic | Decision |
|---|---|
| Warm-up | Same bar counts as the live warm-up (`app/timeframes.py: WARMUP_BARS`) before `date_from`, so both modes start from equivalent structure state. |
| Prices | CSV prices are treated as BID. BUY fills at next open + spread + slippage, SELL at next open − slippage; short exits on ask (= bid + spread). |
| Day boundary | UTC (CSV data carries no broker offset) — noted in every report. |
| Missing HTF files | Resampled from the lowest lower TF (UTC boundaries, weeks start Sunday like MT5) — noted in the report. Prefer exporting real broker candles with `tools/export_mt5_history.py`. |
| News | Off by default. With `apply_news_filter: true` the calendar stored in the DB (feed history + uploaded CSVs) is used. |
| Margin | `lots × contract × base-ccy USD value / leverage` ≤ free margin; crosses use `base_to_usd` from the spec table. Account currency USD. |

## Tech stack

Python 3.11+, FastAPI/Uvicorn, SQLite (stdlib `sqlite3`), pandas/numpy, React + Vite + TypeScript
+ lightweight-charts. The built UI (`web/dist`) is committed so the Windows machine needs no Node.js.
