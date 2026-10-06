"""Historical OHLC loading for backtests (05 §2).

Accepted CSV layouts (auto-detected):
* canonical:   time_utc,open,high,low,close[,volume]   (ISO time or epoch seconds, UTC)
* MT5 export:  <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> <TICKVOL> …  (tab/comma separated,
               server time → pass ``tz_offset_hours`` to convert to UTC)
* Dukascopy:   Gmt time,Open,High,Low,Close,Volume  ("02.01.2024 00:00:00.000", UTC)
Files live in ``data/history/{SYMBOL}_{TF}.csv``. Missing higher timeframes can be resampled
from a lower one (UTC boundaries; weeks start Sunday like MT5) — a note is added to the report.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.models import Candle
from app.timeframes import TF_RANK, TF_SECONDS

log = logging.getLogger("backtest.data")

FILE_RE = re.compile(r"^(?P<symbol>[A-Za-z0-9.#_-]+?)_(?P<tf>1W|1D|4H|1H|30M|15M)\.csv$", re.I)


def history_path(history_dir: Path, symbol: str, tf: str) -> Path:
    return history_dir / f"{symbol}_{tf}.csv"


def parse_csv_bytes(content: bytes | str, tz_offset_hours: float = 0.0) -> pd.DataFrame:
    """Parse any supported layout into a DataFrame[time(int epoch UTC), open, high, low, close, volume]."""
    text = content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content
    sample = text[:4096]
    sep = "\t" if sample.count("\t") > sample.count(",") else ("," if "," in sample else r"\s+")
    df = pd.read_csv(io.StringIO(text), sep=sep, engine="python")
    df.columns = [str(c).strip().strip("<>").strip().lower() for c in df.columns]
    cols = set(df.columns)

    if "time_utc" in cols:
        tcol = df["time_utc"]
        if pd.api.types.is_numeric_dtype(tcol):
            t = pd.to_datetime(tcol.astype("int64"), unit="s", utc=True)
        else:
            t = pd.to_datetime(tcol.astype(str).str.replace("Z", "+00:00"), utc=True, format="mixed")
    elif "date" in cols:
        d = df["date"].astype(str).str.replace(".", "-", regex=False)
        if "time" in cols:
            d = d + " " + df["time"].astype(str)
        t = pd.to_datetime(d, utc=True, format="mixed")
    elif "gmt time" in cols:
        t = pd.to_datetime(df["gmt time"].astype(str), format="%d.%m.%Y %H:%M:%S.%f", utc=True)
    elif "time" in cols:
        t = pd.to_datetime(df["time"].astype(str), utc=True, format="mixed")
    else:
        raise ValueError(f"no time column found in CSV (columns: {sorted(cols)})")

    for need in ("open", "high", "low", "close"):
        if need not in cols:
            raise ValueError(f"CSV is missing column '{need}' (columns: {sorted(cols)})")
    vol = df["tickvol"] if "tickvol" in cols else df["volume"] if "volume" in cols else 0.0
    epoch = (t - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)
    out = pd.DataFrame({
        "time": epoch - int(tz_offset_hours * 3600),
        "open": df["open"].astype(float), "high": df["high"].astype(float),
        "low": df["low"].astype(float), "close": df["close"].astype(float),
        "volume": pd.Series(vol, index=df.index).astype(float),
    })
    out = out.dropna().drop_duplicates("time").sort_values("time").reset_index(drop=True)
    out["time"] = out["time"].astype("int64")
    return out


def df_to_candles(df: pd.DataFrame) -> list[Candle]:
    return [Candle(int(t), float(o), float(h), float(lo), float(c), float(v))
            for t, o, h, lo, c, v in zip(df["time"], df["open"], df["high"], df["low"], df["close"], df["volume"])]


def write_canonical(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    iso = pd.to_datetime(df["time"], unit="s", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out = pd.DataFrame({"time_utc": iso, "open": df["open"], "high": df["high"], "low": df["low"],
                        "close": df["close"], "volume": df["volume"]})
    out.to_csv(path, index=False)


def load_file(path: Path) -> pd.DataFrame:
    return parse_csv_bytes(path.read_bytes())


def _bucket(epoch: pd.Series, tf: str) -> pd.Series:
    if tf == "1W":
        # MT5 weekly bars open on Sunday 00:00 → bucket start = previous Sunday
        days = epoch // 86400
        weekday_mon0 = (days + 3) % 7  # 1970-01-01 was a Thursday (Mon=0 → Thu=3)
        since_sunday = (weekday_mon0 + 1) % 7
        return (days - since_sunday) * 86400
    sec = TF_SECONDS[tf]
    return epoch // sec * sec


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    key = _bucket(df["time"], tf)
    g = df.groupby(key, sort=True)
    out = pd.DataFrame({"time": g["time"].first().index.astype("int64"), "open": g["open"].first().values,
                        "high": g["high"].max().values, "low": g["low"].min().values,
                        "close": g["close"].last().values, "volume": g["volume"].sum().values})
    return out


def available_files(history_dir: Path) -> list[dict]:
    out = []
    if not history_dir.exists():
        return out
    for p in sorted(history_dir.glob("*.csv")):
        m = FILE_RE.match(p.name)
        if not m:
            continue
        try:
            df = load_file(p)
        except Exception as exc:
            out.append({"symbol": m["symbol"], "tf": m["tf"].upper(), "rows": 0, "from": None, "to": None,
                        "error": str(exc)})
            continue
        fmt = lambda e: datetime.fromtimestamp(int(e), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        out.append({"symbol": m["symbol"], "tf": m["tf"].upper(), "rows": len(df),
                    "from": fmt(df["time"].iloc[0]) if len(df) else None,
                    "to": fmt(df["time"].iloc[-1]) if len(df) else None})
    return out


def load_symbol_history(history_dir: Path, symbol: str, tfs: list[str]) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Load every TF for ``symbol``; resample missing TFs from the lowest available lower TF."""
    notes: list[str] = []
    frames: dict[str, pd.DataFrame] = {}
    for tf in tfs:
        p = history_path(history_dir, symbol, tf)
        if p.exists():
            frames[tf] = load_file(p)
    lower_available = sorted(frames, key=lambda x: TF_RANK[x])
    for tf in tfs:
        if tf in frames:
            continue
        src = next((s for s in lower_available if TF_RANK[s] < TF_RANK[tf]), None)
        if src is None:
            raise FileNotFoundError(f"no history for {symbol} {tf} (expected {history_path(history_dir, symbol, tf)})"
                                    f" and no lower timeframe to resample from")
        frames[tf] = resample(frames[src], tf)
        notes.append(f"{symbol} {tf} resampled from {src} (UTC boundaries — may differ from broker candles)")
    return frames, notes
