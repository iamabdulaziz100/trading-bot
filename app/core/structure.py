"""Market structure state machine + "Snake Trick" traceback (02 §D, §E).

All break tests use the candle CLOSE only; wick pierces of an active boundary are logged as
liquidity sweeps and never change structure. Boundaries are body prices.

Initialisation [interpretation of "UNDEFINED until the first two confirmed pivots + first
break"]: once at least one confirmed swing high H and swing low L exist, the first close above
H (below L) sets BULLISH (BEARISH); the snake trace for that first break runs over
K = [min(H.index, L.index), t].
"""
from __future__ import annotations

import logging

from app.core.series import CandleSeries
from app.core.swings import SwingDetector
from app.models import (
    BEARISH,
    BOS_BEARISH,
    BOS_BULLISH,
    BULLISH,
    HIGH,
    INIT_BEARISH,
    INIT_BULLISH,
    LIQUIDITY_SWEEP,
    LOW,
    SNAKE_NO_PIVOT,
    UNDEFINED,
    Pivot,
    StructureEvent,
    StructureSnapshot,
)

log = logging.getLogger("structure")


class StructureMachine:
    def __init__(self, series: CandleSeries, swings: SwingDetector, tf: str, symbol: str = ""):
        self.series = series
        self.swings = swings
        self.tf = tf
        self.symbol = symbol
        self.state = UNDEFINED
        self.active_HH: float | None = None
        self.active_HL: float | None = None
        self.active_LH: float | None = None
        self.active_LL: float | None = None
        self.hh_bar: int | None = None
        self.hl_bar: int | None = None
        self.lh_bar: int | None = None
        self.ll_bar: int | None = None
        self.last_break_bar: int | None = None
        self.events: list[StructureEvent] = []
        self._swept: set[tuple[str, float]] = set()  # a boundary level is reported as swept once

    # ── public ──────────────────────────────────────────────────────────────────────────
    @property
    def trend(self) -> int:
        return 1 if self.state == BULLISH else -1 if self.state == BEARISH else 0

    def snapshot(self) -> StructureSnapshot:
        return StructureSnapshot(self.state, self.active_HH, self.active_HL, self.active_LH,
                                 self.active_LL, self.last_break_bar)

    # ── §D Snake Trick ─────────────────────────────────────────────────────────────────
    def snake_trace(self, start: int, t: int, kind: str) -> Pivot | None:
        """argmin BodyLow (kind=LOW) / argmax BodyHigh (kind=HIGH) over K=[start, t], snapped to
        a confirmed N-window pivot of that kind. Falls back to the nearest confirmed pivot within
        K; returns None if K holds no confirmed pivot of that kind."""
        s = self.series
        start = max(0, start)
        if start > t:
            return None
        f = s.lo if kind == LOW else s.hi
        values = [f(k) for k in range(start, t + 1)]
        extreme = min(values) if kind == LOW else max(values)
        candidates = [start + j for j, v in enumerate(values) if v == extreme]
        pivot_map = self.swings.low_at if kind == LOW else self.swings.high_at
        for k in candidates:
            if k in pivot_map:
                return pivot_map[k]
        k_star = candidates[0]
        in_range = self.swings.pivots_in(start, t, kind)
        if not in_range:
            return None
        if kind == LOW:
            return min(in_range, key=lambda p: (abs(p.index - k_star), p.price, -p.index))
        return min(in_range, key=lambda p: (abs(p.index - k_star), -p.price, -p.index))

    # ── §E state machine ───────────────────────────────────────────────────────────────
    def _event(self, typ: str, t: int, price: float, detail: str = "") -> StructureEvent:
        ev = StructureEvent(typ, t, self.series.time[t], price, self.tf, detail)
        self.events.append(ev)
        return ev

    def _sweep(self, out: list[StructureEvent], t: int, boundary: str, level: float, price: float,
               detail: str) -> None:
        key = (boundary, level)
        if key not in self._swept:
            self._swept.add(key)
            out.append(self._event(LIQUIDITY_SWEEP, t, price, detail))

    def _no_pivot(self, t: int, kind: str) -> StructureEvent:
        log.debug("%s %s SNAKE_NO_PIVOT (%s) at bar %d", self.symbol, self.tf, kind, t)
        return self._event(SNAKE_NO_PIVOT, t, self.series.close[t], f"no confirmed {kind} pivot in K")

    def on_bar(self, t: int) -> list[StructureEvent]:
        """Call after bar ``t`` closed and the swing detector processed it."""
        s = self.series
        c = s.close[t]
        out: list[StructureEvent] = []

        if self.state == UNDEFINED:
            H, L = self.swings.last_high, self.swings.last_low
            if H is None or L is None:
                return out
            start = min(H.index, L.index)
            if c > H.price:
                p = self.snake_trace(start, t, LOW) or L
                self.state = BULLISH
                self.active_HH, self.hh_bar = s.hi(t), t
                self.active_HL, self.hl_bar = p.price, p.index
                self.last_break_bar = t
                out.append(self._event(INIT_BULLISH, t, c))
            elif c < L.price:
                p = self.snake_trace(start, t, HIGH) or H
                self.state = BEARISH
                self.active_LL, self.ll_bar = s.lo(t), t
                self.active_LH, self.lh_bar = p.price, p.index
                self.last_break_bar = t
                out.append(self._event(INIT_BEARISH, t, c))
            return out

        assert self.last_break_bar is not None
        if self.state == BULLISH:
            assert self.active_HH is not None and self.active_HL is not None
            if c > self.active_HH:  # BULLISH EXTENSION
                p = self.snake_trace(self.last_break_bar, t, LOW)
                self.active_HH, self.hh_bar = s.hi(t), t
                if p is not None:
                    self.active_HL, self.hl_bar = p.price, p.index
                else:
                    out.append(self._no_pivot(t, LOW))
                self.last_break_bar = t
            elif c < self.active_HL:  # BEARISH SHIFT (BOS/CHoCH)
                p = self.snake_trace(self.last_break_bar, t, HIGH)
                if p is not None:
                    self.active_LH, self.lh_bar = p.price, p.index
                else:
                    out.append(self._no_pivot(t, HIGH))
                    self.active_LH, self.lh_bar = self.active_HH, self.hh_bar
                self.active_LL, self.ll_bar = s.lo(t), t
                self.active_HH = self.active_HL = None
                self.hh_bar = self.hl_bar = None
                self.state = BEARISH
                self.last_break_bar = t
                out.append(self._event(BOS_BEARISH, t, c))
            else:
                if s.low[t] < self.active_HL:
                    self._sweep(out, t, "HL", self.active_HL, s.low[t], "wick below HL, close above")
                if s.high[t] > self.active_HH:
                    self._sweep(out, t, "HH", self.active_HH, s.high[t], "wick above HH, close below")
            return out

        # BEARISH
        assert self.active_LL is not None and self.active_LH is not None
        if c < self.active_LL:  # BEARISH EXTENSION
            p = self.snake_trace(self.last_break_bar, t, HIGH)
            self.active_LL, self.ll_bar = s.lo(t), t
            if p is not None:
                self.active_LH, self.lh_bar = p.price, p.index
            else:
                out.append(self._no_pivot(t, HIGH))
            self.last_break_bar = t
        elif c > self.active_LH:  # BULLISH SHIFT (BOS/CHoCH)
            p = self.snake_trace(self.last_break_bar, t, LOW)
            if p is not None:
                self.active_HL, self.hl_bar = p.price, p.index
            else:
                out.append(self._no_pivot(t, LOW))
                self.active_HL, self.hl_bar = self.active_LL, self.ll_bar
            self.active_HH, self.hh_bar = s.hi(t), t
            self.active_LH = self.active_LL = None
            self.lh_bar = self.ll_bar = None
            self.state = BULLISH
            self.last_break_bar = t
            out.append(self._event(BOS_BULLISH, t, c))
        else:
            if s.high[t] > self.active_LH:
                self._sweep(out, t, "LH", self.active_LH, s.high[t], "wick above LH, close below")
            if s.low[t] < self.active_LL:
                self._sweep(out, t, "LL", self.active_LL, s.low[t], "wick below LL, close above")
        return out
