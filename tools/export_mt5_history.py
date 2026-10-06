"""Export MetaTrader 5 history to ``data/history/{SYMBOL}_{TF}.csv`` (UTC, canonical format)
for the backtester. Windows only — needs the MT5 terminal running and logged in.

    python tools/export_mt5_history.py                       # config symbols, all TFs, ~6 years
    python tools/export_mt5_history.py --symbols EURUSD,USDJPY --years 3
    python tools/export_mt5_history.py --offset-hours 2      # if the broker offset cannot be detected

The broker UTC offset is detected from live ticks (market must be open); on weekends pass
``--offset-hours`` (e.g. 2 in winter / 3 in summer for most GMT+2/+3 brokers).
Note: MT5 only returns as many bars as "Tools → Options → Charts → Max bars in chart" allows.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app.backtest.data_loader import history_path, write_canonical  # noqa: E402
from app.config import ConfigManager  # noqa: E402
from app.mt5.connector import MT5Connector  # noqa: E402

BARS_PER_YEAR = {"1W": 53, "1D": 262, "4H": 1560, "1H": 6240, "30M": 12480, "15M": 24960}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="", help="comma-separated (default: config.yaml symbols)")
    ap.add_argument("--tfs", default="1W,1D,4H,1H", help="timeframes to export")
    ap.add_argument("--years", type=float, default=6.0, help="years of history per timeframe")
    ap.add_argument("--offset-hours", type=float, default=None, help="broker server UTC offset override")
    args = ap.parse_args()

    cm = ConfigManager()
    cfg = cm.config
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or cfg.symbols
    conn = MT5Connector(cfg.mt5)
    if not conn.available:
        print("MetaTrader5 package not available — run this on Windows with the MT5 terminal installed.")
        return 1
    if not conn.connect():
        print(f"Could not connect to MT5: {conn.message}")
        return 1
    if args.offset_hours is not None:
        conn.server_offset = int(args.offset_hours * 3600)
    elif conn.detect_server_offset(symbols) is None:
        print("Broker UTC offset could not be detected (market closed?). Re-run with --offset-hours N.")
        return 1
    print(f"broker server offset: {conn.server_offset / 3600:+.2f}h")
    out_dir = ROOT / cfg.server.data_dir / "history"
    for sym in symbols:
        conn.symbol_spec(sym)  # selects the symbol, validates the pip layout
        for tf in [t.strip().upper() for t in args.tfs.split(",") if t.strip()]:
            count = int(BARS_PER_YEAR[tf] * args.years) + 1
            bars = conn.rates(sym, tf, count)[:-1]  # drop the still-forming bar
            df = pd.DataFrame([{"time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close,
                                "volume": c.volume} for c in bars])
            if df.empty:
                print(f"{sym} {tf}: no data")
                continue
            path = history_path(out_dir, sym, tf)
            write_canonical(df, path)
            first = pd.to_datetime(df["time"].iloc[0], unit="s", utc=True)
            print(f"{sym} {tf}: {len(df)} bars from {first:%Y-%m-%d} → {path.relative_to(ROOT)}")
    conn.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
