"""Incremental indicators: Wilder ATR (02 §E) and EMA (02 §J)."""
from __future__ import annotations


class WilderATR:
    """ATR with Wilder smoothing. ``values[i]`` is the ATR after bar i (None during warm-up)."""

    def __init__(self, period: int = 14):
        self.period = period
        self.values: list[float | None] = []
        self._trs: list[float] = []
        self._prev_close: float | None = None
        self._atr: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close
        if self._atr is None:
            self._trs.append(tr)
            if len(self._trs) == self.period:
                self._atr = sum(self._trs) / self.period
                self._trs.clear()
        else:
            self._atr = (self._atr * (self.period - 1) + tr) / self.period
        self.values.append(self._atr)
        return self._atr

    @property
    def last(self) -> float | None:
        return self._atr

    def at(self, i: int) -> float | None:
        if 0 <= i < len(self.values):
            return self.values[i]
        return None


class EMA:
    """EMA(t) = α·close(t) + (1−α)·EMA(t−1), α = 2/(period+1); seeded with the SMA of the
    first ``period`` closes."""

    def __init__(self, period: int = 50):
        self.period = period
        self.alpha = 2.0 / (period + 1)
        self.values: list[float | None] = []
        self._seed: list[float] = []
        self._ema: float | None = None

    def update(self, close: float) -> float | None:
        if self._ema is None:
            self._seed.append(close)
            if len(self._seed) == self.period:
                self._ema = sum(self._seed) / self.period
                self._seed.clear()
        else:
            self._ema = self.alpha * close + (1 - self.alpha) * self._ema
        self.values.append(self._ema)
        return self._ema

    @property
    def last(self) -> float | None:
        return self._ema
