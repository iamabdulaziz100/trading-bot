"""Append-only closed-candle series for one symbol + timeframe."""
from __future__ import annotations

from app.models import Candle


class CandleSeries:
    __slots__ = ("tf", "time", "open", "high", "low", "close", "body_based")

    def __init__(self, tf: str, body_based: bool = True):
        self.tf = tf
        self.body_based = body_based
        self.time: list[int] = []
        self.open: list[float] = []
        self.high: list[float] = []
        self.low: list[float] = []
        self.close: list[float] = []

    def __len__(self) -> int:
        return len(self.time)

    def append(self, c: Candle) -> int:
        if self.time and c.time <= self.time[-1]:
            raise ValueError(f"{self.tf}: non-increasing candle time {c.time} <= {self.time[-1]}")
        self.time.append(int(c.time))
        self.open.append(float(c.open))
        self.high.append(float(c.high))
        self.low.append(float(c.low))
        self.close.append(float(c.close))
        return len(self.time) - 1

    def candle(self, i: int) -> Candle:
        return Candle(self.time[i], self.open[i], self.high[i], self.low[i], self.close[i])

    # Structure prices: bodies (spec) or wicks (only if body_based_swings is disabled)
    def hi(self, i: int) -> float:
        if self.body_based:
            o, c = self.open[i], self.close[i]
            return o if o > c else c
        return self.high[i]

    def lo(self, i: int) -> float:
        if self.body_based:
            o, c = self.open[i], self.close[i]
            return o if o < c else c
        return self.low[i]

    def body_high(self, i: int) -> float:
        o, c = self.open[i], self.close[i]
        return o if o > c else c

    def body_low(self, i: int) -> float:
        o, c = self.open[i], self.close[i]
        return o if o < c else c

    @property
    def last_time(self) -> int | None:
        return self.time[-1] if self.time else None
