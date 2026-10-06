"""Deterministic synthetic market data (regime-switching random walk) for pipeline/backtest tests."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.backtest.data_loader import df_to_candles, resample


def hourly_df(days: int = 400, start_px: float = 1.10, seed: int = 7, start: str = "2023-01-02") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = 24 * days
    drift = np.repeat(rng.choice([-1, 1], size=n // 600 + 1) * 0.00005, 600)[:n]
    close = start_px * np.exp(np.cumsum(drift + rng.normal(0, 0.0011, n)))
    open_ = np.r_[close[0], close[:-1]] + rng.normal(0, 0.00004, n) * start_px
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.0005, n)) * start_px
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0005, n)) * start_px
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp())
    df = pd.DataFrame({"time": t0 + np.arange(n, dtype="int64") * 3600, "open": open_.round(5),
                       "high": high.round(5), "low": low.round(5), "close": close.round(5), "volume": 0.0})
    wd = ((df["time"] // 86400) + 3) % 7  # Mon=0
    return df[wd < 5].reset_index(drop=True)


def multi_tf(df_1h: pd.DataFrame) -> dict:
    return {"1H": df_to_candles(df_1h), "4H": df_to_candles(resample(df_1h, "4H")),
            "1D": df_to_candles(resample(df_1h, "1D")), "1W": df_to_candles(resample(df_1h, "1W"))}
