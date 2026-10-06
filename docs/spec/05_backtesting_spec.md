# 05 — Backtesting Module Specification

## 1. Goal & Hard Requirement
Replay historical candles through the **exact same strategy pipeline** (`structure_engine`, `pattern_engine`, `candle_engine`, `confluence_engine`, `risk_manager` sizing math) as live trading. **No duplicated strategy logic.** The only replaced components are the data source (CSV instead of MT5) and the executor (simulated fills instead of MT5 orders).

## 2. Data Input
- CSV per symbol+timeframe: `time_utc,open,high,low,close[,volume]` (UI upload or `data/history/` folder; MT5 export compatible).
- Multi-timeframe alignment: the replay clock steps through execution-TF candles; HTF (4H/1D/1W) candles are only "revealed" when fully closed as of current replay time — **this enforces no-lookahead**.
- Warm-up: first N bars (per TF lookback config) feed indicators but produce no signals.
- Symbol spec table for backtests (pip size, pip value per lot per symbol, optional commission per lot): `config/bt_symbol_specs.yaml`, UI-editable.

## 3. Execution Simulation (config-tunable, conservative defaults `[DECISION]`)
| Parameter | Default |
|---|---|
| Spread | per-symbol fixed, e.g. majors 1.0 pip (JPY 1.0), applied: BUY at ask = close+spread/2 … simpler: BUY fills at close+spread, SELL at close |
| Slippage | `bt_slippage_pips` = 0.5 pip adverse on entries and SL exits |
| Commission | `bt_commission_per_lot` = $7/lot round-turn (0 default) |
| Entry fill | market at next candle open (signals fire on close) |
| SL/TP check | per candle after entry using high/low; if BOTH SL and TP inside same candle → assume **SL hit first** (pessimistic) |
| Gap rule | if candle opens beyond SL/TP → fill at open price (gap slippage) |

## 4. Risk Rules in Backtest
- Same `% balance` sizing against simulated equity curve (start `bt_initial_balance`, default $10,000).
- Daily-loss cutoff active in backtest (same code path).
- News blackout: **skipped by default in backtest** (`bt_apply_news_filter: false`) unless a news CSV is provided — documented limitation (historical calendar data not bundled).
- Margin modeling: simplified — track used margin approx `lots × contract_size × price / leverage` (`bt_leverage` default 100); skip trade if insufficient.

## 5. Output Metrics (`BacktestResult`)
- Totals: trades, win rate, net P/L ($ and %), profit factor, expectancy (R), max drawdown (%/$ and duration), Sharpe (daily), avg R:R realized, pillars distribution of taken trades.
- Per-symbol and per-month breakdown tables.
- **Full trade list:** entry/exit time+price, SL/TP, lots, pillars checked, pattern type, candle signal, exit reason, R multiple — exportable CSV.
- Equity curve series (per closed trade) for UI chart.
- Rejected-signal log (why each checklist failed) — same verbosity as live journal.

## 6. Validation Mode (M5 exit criteria)
"Live-vs-backtest parity test": run backtester over a date range that the live demo bot traded; trades must match within tolerance (±1 candle timing, same direction, SL/TP within 2 pips) for ≥90% of trades. Discrepancies are bugs in parity, reported in UI.

## 7. Performance
- Pure NumPy/Pandas vectorization where possible; target: 2 years × 6 symbols × 4 TFs in < 60s on a typical laptop.
- Runs in a background worker; UI polls status; results stored in DB (`backtest_runs`) with config snapshot hash for reproducibility.

## 8. UI Surface (see 06 for endpoints)
Backtests page: pick symbols, date range, parameters (defaults from config), run → progress bar → results dashboard (equity curve, metrics cards, trade table, downloadable CSV).
