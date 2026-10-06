"""Swing pivot detection — body-only, symmetric N-bar window (02 §C).

Swing High at t  iff  BodyHigh(t) > max(BodyHigh(t−N..t−1))  AND  BodyHigh(t) > max(BodyHigh(t+1..t+N))
Swing Low  at t  iff  BodyLow(t)  < min(BodyLow(t−N..t−1))   AND  BodyLow(t)  < min(BodyLow(t+1..t+N))

A pivot at bar i is only *confirmed* when bar i+N closes; downstream logic consumes confirmed
pivots exclusively (no lookahead — backtest/live parity contract).

Optional "bump" depth filter: a candidate is rejected when its distance to the last confirmed
opposite pivot is below ``max(min_swing_atr_mult × ATR(i), min_swing_pips × pip)``.
``min_swing_atr_mult = 0`` disables the filter (pure N-window per the formal spec).

Tie rule [DECISION]: with body-based pivots a turning point very often produces EQUAL body
extremes on adjacent bars (next open == previous close), in which case the strictly-greater
formula on both sides yields no pivot at all. ``tie_rule="first_of_equal"`` (default) keeps the
left comparison strict and allows equality on the right, so exactly one pivot (the first bar
of the plateau) is produced. Without ties both rules are identical. ``"strict"`` = formula as
written.
"""
from __future__ import annotations

from app.core.indicators import WilderATR
from app.core.series import CandleSeries
from app.models import EPS, HIGH, LOW, Pivot


class SwingDetector:
    def __init__(self, series: CandleSeries, atr: WilderATR, n: int, min_swing_atr_mult: float,
                 min_swing_pips: float, pip: float, tie_rule: str = "first_of_equal"):
        self.series = series
        self.atr = atr
        self.n = n
        self.right_inclusive = tie_rule == "first_of_equal"
        self.mult = min_swing_atr_mult
        self.min_pips = min_swing_pips
        self.pip = pip
        self.pivots: list[Pivot] = []
        self.highs: list[Pivot] = []
        self.lows: list[Pivot] = []
        self.high_at: dict[int, Pivot] = {}
        self.low_at: dict[int, Pivot] = {}
        self.rejected_bumps = 0

    @property
    def last_high(self) -> Pivot | None:
        return self.highs[-1] if self.highs else None

    @property
    def last_low(self) -> Pivot | None:
        return self.lows[-1] if self.lows else None

    def _depth_ok(self, price: float, i: int, opposite: Pivot | None) -> bool:
        if self.mult <= 0 or opposite is None:
            return True
        atr = self.atr.at(i) or 0.0
        threshold = max(self.mult * atr, self.min_pips * self.pip)
        return abs(price - opposite.price) + EPS >= threshold

    def on_bar(self, t: int) -> list[Pivot]:
        """Call after bar ``t`` closed. Returns pivots confirmed at this bar (candidate i = t−N)."""
        n, s = self.n, self.series
        i = t - n
        if i - n < 0:
            return []
        out: list[Pivot] = []

        right = range(i + 1, i + n + 1)
        h = s.hi(i)
        right_ok = (all(h >= s.hi(k) for k in right) if self.right_inclusive
                    else all(h > s.hi(k) for k in right))
        if right_ok and all(h > s.hi(k) for k in range(i - n, i)):
            if self._depth_ok(h, i, self.last_low):
                prev = self.last_high
                label = "HH" if prev is None or h > prev.price else "LH"
                p = Pivot(i, s.time[i], h, HIGH, True, t, label)
                self.highs.append(p)
                self.high_at[i] = p
                self.pivots.append(p)
                out.append(p)
            else:
                self.rejected_bumps += 1

        lo = s.lo(i)
        right_ok = (all(lo <= s.lo(k) for k in right) if self.right_inclusive
                    else all(lo < s.lo(k) for k in right))
        if right_ok and all(lo < s.lo(k) for k in range(i - n, i)):
            if self._depth_ok(lo, i, self.last_high):
                prev = self.last_low
                label = "LL" if prev is None or lo < prev.price else "HL"
                p = Pivot(i, s.time[i], lo, LOW, True, t, label)
                self.lows.append(p)
                self.low_at[i] = p
                self.pivots.append(p)
                out.append(p)
            else:
                self.rejected_bumps += 1
        return out

    def is_pivot(self, i: int, kind: str) -> bool:
        return i in (self.high_at if kind == HIGH else self.low_at)

    def pivots_in(self, start: int, end: int, kind: str) -> list[Pivot]:
        src = self.highs if kind == HIGH else self.lows
        return [p for p in src if start <= p.index <= end]

    def alternating(self, tail: int | None = None) -> list[Pivot]:
        """Confirmed pivots compressed into a strictly alternating HIGH/LOW sequence
        (consecutive same-kind pivots keep the more extreme one). ``tail`` limits the input to
        the most recent ``tail`` pivots."""
        src = self.pivots[-tail:] if tail else self.pivots
        seq: list[Pivot] = []
        for p in sorted(src, key=lambda q: (q.index, 0 if q.kind == HIGH else 1)):
            if seq and seq[-1].kind == p.kind:
                if (p.kind == HIGH and p.price > seq[-1].price) or (p.kind == LOW and p.price < seq[-1].price):
                    seq[-1] = p
                continue
            seq.append(p)
        return seq
