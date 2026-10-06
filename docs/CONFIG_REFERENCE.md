# Configuration reference

Generated from the pydantic schema in `app/config.py` by `python tools/gen_config_reference.py` — do not edit by hand. Spec: `docs/spec/07_configuration_schema.md`.

Legend: **R** = changing it requires *Rebuild structure* (UI button / `POST /api/structure/rebuild`), **S** = requires a restart of the bot. Everything else hot-reloads.

## MT5 connection (`mt5`)

| Key | Default | Allowed | Description | Spec | |
|---|---|---|---|---|---|
| `mt5.path` | `""` |  | Optional terminal64.exe path | 04 §1 | S |
| `mt5.login` | `0` | 0 …  | Account login (0 = use the already logged-in terminal) | 04 §1 | S |
| `mt5.password` | `""` |  | Account password (prefer MSC_MT5_PASSWORD in .env) | 04 §1 | S |
| `mt5.server` | `""` |  | Broker server name | 04 §1 | S |
| `mt5.magic_number` | `20260922` | 1 …  | Magic number namespacing all bot orders/positions | 03 §6 | S |
| `mt5.max_slippage_points` | `10` | 0 … 1000 | Max deviation in points for market orders | 04 §4 | S |

## Universe (`universe`)

| Key | Default | Allowed | Description | Spec | |
|---|---|---|---|---|---|
| `symbols` | `["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURGBP"]` |  | Traded symbols (forex majors/minors only) | 00 §2 | R |
| `timeframes.htf` | `["1W", "1D", "4H"]` | 1W / 1D / 4H / 1H / 30M / 15M | Trend state machine chain (pillar 1) | 02 §F | R |
| `timeframes.execution` | `"1H"` | 1H / 30M / 15M | Execution timeframe | 02 §A | R |

## Structure engine (`structure`)

| Key | Default | Allowed | Description | Spec | |
|---|---|---|---|---|---|
| `structure.body_based_swings` | `true` |  | Pivots on candle bodies (DO NOT disable for spec fidelity) | 02 §C | R |
| `structure.pivot_window_n` | `5` | 2 … 20 | Symmetric N-bar pivot window | 02 §C | R |
| `structure.pivot_tie_rule` | `"first_of_equal"` | first_of_equal / strict | Equal body extremes on the right side: first_of_equal keeps the first bar of a plateau as the pivot; strict = formula as written (no pivot on ties) | 02 §C [DECISION] | R |
| `structure.min_swing_atr_mult` | `0.5` | 0 … 10 | Bump filter ATR multiplier (0 = pure N-window) | 02 §C | R |
| `structure.min_swing_pips` | `10.0` | 0 …  | Absolute floor for the bump filter (pips) | 02 §C | R |
| `structure.atr_period` | `14` | 2 … 200 | ATR period (Wilder) | 02 §E | R |
| `structure.lookback` | `{"1W": {"years": 6.0, "months": 0.0, "days": 0.0}, "1D": {"years": 2.0, "months": 0.0, "days": 0.0}, "4H": {"years": 0.0, "months": 12.0, "days": 0.0}}` |  | AOI pivot lookback per TF | 02 §G.1 | R |

## AOI clustering (`aoi`)

| Key | Default | Allowed | Description | Spec | |
|---|---|---|---|---|---|
| `aoi.min_touches` | `3` | 1 … 20 | Touches(Z) ≥ min_touches | 02 §G.2 | R |
| `aoi.min_width_pips` | `5.0` | 0 …  | Minimum zone width (pips) | 02 §G.2 | R |
| `aoi.max_width_pips` | `60.0` | 0 …  | Maximum zone width (pips) | 02 §G.2 | R |
| `aoi.sweep_step_pips` | `1.0` | 0 …  | Vertical window sweep granularity (pips) | 02 §G.1 | R |
| `aoi.merge_tolerance_atr_mult` | `0.25` | 0 …  | Merge tolerance ATR multiplier | 02 §G.1 | R |
| `aoi.merge_tolerance_min_pips` | `10.0` | 0 …  | Merge tolerance floor (pips) | 02 §G.1 | R |
| `aoi.zone_timeframes` | `["1W", "1D"]` | 1W / 1D / 4H | Pillar-2 qualifying TFs | 02 §G.3 | R |
| `aoi.allow_4h_zones` | `false` |  | Also build/qualify 4H zones | 02 §G.3 | R |
| `aoi.boundary_confinement` | `true` |  | Zones strictly inside the active structural range | 02 §G.3 | R |

## Structural patterns (`patterns`)

| Key | Default | Allowed | Description | Spec | |
|---|---|---|---|---|---|
| `patterns.break_retest.retest_window_bars` | `15` | 1 … 500 | Retest must occur within N bars of the breakout | 02 §H.1 | R |
| `patterns.break_retest.retest_tolerance_pips` | `5.0` | 0 …  | Body tolerance beyond the zone during retest | 02 §H.1 | R |
| `patterns.break_retest.expiry_bars` | `20` | 1 … 500 | PatternEvent validity (bars) awaiting pillar 4 | 02 §H.1 | R |
| `patterns.head_shoulders.neckline_threshold_atr_mult` | `0.6` | 0 … 20 | |V2−V1| ≤ mult × ATR(14) neckline alignment | 02 §H.2 | R |
| `patterns.head_shoulders.expiry_bars` | `20` | 1 … 500 | PatternEvent validity (bars) | 02 §H.2 | R |

## Candlestick triggers (`candles`)

| Key | Default | Allowed | Description | Spec | |
|---|---|---|---|---|---|
| `candles.star_indecision_body_ratio` | `0.35` | 0 … 1 | Star middle candle: |C−O| ≤ ratio × (H−L) | 02 §I.1 | R |
| `candles.hammer_wick_ratio` | `0.6` | 0 … 1 | Rejection wick ≥ ratio × range | 02 §I.1 | R |
| `candles.hammer_body_ratio` | `0.25` | 0 … 1 | Body ≤ ratio × range | 02 §I.1 | R |
| `candles.engulf_lookback` | `2` | 1 … 5 | Engulf the bodies of the previous N candles | 02 §I.1 | R |
| `candles.triggers` | `["BULL_ENGULF", "BEAR_ENGULF", "MORNING_STAR", "EVENING_STAR", "HAMMER", "SHOOTING_STAR"]` | BULL_ENGULF / BEAR_ENGULF / MORNING_STAR / EVENING_STAR / HAMMER / SHOOTING_STAR | Enabled trigger formations | 02 §I | R |

## Confluence (`confluence`)

| Key | Default | Allowed | Description | Spec | |
|---|---|---|---|---|---|
| `confluence.min_pillars` | `3` | 1 … 4 | Pillars required to fire a TradeSignal | 02 §K |  |
| `confluence.ema_period` | `50` | 2 … 500 | EMA period for the bonus filter (α = 2/(n+1)) | 02 §J | R |
| `confluence.ema_counts_as_confluence` | `false` |  | Count EMA as an extra pillar (spec: no) | 02 §J |  |

## Trade parameters (`trade`)

| Key | Default | Allowed | Description | Spec | |
|---|---|---|---|---|---|
| `trade.sl_buffer_pips` | `15.0` | 5 … 30; warn outside 10–20 | SL buffer beyond the trigger wick (pips) | 02 §L |  |
| `trade.tp_r_multiple` | `2.0` | 2.0 … 4.0 | TP = entry ± R × risk | 02 §L |  |
| `trade.tp_cap_at_structure` | `true` |  | Cap TP at active_HH (long) / active_LL (short) | 02 §L |  |
| `trade.min_rr` | `2.0` | 0 … 10 | Reject if the structure cap forces R:R below this | 02 §L |  |
| `trade.rr_slippage_tolerance` | `0.2` | 0 … 5 | Abort if fill R:R < min_rr − tolerance | 03 §2 |  |
| `trade.signal_ttl_seconds` | `300` | 1 … 86400 | Max signal age before execution | 04 §4 |  |
| `trade.entry_zone_tolerance_pips` | `10.0` | 0 …  | Max distance of price from the AOI edge at execution | 04 §4 |  |
| `trade.one_trade_per_symbol` | `true` |  | No stacking on a symbol | 03 §3 |  |
| `trade.breakeven_enabled` | `false` |  | v2 — not implemented (strict set & forget) | 02 §L |  |
| `trade.trailing_enabled` | `false` |  | v2 — not implemented (strict set & forget) | 02 §L |  |
| `trade.partial_close_enabled` | `false` |  | v2 — not implemented (strict set & forget) | 02 §L |  |

## Risk management (`risk`)

| Key | Default | Allowed | Description | Spec | |
|---|---|---|---|---|---|
| `risk.risk_per_trade_pct` | `1.0` | 0 … 5; warn outside –2 | Risk per trade (% of balance) | 03 §1 |  |
| `risk.risk_per_trade_pct_max` | `1.5` | 0 … 5; warn outside –2 | 4-pillar risk when pillar_risk_scaling is on | 03 §5 |  |
| `risk.pillar_risk_scaling` | `false` |  | Scale risk with pillar count | 03 §5 |  |
| `risk.max_daily_loss_pct` | `3.0` | 0 … 100 | Halt new entries when day P/L ≤ −pct of day-start balance | 03 §3 |  |
| `risk.max_concurrent_trades` | `3` | 1 … 100 | Global cap on open bot positions | 03 §3 |  |
| `risk.max_trades_per_day` | `0` | 0 … 1000 | 0 = disabled | 03 §3 |  |
| `risk.day_boundary` | `"server"` | server / utc | Day boundary for daily stats/cutoff | 01 §7 |  |

## News blackout (`news`)

| Key | Default | Allowed | Description | Spec | |
|---|---|---|---|---|---|
| `news.enabled` | `true` |  | News blackout filter | 03 §4 |  |
| `news.feed_url` | `""` |  | Calendar JSON feed (empty = built-in default) | 01 §6 |  |
| `news.poll_minutes` | `60` | 5 … 1440 | Feed poll interval (minutes) | 03 §4 |  |
| `news.impact_levels` | `["high"]` | high / medium / low | Impact levels that block | 03 §4 |  |
| `news.block_before_min` | `30` | 0 … 1440 | Block new entries N minutes before the event | 03 §4 |  |
| `news.block_after_min` | `30` | 0 … 1440 | Block new entries N minutes after the event | 03 §4 |  |
| `news.feed_failure_mode` | `"allow"` | allow / block_all | Behaviour while the feed is down | 01 §6 |  |

## Backtesting (`backtest`)

| Key | Default | Allowed | Description | Spec | |
|---|---|---|---|---|---|
| `backtest.initial_balance` | `10000.0` | 0 …  | Starting balance (USD) | 05 §4 |  |
| `backtest.leverage` | `100.0` | 1 … 5000 | Leverage for the simplified margin model | 05 §4 |  |
| `backtest.spread_pips_default` | `1.0` | 0 …  | Spread when a symbol has no spec entry | 05 §3 |  |
| `backtest.slippage_pips` | `0.5` | 0 …  | Adverse slippage on entries and SL exits | 05 §3 |  |
| `backtest.commission_per_lot` | `0.0` | 0 …  | Round-turn commission per lot (USD) | 05 §3 |  |
| `backtest.apply_news_filter` | `false` |  | Apply news blackout using the stored calendar | 05 §4 |  |
| `backtest.symbol_specs_file` | `"config/bt_symbol_specs.yaml"` |  | Backtest symbol spec table | 05 §2 |  |

## Server / UI (`server`)

| Key | Default | Allowed | Description | Spec | |
|---|---|---|---|---|---|
| `server.host` | `"127.0.0.1"` |  | Bind address (keep 127.0.0.1) | 06 | S |
| `server.port` | `8000` | 1 … 65535 | HTTP port | 06 | S |
| `server.api_token` | `""` |  | Required only if host != 127.0.0.1 | 06 | S |
| `server.log_level` | `"INFO"` | DEBUG / INFO / WARNING / ERROR | Log level | 01 §5 | S |
| `server.data_dir` | `"data"` |  | SQLite / uploads directory | 01 §5 | S |
