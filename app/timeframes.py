"""Timeframe constants shared by the live feed, the strategy pipeline and the backtester."""
from __future__ import annotations

TF_SECONDS: dict[str, int] = {
    "1W": 7 * 86400,
    "1D": 86400,
    "4H": 4 * 3600,
    "1H": 3600,
    "30M": 1800,
    "15M": 900,
}

# Higher rank = higher timeframe. When several candles close at the same instant the
# higher timeframe is processed first, so the execution TF always sees fresh HTF state.
TF_RANK: dict[str, int] = {"1W": 6, "1D": 5, "4H": 4, "1H": 3, "30M": 2, "15M": 1}

# History warm-up (04 §3). Used by the live feed AND the backtester so both start their
# structure state machines from the same amount of history (parity contract).
WARMUP_BARS: dict[str, int] = {
    "1W": 320,
    "1D": 600,
    "4H": 2200,
    "1H": 3000,
    "30M": 3000,
    "15M": 3000,
}

HTF_CHAIN = ("1W", "1D", "4H")
EXECUTION_TFS = ("1H", "30M", "15M")


def tf_seconds(tf: str) -> int:
    try:
        return TF_SECONDS[tf]
    except KeyError as exc:  # pragma: no cover - guarded by config validation
        raise ValueError(f"unknown timeframe {tf!r}") from exc


def close_time(open_time: int, tf: str) -> int:
    """Epoch second at which a candle that opened at ``open_time`` is fully closed."""
    return open_time + tf_seconds(tf)


def event_sort_key(open_time: int, tf: str, symbol: str = "") -> tuple[int, int, str]:
    """Ordering of candle-close events: by close time, higher TF first, then symbol."""
    return (close_time(open_time, tf), -TF_RANK[tf], symbol)
