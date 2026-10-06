# 02 — Strategy Engine Specification (the core document)

**AUTHORITATIVE SOURCE:** The owner's formal algorithmic specification ("Part 1: Algorithmic Architecture & Mathematical Specifications"). This document restates it as implementable engineering requirements. Where anything else in this pack conflicts with that spec, **the formal spec wins**. All thresholds are config parameters (see `07_configuration_schema.md`); defaults = the values given in the formal spec.

---

## A. Canonical Data Types

```python
Candle       = {time_utc, open, high, low, close, symbol, timeframe}
BodyHigh(t)  = max(open_t, close_t)          # structure lives on BODIES only
BodyLow(t)   = min(open_t, close_t)
Pivot        = {index, time_utc, price, kind: HIGH|LOW, confirmed: bool}
Structure    = {state: BULLISH|BEARISH|UNDEFINED,
                active_HH, active_HL, active_LH, active_LL,
                last_break_bar: int}
TrendDir     = +1 (BULLISH) | -1 (BEARISH) | 0 (UNDEFINED)   # per TF
AOI          = {z_min, z_max, touches: int, timeframe, valid: bool}
PatternEvent = {type: BREAK_RETEST|HS_NECKLINE_BREAK|INV_HS_NECKLINE_BREAK,
                direction: BUY|SELL, level: float, bar: int}
CandleSignal = {type: BULL_ENGULF|BEAR_ENGULF|MORNING_STAR|EVENING_STAR|
                      HAMMER|SHOOTING_STAR, direction: BUY|SELL}
TradeSignal  = {symbol, direction, entry, sl, tp, pillars: int, reasons: [...]}
```

**Timeframes (config):** HTF chain = `[1W, 1D, 4H]` (one Structure state machine per TF); execution TF default `1H` (config allows 30M/15M).

---

## B. Instrument Normalization (Pip Scale Engine)

Pip size is derived dynamically from instrument precision, never hardcoded per symbol:

```
pip_size(symbol) =
    0.01    if JPY-quoted pair            (e.g., USDJPY)
    0.0001  if standard FX pair           (e.g., EURUSD)
    0.10    if XAUUSD (Gold)              (defined for engine completeness)
    1.00    if index CFD (SPX500, US30)   (defined for engine completeness)
```

Implementation: classify from `symbol_info` (currency pair quote == "JPY", or digits-based derivation: digits 3/5 → pip = 10×point, digits 2/4 → pip = point). Metals/indices branches exist in the engine table but v1 trading scope is **forex majors/minors only** (owner decision). Unknown layouts → assert + refuse to trade the symbol; never guess.

---

## C. Swing Pivot Detection (Body-Only, N-Period Window)

Structural pivots ("elbows") are detected on **candle bodies** with a symmetric N-bar window (`pivot_window_n`, **default N=5**):

```
Swing High pivot at t  iff:
    BodyHigh(t) > max(BodyHigh(t-N .. t-1))  AND  BodyHigh(t) > max(BodyHigh(t+1 .. t+N))
Swing Low pivot at t  iff:
    BodyLow(t)  < min(BodyLow(t-N .. t-1))   AND  BodyLow(t)  < min(BodyLow(t+1 .. t+N))
```

Hard requirements:
- A pivot is **confirmed only N bars after** it prints. Downstream logic consumes confirmed pivots exclusively (no lookahead — this is the backtest/live parity contract).
- The strategy's "ignore minor bumps" rule is realized by two mechanisms, both config-exposed:
  1. The N-window itself (N=5 filters 1–4 candle bumps).
  2. Optional depth filter `min_swing_atr_mult × ATR(14)` floored at `min_swing_pips` (defaults 0.5×ATR / 10 pips) — the owner-tunable knob chosen for v1. Set multiplier to 0 to run pure N-window per the formal spec.

---

## D. The Algorithmic "Snake Trick" (Retrospective Pivot Traversal)

Implements the strategy's "trace back from the head of the snake to the first major elbow" as an **extremum search over the run between structural breaks**. On a structural event at bar `t`, define the search range `K = [t_prev_break, t]` (from the bar of the previous structural break to the current bar):

- **Bullish extension (new HH):**
  `active_HL = argmin over k∈K of BodyLow(k)` — the absolute lowest body turning point of the run.
- **Bearish extension (new LL):**
  `active_LH = argmax over k∈K of BodyHigh(k)` — the absolute highest body turning point of the run.

The traced pivot MUST coincide with a confirmed N-window pivot of the correct kind; if the argmin/argmax bar is not a confirmed pivot (edge case at range ends), take the nearest confirmed pivot within K; if none exists, keep the previous boundary and log `SNAKE_NO_PIVOT`.

---

## E. Structure State Machine (per symbol, per TF)

State: `BULLISH | BEARISH | UNDEFINED` (UNDEFINED until the first two confirmed pivots + first break establish a structure). Boundaries are body prices. **All break tests use candle CLOSE only** — wick pierces are liquidity sweeps: logged, never structural.

```
BULLISH EXTENSION (in state BULLISH):
    if close(t) > active_HH:
        active_HH ← BodyHigh(t)
        active_HL ← SnakeTraceLow(t)          # §D argmin BodyLow over K
        last_break_bar ← t

BEARISH SHIFT (BOS/CHoCH — from BULLISH):
    if close(t) < active_HL:
        state ← BEARISH
        active_LH ← SnakeTraceHigh(t)         # argmax BodyHigh over K
        active_LL ← BodyLow(t)
        last_break_bar ← t
        emit StructureEvent(BOS_BEARISH)

BEARISH EXTENSION (in state BEARISH):
    if close(t) < active_LL:
        active_LL ← BodyLow(t)
        active_LH ← SnakeTraceHigh(t)
        last_break_bar ← t

BULLISH SHIFT (BOS/CHoCH — from BEARISH):
    if close(t) > active_LH:
        state ← BULLISH
        active_HL ← SnakeTraceLow(t)
        active_HH ← BodyHigh(t)
        last_break_bar ← t
        emit StructureEvent(BOS_BULLISH)
```

- Internal oscillation strictly between active boundaries never changes state.
- `Traceback_Lowest_Body(t_prev_break, t)` / `Traceback_Highest_Body(...)` in the formal spec = the §D functions.
- ATR(14) with Wilder smoothing per TF (used only by the optional §C depth filter and AOI merge tolerance).

---

## F. Multi-Timeframe Alignment Matrix (Pillar 1)

Let `T_1W, T_1D, T_4H ∈ {+1, -1, 0}` be the structure states of the three HTFs (0 = UNDEFINED).

```
Long_Allowed  = (T_1W = +1 AND T_1D = +1) OR (T_1D = +1 AND T_4H = +1)
Short_Allowed = (T_1W = -1 AND T_1D = -1) OR (T_1D = -1 AND T_4H = -1)
```

If neither holds → system state `NEUTRAL_FILTER`: no new setups are evaluated; logged as a skip. A TF in UNDEFINED state counts as 0 and therefore cannot satisfy either clause.

---

## G. AOI Clustering Engine (Pillar 2)

### G.1 Construction
Input: all confirmed pivot body-prices `P = {p1..pm}` within the TF lookback limit (**1W ≤ 6y, 1D ≤ 2y, 4H ≤ 12m** per strategy §5.1; config `lookback`).

1. Sweep a vertical window of width `W` (config-bounded `5×pip ≤ W ≤ 60×pip`, default sweep step 1 pip) over the price range covered by P.
2. A zone `Z = [z_min, z_max]` is formed at the window maximizing intersecting pivot count:
   `Touches(Z) = Σ_k 𝟙(p_k ∈ [z_min, z_max])` — mixed support/resistance pivot kinds both count (role flips valid per strategy §6.1).
3. Overlapping candidate zones merge if `|z_center_i − z_center_j| ≤ merge_tolerance` (default `max(0.25×ATR, 10 pips)`).

### G.2 Validity filter (exact)
```
IsValid(Z) = Touches(Z) ≥ 3  AND  5×pip_size ≤ (z_max − z_min) ≤ 60×pip_size
```
Zones narrower than 5 pips after clustering are padded symmetrically to 5 pips before the touch recount; zones that cannot satisfy the width cap after discarding outermost pivots are dropped.

### G.3 Boundary confinement rule (exact)
- Long context: keep zones with `active_HL < z_min AND z_max < active_HH`.
- Short context: keep zones with `active_LL < z_min AND z_max < active_LH`.
- Anything outside the active structural range is invalid for the current setup (reaching it would break structure first).
- Zones are built per TF; pillar 2 requires a valid zone on a TF in `aoi.zone_timeframes` (default `["1W","1D"]`).

### G.4 Cardinal execution rule
BUY only at support-side interaction (price above/inside zone reacting up); SELL only at resistance-side interaction. Never buy into resistance / sell into support.

---

## H. Structural Patterns (Pillar 3) — execution TF state machines

### H.1 Break & Retest (continuation)
Per symbol, per candidate zone Z, in the trend direction:

- **Bullish:**
  1. ARMED at bar `t−k` when `close(t−k) > z_max` (confirmed body breakout above AOI).
  2. RETEST confirmed at bar `t` when `low(t) ≤ z_max` AND `BodyLow(t) ≥ z_min − retest_tolerance` (pullback tags the broken boundary, body does not fall back through the zone).
- **Bearish (mirror):**
  1. ARMED when `close(t−k) < z_min`.
  2. RETEST when `high(t) ≥ z_min` AND `BodyHigh(t) ≤ z_max + retest_tolerance`.

Constraints: retest must occur within `retest_window_bars` (default 15); a body close back through the far side of the zone cancels the setup; emits `BREAK_RETEST` PatternEvent valid for `pattern_expiry_bars` (default 20) awaiting pillar 4. `retest_tolerance` default 5 pips.

### H.2 Head & Shoulders / Inverted H&S (reversal) — two-trough neckline
Match the 5-pivot confirmed-swing sequence:

```
Bearish H&S:
  S_L: peak (t1, P1)
  T_1: trough (t2, V1)                       ← establishes neckline level
  H:   peak  (t3, P2)  with P2 > P1
  T_2: trough (t4, V2)  with |V2 − V1| ≤ neckline_threshold   ← horizontal alignment
  S_R: peak  (t5, P3)  with V1 < P3 < P2
  Neckline = min(V1, V2)                     ← HORIZONTAL, on bodies, never diagonal
  CONFIRMATION TRIGGER: close(t) < min(V1, V2)     (body close below neckline)
  VALID ENTRY: retest of the neckline from below + bearish candle trigger (§I)
```

- `neckline_threshold` default `0.6 × |P2 − min(V1,V2)|` scaled, config `head_shoulders.neckline_threshold_atr_mult` (default 0.6×ATR(14) of execution TF — chosen to make it scale-invariant; tune with owner).
- Inverted H&S is the exact mirror (neckline = max(V1, V2); confirmation = close above; entry on retest from above with bullish trigger).
- **Hard guard (strategy's "high-risk error"):** no trade may be generated from the right-shoulder region before the neckline body-close confirmation. Enforce in code and in tests.

---

## I. Candlestick Trigger Formations (Pillar 4) — exact formulas

Evaluated on **closed** execution-TF candles, only when price is interacting with a valid AOI (candle range intersects `[z_min, z_max]`). All five are full trade triggers per the formal spec §7 (the strategy prose's "doji/hammer as precondition" is superseded — hammer and shooting star are listed there as confirmation signals).

### I.1 Bullish triggers
- **Bullish Engulfing:**
  `close(t) > open(t)` AND `close(t) > max(open(t−1), close(t−1), open(t−2), close(t−2))` AND `open(t) ≤ min(open(t−1), close(t−1))` — body engulfs the bodies of BOTH prior candles (lookback 2, config `engulf_lookback`).
- **Morning Star:**
  1. `close(t−2) < open(t−2)` (bearish impulse)
  2. `|close(t−1) − open(t−1)| ≤ 0.35 × (high(t−1) − low(t−1))` (indecision: doji/hammer/small body)
  3. `close(t) > open(t)` AND `close(t) ≥ (open(t−2) + close(t−2)) / 2` (recovers past midpoint of impulse candle)
- **Hammer Rejection:**
  `(min(open,close) − low) / (high − low) ≥ 0.60` AND `|close − open| / (high − low) ≤ 0.25`

### I.2 Bearish triggers (mirrors)
- **Bearish Engulfing:** `close(t) < open(t)` AND `close(t) < min(o(t−1), c(t−1), o(t−2), c(t−2))` AND `open(t) ≥ max(o(t−1), c(t−1))`
- **Evening Star:** bullish impulse at t−2; indecision at t−1 (same 0.35 rule); `close(t) < open(t)` AND `close(t) ≤ (open(t−2) + close(t−2))/2`
- **Shooting Star Rejection:** `(high − max(open,close)) / (high − low) ≥ 0.60` AND `|close − open| / (high − low) ≤ 0.25`

Division-by-zero guard: if `high − low = 0`, the candle matches nothing. Discard-list patterns (Piercing, Dark Cloud, 3 Soldiers/Crows) are NOT implemented — by design.

---

## J. 50 EMA Filter (bonus confluence)

`EMA50(t) = α·close(t) + (1−α)·EMA50(t−1)`, `α = 2/51`.

- Long extra confluence: `close(t) > EMA50(t)`; Short: `close(t) < EMA50(t)`.
- Displayed in UI and journal; does NOT change the pillar count by default (`ema_counts_as_confluence: false`) — per the strategy's "car hood / cherry on top" rule. Never a standalone trade reason.

---

## K. Confluence Decision Rule

| Pillar | Pass condition |
|---|---|
| 1. Trend Alignment | §F holds in the trade direction |
| 2. Valid AOI | Price interacting with a valid, boundary-confined 1W/1D AOI (§G); correct side (§G.4) |
| 3. Structural Pattern | Unexpired `PatternEvent` (§H) in trade direction |
| 4. Candle Trigger | `CandleSignal` (§I) on the just-closed candle, direction-matched |

Fire `TradeSignal` iff `pillar_count ≥ min_pillars` (default 3). Every evaluation at an AOI is journaled with per-pillar pass/fail, including rejected 2-pillar setups ("low-confluence — not taken").

---

## L. Trade Parameter Construction (on TradeSignal) — "Set and Forget"

- **Entry:** market order at the close of the trigger candle (fill on next tick; see 04 §4).
- **Stop Loss** (`sl_buffer_pips`, default 15, allowed band 10–20):
  - Long:  `SL = low(entry trigger candle) − 15 × pip_size`
  - Short: `SL = high(entry trigger candle) + 15 × pip_size`
  - (The trigger-candle wick is the strategy's "invalidation wick". For 3-candle star formations use the formation's extreme wick — min low / max high across candles t−2..t.)
- **Take Profit:** `TP = entry ± R × |entry − SL|` with `R ∈ [2.0, 4.0]` (config `tp_r_multiple`, default 2.0), **capped at the macro structural boundary** — longs: `min(entry + R·risk, active_HH)`; shorts: `max(entry − R·risk, active_LL)`.
  - Validity gate: if the macro boundary caps TP such that realized R:R < `min_rr` (default 2.0), **reject the signal** (`RR_CAP_REJECT`).
- **Order type:** bracket/OCO semantics — SL and TP attached at placement; whichever fills first cancels the other. **No trailing stops, no breakeven moves, no discretionary early exits, no manual intervention** (config flags exist for v2, default off).
- **Position sizing (generic):** `Units = (account_balance × risk_pct) / |entry − SL|`; MT5 lot conversion via tick value/size in 03 §1.

---

## M. Evaluation Pseudocode

```python
def on_candle_closed(symbol, tf, candle):
    structure[symbol][tf].update(candle)         # §C pivots → §E state machine → §D snake trace
    if tf in AOI_TFS: aoi_engine[symbol][tf].rebuild_if_needed()   # §G
    if tf != EXEC_TF: return

    direction_ok = alignment_matrix(symbol)      # §F
    if direction_ok == NEUTRAL_FILTER: return log_skip("no trend sync")

    zone = aoi_engine.interacting_zone(symbol, candle)             # §G incl. confinement
    if not zone: return log_skip("no valid AOI interaction")

    patterns.update(symbol, structure, zone)     # §H B&R + H&S state machines
    csig = candle_engine.classify(last_3_candles)# §I exact formulas
    pillars = score_pillars(symbol, zone, patterns, csig)          # §K
    log_checklist(symbol, pillars, csig)         # journaled pass OR fail

    if pillars.count < cfg.min_pillars or csig is None: return
    ts = build_trade_signal(symbol, csig, patterns, zone)          # §L (SL/TP/RR gates)
    risk_manager.evaluate(ts)                                      # → 03
```

## N. Backtest Parity Contract
The backtester MUST invoke these exact functions on identical closed-candle inputs. Pivot confirmation lag (N bars), snake-trace ranges, lookbacks, and all thresholds are identical in both modes. Any code path branching on `is_backtest` inside the strategy engines is a defect (execution/data-source layers may branch; strategy layers may not).
