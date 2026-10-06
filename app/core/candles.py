"""Candlestick trigger formations — Pillar 4 (02 §I), exact formulas.

Bullish:
  Bull Engulfing  C>O AND C > max(O,C of t−1..t−L) AND O ≤ min(O(t−1), C(t−1))      (L = engulf_lookback)
  Morning Star    C(t−2)<O(t−2); |C(t−1)−O(t−1)| ≤ 0.35×(H(t−1)−L(t−1)); C>O AND C ≥ (O(t−2)+C(t−2))/2
  Hammer          (min(O,C)−L)/(H−L) ≥ 0.60 AND |C−O|/(H−L) ≤ 0.25
Bearish mirrors: Bear Engulfing, Evening Star, Shooting Star.

Guard: a candle with H−L = 0 matches nothing. Threshold comparisons carry a 1e-9 epsilon so
values that are exactly at a ratio boundary (e.g. 0.35) pass despite binary float error.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.models import (
    BEAR_ENGULF,
    BULL_ENGULF,
    BUY,
    EPS,
    EVENING_STAR,
    HAMMER,
    MORNING_STAR,
    SELL,
    SHOOTING_STAR,
    Candle,
    CandleSignal,
)


def _rng(c: Candle) -> float:
    return c.high - c.low


def bull_engulf(cs: Sequence[Candle], lookback: int = 2) -> bool:
    if len(cs) < lookback + 1:
        return False
    t = cs[-1]
    prior = cs[-1 - lookback:-1]
    if _rng(t) <= 0:
        return False
    p1 = cs[-2]
    return (t.close > t.open
            and t.close > max(max(p.open, p.close) for p in prior)
            and t.open <= min(p1.open, p1.close) + EPS)


def bear_engulf(cs: Sequence[Candle], lookback: int = 2) -> bool:
    if len(cs) < lookback + 1:
        return False
    t = cs[-1]
    prior = cs[-1 - lookback:-1]
    if _rng(t) <= 0:
        return False
    p1 = cs[-2]
    return (t.close < t.open
            and t.close < min(min(p.open, p.close) for p in prior)
            and t.open >= max(p1.open, p1.close) - EPS)


def _indecision(c: Candle, ratio: float) -> bool:
    r = _rng(c)
    return r > 0 and abs(c.close - c.open) <= ratio * r + EPS


def morning_star(cs: Sequence[Candle], ratio: float = 0.35) -> bool:
    if len(cs) < 3:
        return False
    c2, c1, t = cs[-3], cs[-2], cs[-1]
    if _rng(t) <= 0:
        return False
    return (c2.close < c2.open
            and _indecision(c1, ratio)
            and t.close > t.open
            and t.close >= (c2.open + c2.close) / 2 - EPS)


def evening_star(cs: Sequence[Candle], ratio: float = 0.35) -> bool:
    if len(cs) < 3:
        return False
    c2, c1, t = cs[-3], cs[-2], cs[-1]
    if _rng(t) <= 0:
        return False
    return (c2.close > c2.open
            and _indecision(c1, ratio)
            and t.close < t.open
            and t.close <= (c2.open + c2.close) / 2 + EPS)


def hammer(c: Candle, wick_ratio: float = 0.60, body_ratio: float = 0.25) -> bool:
    r = _rng(c)
    if r <= 0:
        return False
    return ((min(c.open, c.close) - c.low) / r >= wick_ratio - EPS
            and abs(c.close - c.open) / r <= body_ratio + EPS)


def shooting_star(c: Candle, wick_ratio: float = 0.60, body_ratio: float = 0.25) -> bool:
    r = _rng(c)
    if r <= 0:
        return False
    return ((c.high - max(c.open, c.close)) / r >= wick_ratio - EPS
            and abs(c.close - c.open) / r <= body_ratio + EPS)


def classify(cs: Sequence[Candle], *, star_ratio: float = 0.35, wick_ratio: float = 0.60,
             body_ratio: float = 0.25, engulf_lookback: int = 2,
             enabled: Sequence[str] | None = None) -> list[CandleSignal]:
    """All trigger formations completed by the last (just-closed) candle of ``cs``.

    Priority order: engulfing → star → hammer/shooting star. ``sl_ref`` is the invalidation
    wick — the trigger candle's low/high, or the formation extreme (t−2..t) for stars."""
    if not cs:
        return []
    en = set(enabled) if enabled is not None else None
    t = cs[-1]
    out: list[CandleSignal] = []

    def on(name: str) -> bool:
        return en is None or name in en

    if on(BULL_ENGULF) and bull_engulf(cs, engulf_lookback):
        out.append(CandleSignal(BULL_ENGULF, BUY, t.low))
    if on(BEAR_ENGULF) and bear_engulf(cs, engulf_lookback):
        out.append(CandleSignal(BEAR_ENGULF, SELL, t.high))
    if on(MORNING_STAR) and morning_star(cs, star_ratio):
        out.append(CandleSignal(MORNING_STAR, BUY, min(c.low for c in cs[-3:])))
    if on(EVENING_STAR) and evening_star(cs, star_ratio):
        out.append(CandleSignal(EVENING_STAR, SELL, max(c.high for c in cs[-3:])))
    if on(HAMMER) and hammer(t, wick_ratio, body_ratio):
        out.append(CandleSignal(HAMMER, BUY, t.low))
    if on(SHOOTING_STAR) and shooting_star(t, wick_ratio, body_ratio):
        out.append(CandleSignal(SHOOTING_STAR, SELL, t.high))
    return out


def trigger_for(cs: Sequence[Candle], direction: str, **kw) -> CandleSignal | None:
    """First direction-matched trigger on the just-closed candle (02 §K pillar 4)."""
    for sig in classify(cs, **kw):
        if sig.direction == direction:
            return sig
    return None
