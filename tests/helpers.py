"""Synthetic OHLC builders for golden-dataset tests."""
from __future__ import annotations

from app.config import BotConfig
from app.core.indicators import WilderATR
from app.core.series import CandleSeries
from app.core.structure import StructureMachine
from app.core.swings import SwingDetector
from app.models import Candle

PIP = 0.0001
H1 = 3600


def zigzag(start: float, legs: list[tuple[float, int]], wick: float = 2 * PIP, t0: int = 1_700_000_000,
           step: int = H1) -> list[Candle]:
    """Closes move linearly from ``start`` through each (target, n_bars) leg; open = previous
    close; wicks extend ``wick`` beyond the body on both sides."""
    closes: list[float] = []
    prev = start
    for target, n in legs:
        for k in range(1, n + 1):
            closes.append(round(prev + (target - prev) * k / n, 6))
        prev = target
    out, last = [], start
    for i, c in enumerate(closes):
        o = last
        out.append(Candle(t0 + i * step, o, max(o, c) + wick, min(o, c) - wick, c))
        last = c
    return out


def candles_from_bodies(bodies: list[tuple[float, float]], wick: float = 2 * PIP, t0: int = 1_700_000_000,
                        step: int = H1) -> list[Candle]:
    return [Candle(t0 + i * step, o, max(o, c) + wick, min(o, c) - wick, c) for i, (o, c) in enumerate(bodies)]


class Rig:
    """Series + ATR + swings + structure for one TF, fed bar by bar."""

    def __init__(self, n: int = 2, mult: float = 0.0, min_pips: float = 0.0, tie_rule: str = "first_of_equal",
                 pip: float = PIP):
        self.series = CandleSeries("1H")
        self.atr = WilderATR(14)
        self.swings = SwingDetector(self.series, self.atr, n, mult, min_pips, pip, tie_rule)
        self.sm = StructureMachine(self.series, self.swings, "1H", "TEST")
        self.log: list[dict] = []

    def feed(self, candles: list[Candle]) -> "Rig":
        for c in candles:
            t = self.series.append(c)
            self.atr.update(c.high, c.low, c.close)
            piv = self.swings.on_bar(t)
            ev = self.sm.on_bar(t)
            snap = self.sm.snapshot()
            self.log.append({"t": t, "pivots": piv, "events": ev, "state": snap.state, "HH": snap.active_HH,
                             "HL": snap.active_HL, "LH": snap.active_LH, "LL": snap.active_LL})
        return self


def cfg(**overrides) -> BotConfig:
    data = BotConfig().model_dump()
    for path, value in overrides.items():
        node = data
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
    return BotConfig.model_validate(data)
