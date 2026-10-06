# 04 — MT5 Integration Specification (Python ↔ MetaTrader 5)

## 1. Prerequisites
- Windows host with **MetaTrader 5 terminal installed** and logged into the target account (demo first). The `MetaTrader5` Python package communicates with the local terminal — no credentials in code by default; optional config `mt5_login/mt5_password/mt5_server/path` for headless init.
- In MT5: Tools → Options → Expert Advisors → **allow algorithmic trading**; Python 64-bit matching terminal architecture.

## 2. Connector (`mt5_connector.py`)

```python
class MT5Connector:
    def connect()          # mt5.initialize(path?, login?, password?, server?); retry 5× backoff 2^n s
    def watchdog()         # every 15s: mt5.terminal_info() ping; on failure → reconnect loop, WS status push
    def account()          # balance, equity, currency (cached ≤5s)
    def symbol_info(sym)   # digits, point, pip size, tick value/size, min/max lot, lot step, filling modes, stops level
    def rates(sym, tf, n)  # copy_rates_from_pos → DataFrame[time_utc, o,h,l,c] (UTC-converted)
    def tick(sym)          # latest bid/ask
    def positions()        # filter by magic_number
    def order_send(req)    # with filling-mode fallback (see §5)
```

- **Pip Scale Engine (formal spec §1):** `pip = 0.01` for JPY-quoted pairs, `0.0001` standard FX; the engine table also defines `0.10` (XAUUSD) and `1.00` (index CFDs) for completeness, but v1 trade scope is forex-only (owner decision). Derive from `symbol_info.digits` (digits 3/5 ⇒ pip = 10×point; digits 2/4 ⇒ pip = point). Assert on unknown layouts; never guess.
- **Threading:** ALL mt5 calls under one global `threading.Lock`; called from executor threads (see 01 §4).
- **Reconnection:** exponential backoff 1s→60s cap; while disconnected, strategy loop pauses new evaluations (data is stale); UI shows red "MT5 DISCONNECTED".

## 3. Data Feed

- Symbols: config `symbols` (default e.g. `EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, EURGBP` — owner edits).
- Timeframes: `1W, 1D, 4H, 1H` (+ optional 30M/15M execution).
- Poll loop every 5s per symbol+TF: fetch last 2 candles; a candle whose open time changed and whose previous candle is now closed → emit `CandleClosed`.
- History warm-up on start: `1W: 320 bars, 1D: 600 bars, 4H: 2200 bars, 1H: 3000 bars` (covers lookbacks incl. ATR warm-up; config-overridable).
- Handle broker weekend gaps / missing bars: never fabricate candles; log gaps.

## 4. Order Execution (`execution_engine.py`)

```python
request = {
  "action": TRADE_ACTION_DEAL, "symbol": sym, "volume": lots,
  "type": ORDER_TYPE_BUY|SELL, "price": tick.ask|bid,
  "sl": sl, "tp": tp, "deviation": cfg.max_slippage_points (default 10),
  "magic": cfg.magic_number, "comment": f"MSC|{signal_id}",
  "type_time": ORDER_TIME_GTC, "type_filling": resolved_filling,
}
```
1. Pre-trade: re-verify signal age ≤ `signal_ttl_seconds` (default 300) and price still at/near zone (≤ `entry_zone_tolerance_pips`, default 10, from AOI edge); else drop.
2. Resolve filling mode: try `SYMBOL_FILLING_MODE` preference order `FOK → IOC → RETURN` against symbol capabilities `[DECISION]`; on retcode `TRADE_RETCODE_INVALID_FILL` cycle to next.
3. Retry policy: on transient retcodes (requote, price off, timeout) → retry up to 3 times with fresh tick; on `NO_MONEY`, `MARKET_CLOSED`, `TRADE_DISABLED` → fail permanently, journal + UI alert.
4. Post-fill: read actual `deal.price`; persist trade row; verify SL/TP landed (re-fetch position); if missing → attempt `order_modify` once; else close immediately (safety rule 03 §4).
5. Idempotency: one execution per `signal_id` — DB unique constraint prevents duplicates across retries/crashes.
6. **Bracket/OCO semantics (formal spec §9):** SL and TP are attached at placement; MT5 server-side fills whichever triggers first. The bot never cancels or modifies afterwards — "set & forget" is enforced at this layer (integration test I10 audits MT5 history for zero position-modify calls).

## 5. Position Monitoring

- Every 30s: sync MT5 positions (magic-filtered) with DB: detect SL/TP closes (deal history scan since last sync), record exit price/reason (`TP|SL|MANUAL_EXTERNAL`), P/L, R multiple.
- Daily stats job at day boundary: wins/losses, P/L, R sum, per-symbol breakdown → `daily_stats` table.
- The bot **never modifies** open positions (strict set & forget per formal spec §9). If a position was closed externally (manual in MT5), mark `MANUAL_EXTERNAL` and continue.

## 6. Timezone & Server Time
- Detect broker UTC offset once per session: compare `mt5.symbol_info_tick(...).time` (server) with UTC now; store offset; convert all candle times to UTC at ingestion. Re-check hourly; log changes (DST shifts).

## 7. Failure Modes Matrix

| Failure | Detection | Response |
|---|---|---|
| Terminal closed | watchdog ping fail | reconnect loop; pause signals; UI banner |
| Market closed | retcode MARKET_CLOSED | queue-drop signal; no retry storm |
| Margin insufficient | retcode NO_MONEY | block symbol until next day; UI alert |
| SL/TP rejected | position w/o sl/tp after fill | modify once → else close position (CRITICAL log) |
| Stale feed | last candle age > 2×TF | pause symbol; UI warn |
| DB write fail | exception | retry 3×; else halt trading (journal integrity) |

## 8. Backtest reuse
Backtester does NOT use MT5. It loads historical OHLC from CSV (exported from MT5 or Dukascopy-format) into the same `Candle` stream interface. Execution is simulated with config `bt_spread_pips`, `bt_slippage_pips`, per-symbol pip values from a symbol-spec table (see 05).
