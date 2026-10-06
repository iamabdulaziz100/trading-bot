# 08 — Testing & Acceptance Plan

Tests assert the **formal algorithmic spec's exact formulas and thresholds** — not approximations. Build fixtures before any MT5 code (M2 order).

## 1. Unit Tests (pytest) — Golden Datasets

Hand-crafted synthetic OHLC sequences (JSON fixtures) where the expected answer is known by construction.

| Test suite | Fixture asserts |
|---|---|
| `test_pip_engine` | USDJPY → 0.01; EURUSD → 0.0001; XAUUSD → 0.10; US30 → 1.00; unknown layout → assert/refuse |
| `test_swings` | Body-based pivots with N=5: pivot detected on `max(open,close)` / `min(open,close)`, NOT on wicks (fixture where wick exceeds but body doesn't → NO pivot); confirmation exactly N bars later (no early confirm); bump filter on/off behavior |
| `test_snake_trick` | After HH break: `active_HL = argmin BodyLow over [prev_break, t]` — fixture with two candidate lows picks the absolute lowest; after LL break: `active_LH = argmax BodyHigh`; nearest-confirmed-pivot fallback logged |
| `test_structure` | HH/HL → BULLISH; `close < active_HL` → BEARISH + BOS event; **wick below HL with body close above → NO shift** (liquidity sweep); extension updates use `BodyHigh(t)`/`BodyLow(t)`; UNDEFINED → no alignment |
| `test_alignment` | Full 27-combo matrix of (T1W, T1D, T4H) ∈ {+1,0,−1}³ → Long/Short/NEUTRAL_FILTER exactly per §F formula |
| `test_aoi` | 2-touch invalid / 3-touch valid (incl. S/R flip mix); width 4 pips → padded to 5; 61 pips → invalid; boundary confinement: zone with `z_max ≥ active_HH` rejected (long), mirror for short |
| `test_candles` | Exact-ratio boundary tests: star middle body = 0.35×range (pass) vs 0.36 (fail); hammer wick 0.60/body 0.25 boundary; engulfing must cover BOTH prior bodies (fails if it only covers t−1); star 3rd candle must close past impulse midpoint; `high==low` candle matches nothing |
| `test_patterns_br` | `close > z_max` arms; retest `low ≤ z_max` with `BodyLow ≥ z_min − tol` confirms; body close through far side cancels; expiry after N bars |
| `test_patterns_hs` | 5-pivot H&S with `|V2−V1| ≤ threshold` → armed; neckline = `min(V1,V2)`; **right-shoulder touch before neckline body-break → NO event** (strategy's "high-risk error"); close below neckline → confirmed; entry only on neckline retest + trigger; inverted mirror; `V2` misaligned beyond threshold → no pattern |
| `test_confluence` | All 16 pillar combinations → only ≥3 emit TradeSignal; NEUTRAL_FILTER blocks; EMA never counted as 5th pillar by default |
| `test_trade_params` | SL = trigger wick ∓ 15 pips (star: formation extreme wick); TP = R×risk capped at `active_HH`/`active_LL`; cap below min_rr → `RR_CAP_REJECT` |
| `test_sizing` | Units formula on EURUSD & USDJPY with known tick values; floor-to-lot-step; under-min-lot skip |
| `test_risk_gates` | Daily-loss cutoff at exact boundary; news window blocks correct symbols only; concurrency cap; RR slippage abort |

## 2. Integration Tests (demo MT5)
| # | Scenario | Pass criteria |
|---|---|---|
| I1 | Cold start | Connects, warms history, UI green ≤60s |
| I2 | Signal→order | Valid setup → bracket order (SL+TP attached) placed, journaled, WS pushed |
| I3 | Retry path | Simulated requote → ≤3 retries, zero duplicates (signal_id idempotency) |
| I4 | SL/TP attach failure | Modify-once → else immediate close; naked position impossible |
| I5 | Disconnect | Kill terminal → red banner, reconnect resumes, no duplicate trades |
| I6 | External close | Manual close in MT5 → `MANUAL_EXTERNAL`, no interference |
| I7 | Daily cutoff | Forced −3% → entries blocked, banner, auto-reset next day |
| I8 | News blackout | Test event → symbol blocked in window, released after |
| I9 | Panic button | Closes bot positions only, disables bot |
| I10 | Set & forget audit | 48h soak: zero position-modify calls in MT5 history for bot trades |

## 3. Backtest Acceptance
| # | Check | Criteria |
|---|---|---|
| B1 | No-lookahead audit | Pivot N-bar confirmation lag active in replay; HTF candles revealed only after close; assertion tests + code review |
| B2 | Parity (M5) | Backtest vs live demo, same 2+ week window: ≥90% trade match (direction, timing ±1 candle, SL/TP ±2 pips) |
| B3 | Metrics sanity | Hand-verified 5-trade dataset → metrics match manual math exactly |
| B4 | Pessimism rule | Candle spanning both SL and TP → SL first |
| B5 | Spec fidelity | No `is_backtest` branches inside strategy engines (grep-level CI check) |

## 4. Definition of Done (demo phase)
1. All unit + integration + backtest tests green.
2. **2–4 week unattended demo run:** no crash-loop, zero naked positions, journal complete, cutoff + blackout each exercised (real or forced).
3. UI full suite functional; settings round-trip; logs searchable.
4. README: install/run steps + config reference generated from schema.

## 5. Go-Live Checklist (owner decision after demo)
- [ ] Demo results reviewed (win rate, drawdown, parity report)
- [ ] `risk_per_trade_pct` re-confirmed for live capital
- [ ] News feed verified live ≥1 week
- [ ] Panic/disable rehearsed on demo
- [ ] VPS reboot-recovery tested (auto-start + state reconcile)
- [ ] Owner acknowledges: probabilistic edge, no profit guarantee

## 6. Documented Residual Risks
- N-window + optional ATR depth filter approximate the discretionary "major elbow" judgment — expect iterative tuning of `pivot_window_n` / `min_swing_*` during demo.
- Two-trough neckline tolerance (`neckline_threshold_atr_mult`) is the one H&S parameter the formal spec leaves as "Threshold" — owner tunes from demo observations.
- Historical news calendar not bundled → backtests ignore the news filter (noted in every report).
