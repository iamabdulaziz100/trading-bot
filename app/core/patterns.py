"""Structural patterns — Pillar 3 (02 §H), execution-TF state machines.

Break & Retest (H.1), per zone Z, both directions tracked (pillar 3 filters by trade direction):
  Bullish: ARMED when a candle body-closes above z_max coming from at/below it
           (close(t−k) > z_max, close(t−k−1) ≤ z_max). RETEST at bar t when
           low(t) ≤ z_max AND BodyLow(t) ≥ z_min − retest_tolerance, within retest_window_bars.
           A body close below z_min (far side) cancels. Emits BREAK_RETEST.
  Bearish: mirror.

Head & Shoulders (H.2), 5 alternating confirmed body pivots S_L, T_1, H, T_2, S_R:
  P2 > P1,  |V2 − V1| ≤ neckline_threshold_atr_mult × ATR(14),  V1 < P3 < P2
  Neckline = min(V1, V2) (horizontal, bodies). Confirmation = body close below the neckline.
  Valid entry = later retest of the neckline from below (high ≥ neckline − tol AND
  BodyHigh ≤ neckline + tol) → emits HS_NECKLINE_BREAK (SELL). Inverted H&S is the mirror
  (neckline = max(V1, V2)) → INV_HS_NECKLINE_BREAK (BUY).
  HARD GUARD: nothing is emitted before the neckline body-close confirmation, so the
  right-shoulder region can never produce a pattern event.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.series import CandleSeries
from app.core.swings import SwingDetector
from app.models import (
    BREAK_RETEST,
    BUY,
    EPS,
    HIGH,
    HS_NECKLINE_BREAK,
    INV_HS_NECKLINE_BREAK,
    LOW,
    SELL,
    PatternEvent,
    Zone,
)


# ── Break & Retest ──────────────────────────────────────────────────────────────────────

@dataclass
class BRTracker:
    zone: Zone
    bull_armed_bar: int | None = None
    bear_armed_bar: int | None = None

    def step(self, s: CandleSeries, t: int, window: int, tol: float) -> list[PatternEvent]:
        z = self.zone
        o, h, l, c = s.open[t], s.high[t], s.low[t], s.close[t]
        prev_c = s.close[t - 1] if t > 0 else None
        body_lo, body_hi = min(o, c), max(o, c)
        out: list[PatternEvent] = []

        # bullish
        if self.bull_armed_bar is not None:
            if c < z.z_min:
                self.bull_armed_bar = None  # body close back through the far side → cancel
            elif t - self.bull_armed_bar > window:
                self.bull_armed_bar = None  # retest window elapsed
            elif l <= z.z_max + EPS and body_lo >= z.z_min - tol - EPS:
                out.append(PatternEvent(BREAK_RETEST, BUY, z.z_max, t, s.time[t],
                                        f"{z.tf} zone {z.z_min:.5f}-{z.z_max:.5f} broken up at bar "
                                        f"{self.bull_armed_bar}, retested"))
                self.bull_armed_bar = None
        elif prev_c is not None and c > z.z_max and prev_c <= z.z_max:
            self.bull_armed_bar = t

        # bearish
        if self.bear_armed_bar is not None:
            if c > z.z_max:
                self.bear_armed_bar = None
            elif t - self.bear_armed_bar > window:
                self.bear_armed_bar = None
            elif h >= z.z_min - EPS and body_hi <= z.z_max + tol + EPS:
                out.append(PatternEvent(BREAK_RETEST, SELL, z.z_min, t, s.time[t],
                                        f"{z.tf} zone {z.z_min:.5f}-{z.z_max:.5f} broken down at bar "
                                        f"{self.bear_armed_bar}, retested"))
                self.bear_armed_bar = None
        elif prev_c is not None and c < z.z_min and prev_c >= z.z_min:
            self.bear_armed_bar = t
        return out


class BreakRetestEngine:
    def __init__(self, retest_window_bars: int, retest_tolerance: float):
        self.window = retest_window_bars
        self.tol = retest_tolerance
        self.trackers: list[BRTracker] = []

    def set_zones(self, zones: list[Zone], carry_tolerance: float) -> None:
        """Replace the tracked zone set; armed state carries over to the overlapping new zone."""
        new: list[BRTracker] = []
        for z in zones:
            tr = BRTracker(z)
            for old in self.trackers:
                oz = old.zone
                if oz.tf == z.tf and abs(oz.center - z.center) <= carry_tolerance + EPS:
                    tr.bull_armed_bar = old.bull_armed_bar
                    tr.bear_armed_bar = old.bear_armed_bar
                    break
            new.append(tr)
        self.trackers = new

    def on_bar(self, s: CandleSeries, t: int) -> list[PatternEvent]:
        out: list[PatternEvent] = []
        for tr in self.trackers:
            out.extend(tr.step(s, t, self.window, self.tol))
        return out


# ── Head & Shoulders ────────────────────────────────────────────────────────────────────

ARMED, BROKEN, DONE, DROPPED = "ARMED", "BROKEN", "DONE", "DROPPED"


@dataclass
class HSPattern:
    direction: str  # SELL = bearish H&S, BUY = inverted H&S
    neckline: float
    head: float
    sr_bar: int  # right-shoulder pivot bar
    detected_bar: int
    key: tuple[int, ...]
    state: str = ARMED
    break_bar: int | None = None
    pivots: list[tuple[int, float]] = field(default_factory=list)


class HeadShouldersEngine:
    def __init__(self, threshold_atr_mult: float, retest_window_bars: int, retest_tolerance: float,
                 confirm_window_bars: int):
        self.mult = threshold_atr_mult
        self.window = retest_window_bars
        self.tol = retest_tolerance
        self.confirm_window = confirm_window_bars
        self.active: list[HSPattern] = []
        self.history: list[HSPattern] = []
        self._seen: set[tuple[int, ...]] = set()

    @staticmethod
    def match(seq5: list[tuple[str, float]], threshold: float) -> tuple[str, float, float] | None:
        """Match 5 alternating pivots [(kind, price)…]. Returns (direction, neckline, head)."""
        kinds = [k for k, _ in seq5]
        p = [v for _, v in seq5]
        if kinds == [HIGH, LOW, HIGH, LOW, HIGH]:
            P1, V1, P2, V2, P3 = p
            if P2 > P1 and abs(V2 - V1) <= threshold + EPS and V1 < P3 < P2:
                return SELL, min(V1, V2), P2
        elif kinds == [LOW, HIGH, LOW, HIGH, LOW]:
            P1, V1, P2, V2, P3 = p
            if P2 < P1 and abs(V2 - V1) <= threshold + EPS and P2 < P3 < V1:
                return BUY, max(V1, V2), P2
        return None

    def _step(self, p: HSPattern, s: CandleSeries, t: int) -> PatternEvent | None:
        o, h, l, c = s.open[t], s.high[t], s.low[t], s.close[t]
        nk, tol = p.neckline, self.tol
        if p.state == ARMED:
            if (p.direction == SELL and c > p.head) or (p.direction == BUY and c < p.head):
                p.state = DROPPED  # head exceeded on close → pattern invalid
            elif (p.direction == SELL and c < nk) or (p.direction == BUY and c > nk):
                p.state, p.break_bar = BROKEN, t  # neckline body-close confirmation
            elif t - p.detected_bar > self.confirm_window:
                p.state = DROPPED
            return None
        if p.state == BROKEN:
            assert p.break_bar is not None
            if t == p.break_bar:
                return None
            if t - p.break_bar > self.window:
                p.state = DROPPED
                return None
            if p.direction == SELL:
                if c > nk + tol:
                    p.state = DROPPED
                elif h >= nk - tol - EPS and max(o, c) <= nk + tol + EPS:
                    p.state = DONE
                    return PatternEvent(HS_NECKLINE_BREAK, SELL, nk, t, s.time[t],
                                        f"H&S neckline {nk:.5f} broken at bar {p.break_bar}, retested")
            else:
                if c < nk - tol:
                    p.state = DROPPED
                elif l <= nk + tol + EPS and min(o, c) >= nk - tol - EPS:
                    p.state = DONE
                    return PatternEvent(INV_HS_NECKLINE_BREAK, BUY, nk, t, s.time[t],
                                        f"inverse H&S neckline {nk:.5f} broken at bar {p.break_bar}, retested")
        return None

    def on_bar(self, s: CandleSeries, t: int) -> list[PatternEvent]:
        """Advance active patterns with the just-closed bar ``t``."""
        out: list[PatternEvent] = []
        for p in self.active:
            ev = self._step(p, s, t)
            if ev:
                out.append(ev)
        self._retire()
        return out

    def detect(self, swings: SwingDetector, s: CandleSeries, t: int, atr: float | None) -> list[PatternEvent]:
        """Check the latest 5 alternating confirmed pivots; call when new pivots confirmed at t.
        A new pattern is replayed over bars (S_R, t] so a neckline break that happened before
        S_R was confirmable is honoured (all bars ≤ t — no lookahead)."""
        if atr is None:
            return []
        seq = swings.alternating(tail=16)
        if len(seq) < 5:
            return []
        last5 = seq[-5:]
        key = tuple(p.index for p in last5)
        if key in self._seen:
            return []
        m = self.match([(p.kind, p.price) for p in last5], self.mult * atr)
        if m is None:
            return []
        self._seen.add(key)
        direction, neckline, head = m
        p = HSPattern(direction, neckline, head, last5[-1].index, t, key,
                      pivots=[(q.index, q.price) for q in last5])
        out: list[PatternEvent] = []
        for k in range(p.sr_bar + 1, t + 1):
            ev = self._step(p, s, k)
            if ev:
                out.append(ev)
            if p.state in (DONE, DROPPED):
                break
        self.active.append(p)
        self._retire()
        return out

    def _retire(self) -> None:
        keep = []
        for p in self.active:
            if p.state in (DONE, DROPPED):
                self.history.append(p)
            else:
                keep.append(p)
        self.active = keep
        if len(self.history) > 200:
            del self.history[:100]
