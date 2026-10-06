# 03 — Risk Management Specification

Aligned to the formal algorithmic spec §9 ("Execution, Sizing & Order Management — Set and Forget").

## 1. Position Sizing — Fixed % of Balance

Generic form (per formal spec):
```
Units = (account_balance × risk_pct) / |entry_price − stop_loss|
```

MT5 lot conversion (forex, per owner scope):
```
risk_money   = account_balance × risk_per_trade_pct        (default 1.0%)
sl_distance  = |entry_price − stop_loss|                   (price units)
lots         = risk_money × tick_size / (sl_distance × tick_value)
             # equivalently: risk_money / (sl_distance_in_pips × pip_value_per_lot)
lots         = floor to symbol.lot_step, clamp to [min_lot, max_lot]
```
- Pull `account_balance` fresh from MT5 before each trade (cache ≤5s).
- `tick_value`, `tick_size`, lot limits from `symbol_info` (see 04 §2). Never hardcode pip values.
- Computed lots < `min_lot` → **skip trade**, journal `RISK_SKIP_UNDER_MIN_LOT` (never round up).
- Reject if `sl_distance ≤ 0` or symbol spec unavailable.

## 2. Stop Loss / Take Profit (from formal spec §9)

- **SL:** Long = `low(entry trigger candle/formation) − 15×pip_size`; Short = `high(...) + 15×pip_size`. Buffer config `sl_buffer_pips` default **15**, allowed band 10–20 (UI warns outside).
- **TP:** `entry ± R×|entry−SL|`, `R ∈ [2.0, 4.0]` (config `tp_r_multiple`, default 2.0), **capped at the macro structural boundary** (longs: `active_HH`; shorts: `active_LL`). If the cap forces realized R:R below `min_rr` (2.0) → reject signal (`RR_CAP_REJECT`).
- After fill: recompute realized R:R from actual fill price; if slippage dropped it below `min_rr − rr_slippage_tolerance` (default 0.2) → close immediately, journal `RR_SLIPPAGE_ABORT`.
- **Bracket / OCO semantics:** SL and TP attached at order placement; first fill cancels the other. If broker rejects SL/TP on the market order (filling-mode quirks): place, then immediately `order_modify` SL/TP; if that fails → **close position immediately**, CRITICAL log. Naked positions are never tolerated.
- **Strict Set & Forget:** after placement, no modification whatsoever — no breakeven, no trailing, no partial close, no early exit (config flags exist, default off, v2). MT5 server-side SL/TP close the trade.

## 3. Risk Limits (owner-selected set)

| Limit | Default | Behavior |
|---|---|---|
| `risk_per_trade_pct` | 5% to 10.0% | §1 |
| `max_daily_loss_pct` | 30.0% | Realized+floating day P/L ≤ −3% of day-start balance → **halt new entries** until next day boundary; open positions untouched (set & forget). UI banner + log. |
| `max_concurrent_trades` | 3 | Global cap on bot-magic-number positions. |
| `one_trade_per_symbol` | true | No stacking on a symbol. |
| `max_trades_per_day` | 0 (disabled) | Available, OFF per owner choice. |

Daily tracking: snapshot balance at day boundary (config `day_boundary: server|utc`, default server); evaluate `equity − day_start_balance` every sync cycle (≤30s); auto-reset next boundary. Cutoff state is journaled and shown on the dashboard as a progress bar.

## 4. News Blackout (owner-selected)

- **Rule:** no NEW entries within `block_before_min` (30) to `block_after_min` (30) around **high-impact** events affecting either currency of the symbol. Open positions are left untouched (set & forget).
- **Source:** scheduled calendar fetch (hourly), cached in DB; currency→symbol mapping from the configured symbol list; manual CSV upload fallback (`date,time_utc,currency,impact,title`).
- **Impact filter:** `high` only (medium optional via config).
- **Feed failure:** `news.feed_failure_mode: allow` (default, with persistent UI warning + ERROR log) or `block_all`.
- UI: upcoming events list + per-symbol blackout countdown.

## 5. Confluence ↔ Risk Mapping (strategy §10.2)

- `min_pillars = 3`: 2-pillar setups are journaled as "low-confluence — not taken", never executed.
- Optional `pillar_risk_scaling` (default OFF): 3 pillars → base risk; 4 pillars → up to `risk_per_trade_pct_max` (1.5%). Off by default to keep the model simple and auditable.
- High-confluence (3–4 pillar) trades are the only ones taken — consistent with the strategy's "high confluence = low risk" doctrine.

## 6. Kill Switch & Safety

- UI **Enable/Disable** toggle (new entries only).
- **Panic Close All** (double-confirm): market-closes all bot positions, disables bot. Manual (non-magic) positions are never touched.
- Unhandled exception in the trading loop → CRITICAL log, halt new entries, UI alert; open positions remain protected by server-side SL/TP.
- `magic_number` (default 20260922) namespaces all bot orders/positions.
- DB write failure during execution → halt trading (journal integrity is a hard requirement).

## 7. Risk Decision Journal

Every gate evaluation is persisted: `{ts, symbol, signal_id, gate: SIZING|DAILY_LOSS|NEWS|CONCURRENCY|SYMBOL_DUP|RR_CAP|RR_SLIPPAGE, result: PASS|BLOCK, details}` — queryable in the UI Journal/Logs pages and exported in backtest reports (same code path).
