"""Strategy pipeline per symbol (02 §M).

The SAME object is driven by the live data feed, the live warm-up and the backtester
(backtest parity contract, 02 §N). Nothing in this package knows whether it is running live
or in a backtest.

Interpretation notes ([DECISION], see docs/DECISIONS.md):
* Pillar 2 / G.3 confinement and the §L TP cap use the "macro" structure = the zone's own TF
  when that TF's state agrees with the trade direction, otherwise the 1D structure (which is
  always aligned: both §F clauses require T_1D = trade direction).
* Break & Retest trackers run on every execution-TF candle for every valid zone of the
  pillar-2 timeframes, so a breakout is never missed while price is away from the zone.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from app.config import BotConfig
from app.core.alignment import alignment_direction, alignment_matrix
from app.core.aoi import build_zones, correct_side, interacts, is_confined
from app.core.candles import trigger_for
from app.core.confluence import decide, ema_confluence
from app.core.indicators import EMA, WilderATR
from app.core.patterns import BreakRetestEngine, HeadShouldersEngine
from app.core.series import CandleSeries
from app.core.structure import StructureMachine
from app.core.swings import SwingDetector
from app.core.trade_params import TradeParamsReject, build_trade_params
from app.models import (
    BREAK_RETEST,
    BUY,
    NEUTRAL_FILTER,
    REJECTED,
    SIGNAL,
    Candle,
    Evaluation,
    PatternEvent,
    PillarResult,
    StepResult,
    TradeSignal,
    Zone,
)
from app.timeframes import TF_SECONDS, event_sort_key

log = logging.getLogger("pipeline")

NO_AOI = "NO_AOI"
MAX_EVENTS_KEPT = 2000


class SymbolPipeline:
    def __init__(self, symbol: str, cfg: BotConfig, pip: float):
        self.symbol = symbol
        self.cfg = cfg  # hot-reloadable parts (confluence thresholds, trade params) read at eval time
        self.engine_cfg = cfg  # structural params captured at construction (rebuild to change)
        self.pip = pip
        self.exec_tf = cfg.exec_tf
        self.tfs: list[str] = list(dict.fromkeys(cfg.all_tfs))
        sc = cfg.structure
        self.series: dict[str, CandleSeries] = {}
        self.atr: dict[str, WilderATR] = {}
        self.swings: dict[str, SwingDetector] = {}
        self.structure: dict[str, StructureMachine] = {}
        for tf in self.tfs:
            s = CandleSeries(tf, sc.body_based_swings)
            a = WilderATR(sc.atr_period)
            sw = SwingDetector(s, a, sc.pivot_window_n, sc.min_swing_atr_mult, sc.min_swing_pips, pip,
                               sc.pivot_tie_rule)
            self.series[tf], self.atr[tf], self.swings[tf] = s, a, sw
            self.structure[tf] = StructureMachine(s, sw, tf, symbol)
        self.ema = EMA(cfg.confluence.ema_period)
        self.zone_tfs: list[str] = cfg.aoi.effective_zone_tfs
        self.zones: dict[str, list[Zone]] = {tf: [] for tf in self.zone_tfs}
        self._zone_sig: dict[str, tuple] = {}
        br, hs = cfg.patterns.break_retest, cfg.patterns.head_shoulders
        self.br = BreakRetestEngine(br.retest_window_bars, br.retest_tolerance_pips * pip)
        self.hs = HeadShouldersEngine(hs.neckline_threshold_atr_mult, br.retest_window_bars,
                                      br.retest_tolerance_pips * pip, confirm_window_bars=hs.expiry_bars)
        self.pattern_events: list[PatternEvent] = []
        self.last_alignment: str = NEUTRAL_FILTER
        self.last_evaluation: Evaluation | None = None

    # ── feeding ────────────────────────────────────────────────────────────────────────
    def last_time(self, tf: str) -> int | None:
        return self.series[tf].last_time

    def on_candle_closed(self, tf: str, candle: Candle) -> StepResult:
        s = self.series[tf]
        t = s.append(candle)
        self.atr[tf].update(candle.high, candle.low, candle.close)
        if tf == self.exec_tf:
            self.ema.update(candle.close)
        new_pivots = self.swings[tf].on_bar(t)
        sm = self.structure[tf]
        sevents = sm.on_bar(t)
        if len(sm.events) > MAX_EVENTS_KEPT:
            del sm.events[: len(sm.events) - MAX_EVENTS_KEPT]
        res = StepResult(self.symbol, tf, t, candle, new_pivots, sevents)

        if tf in self.zone_tfs:
            self._rebuild_zones(tf, candle.time + TF_SECONDS[tf])
        if tf != self.exec_tf:
            return res

        pev = self.br.on_bar(s, t)
        pev += self.hs.on_bar(s, t)
        if new_pivots:
            pev += self.hs.detect(self.swings[tf], s, t, self.atr[tf].last)
        if pev:
            self.pattern_events.extend(pev)
        self._prune_patterns(t)
        res.pattern_events = pev
        self._evaluate(res, t, candle)
        return res

    # ── zones ──────────────────────────────────────────────────────────────────────────
    def merge_tolerance(self, tf: str) -> float:
        a = self.engine_cfg.aoi
        atr = self.atr[tf].last or 0.0
        return max(a.merge_tolerance_atr_mult * atr, a.merge_tolerance_min_pips * self.pip)

    def _rebuild_zones(self, tf: str, now_epoch: int) -> None:
        a = self.engine_cfg.aoi
        lb = self.engine_cfg.structure.lookback.get(tf)
        horizon = now_epoch - lb.to_seconds() if lb else None
        piv = [p for p in self.swings[tf].pivots if horizon is None or p.time >= horizon]
        tol = self.merge_tolerance(tf)
        sig = (len(piv), piv[0].index if piv else -1, piv[-1].index if piv else -1, round(tol / self.pip))
        if self._zone_sig.get(tf) == sig:
            return
        self._zone_sig[tf] = sig
        self.zones[tf] = build_zones([p.price for p in piv], self.pip, tf, min_touches=a.min_touches,
                                     min_width_pips=a.min_width_pips, max_width_pips=a.max_width_pips,
                                     sweep_step_pips=a.sweep_step_pips, merge_tolerance=tol)
        valid = [z for ztf in self.zone_tfs for z in self.zones[ztf] if z.valid]
        carry = max(self.merge_tolerance(ztf) for ztf in self.zone_tfs)
        self.br.set_zones(valid, carry)

    def macro_tf(self, zone_tf: str, direction: str) -> str:
        want = 1 if direction == BUY else -1
        return zone_tf if self.structure[zone_tf].trend == want else "1D"

    def interacting_zone(self, candle: Candle, direction: str) -> tuple[Zone, str] | None:
        """Pillar 2: valid zone on a qualifying TF, candle range intersecting it, correct side
        (G.4) and boundary-confined (G.3). Higher TF first, then most touches."""
        for tf in self.zone_tfs:
            mtf = self.macro_tf(tf, direction)
            macro = self.structure[mtf].snapshot()
            for z in sorted(self.zones[tf], key=lambda q: -q.touches):
                if not z.valid or not interacts(z, candle.high, candle.low):
                    continue
                if not correct_side(z, candle.close, direction):
                    continue
                if self.engine_cfg.aoi.boundary_confinement and not is_confined(z, macro, direction):
                    continue
                return z, mtf
        return None

    # ── patterns ───────────────────────────────────────────────────────────────────────
    def _expiry(self, ev: PatternEvent) -> int:
        p = self.cfg.patterns
        return p.break_retest.expiry_bars if ev.type == BREAK_RETEST else p.head_shoulders.expiry_bars

    def _prune_patterns(self, t: int) -> None:
        horizon = max(self.cfg.patterns.break_retest.expiry_bars, self.cfg.patterns.head_shoulders.expiry_bars)
        if self.pattern_events and t - self.pattern_events[0].bar > horizon:
            self.pattern_events = [e for e in self.pattern_events if t - e.bar <= horizon]

    def active_pattern(self, direction: str, t: int) -> PatternEvent | None:
        for ev in reversed(self.pattern_events):
            if ev.direction == direction and 0 <= t - ev.bar <= self._expiry(ev):
                return ev
        return None

    # ── evaluation (§M) ────────────────────────────────────────────────────────────────
    def trends(self) -> dict[str, int]:
        return {tf: self.structure[tf].trend for tf in ("1W", "1D", "4H")}

    def _evaluate(self, res: StepResult, t: int, candle: Candle) -> None:
        cfg = self.cfg
        tr = self.trends()
        alignment = alignment_matrix(tr["1W"], tr["1D"], tr["4H"])
        self.last_alignment = alignment
        direction = alignment_direction(alignment)
        if direction is None:
            res.skip_reason = NEUTRAL_FILTER
            return
        hit = self.interacting_zone(candle, direction)
        if hit is None:
            res.skip_reason = NO_AOI
            return
        zone, mtf = hit
        s = self.series[self.exec_tf]
        c = cfg.candles
        lookback = max(3, c.engulf_lookback + 1)
        recent = [s.candle(k) for k in range(max(0, t - lookback + 1), t + 1)]
        csig = trigger_for(recent, direction, star_ratio=c.star_indecision_body_ratio,
                           wick_ratio=c.hammer_wick_ratio, body_ratio=c.hammer_body_ratio,
                           engulf_lookback=c.engulf_lookback, enabled=c.triggers)
        pattern = self.active_pattern(direction, t)
        ema_ok = ema_confluence(candle.close, self.ema.last, direction)
        details: dict[str, Any] = {
            "pillar1": f"1W={tr['1W']:+d} 1D={tr['1D']:+d} 4H={tr['4H']:+d} -> {alignment}",
            "pillar2": f"{zone.tf} zone {zone.z_min:.5f}-{zone.z_max:.5f} ({zone.touches} touches), macro {mtf}",
            "pillar3": f"{pattern.type} @ {pattern.level:.5f} (bar {pattern.bar})" if pattern else "no unexpired pattern",
            "pillar4": csig.type if csig else "no trigger",
            "ema": None if self.ema.last is None else round(self.ema.last, 6),
        }
        pillars = PillarResult(True, True, pattern is not None, csig is not None, ema_ok, details)
        fire, n, reason = decide(pillars, cfg.confluence.min_pillars, cfg.confluence.ema_counts_as_confluence)
        signal_id = f"{self.symbol}-{self.exec_tf}-{candle.time}"
        signal_time = candle.time + TF_SECONDS[self.exec_tf]
        ev = Evaluation(self.symbol, self.exec_tf, candle.time, signal_time, direction, pillars, n,
                        csig.type if csig else None, pattern.type if pattern else None,
                        REJECTED, reason, signal_id)
        if fire and csig is not None:
            macro = self.structure[mtf].snapshot()
            boundary = macro.active_HH if direction == BUY else macro.active_LL
            tp_cfg = cfg.trade
            params = build_trade_params(direction, candle.close, csig.sl_ref, self.pip,
                                        sl_buffer_pips=tp_cfg.sl_buffer_pips, tp_r_multiple=tp_cfg.tp_r_multiple,
                                        tp_cap_at_structure=tp_cfg.tp_cap_at_structure,
                                        macro_boundary=boundary, min_rr=tp_cfg.min_rr)
            details.update(entry=params.entry, sl=params.sl, tp=params.tp, rr=round(params.rr, 3))
            if isinstance(params, TradeParamsReject):
                ev.reason = params.reason
                details["reject"] = params.detail
            else:
                details["tp_capped"] = params.capped
                reasons = [details["pillar1"], details["pillar2"], details["pillar3"], details["pillar4"]]
                ev.decision, ev.reason = SIGNAL, None
                ev.signal = TradeSignal(
                    signal_id=signal_id, symbol=self.symbol, timeframe=self.exec_tf, direction=direction,
                    entry=params.entry, sl=params.sl, tp=params.tp, rr=params.rr, pillars=n, reasons=reasons,
                    bar_time=candle.time, signal_time=signal_time, zone_tf=zone.tf, z_min=zone.z_min,
                    z_max=zone.z_max, candle_signal=csig.type, pattern_type=pattern.type if pattern else None,
                    ema_ok=ema_ok, pip_size=self.pip)
        res.evaluation = ev
        self.last_evaluation = ev

    # ── read models for the API / UI ───────────────────────────────────────────────────
    def candles(self, tf: str, limit: int = 500, start: int | None = None, end: int | None = None) -> list[dict]:
        s = self.series[tf]
        idx = range(len(s))
        out = [{"time": s.time[i], "open": s.open[i], "high": s.high[i], "low": s.low[i], "close": s.close[i]}
               for i in idx if (start is None or s.time[i] >= start) and (end is None or s.time[i] <= end)]
        return out[-limit:] if limit else out

    def overlays(self, tf: str, since: int | None = None) -> dict[str, Any]:
        sm = self.structure[tf]
        snap = sm.snapshot()
        last_close = self.series[tf].close[-1] if len(self.series[tf]) else None
        zones = []
        for ztf in self.zone_tfs:
            for z in self.zones[ztf]:
                d = BUY if (last_close is not None and last_close >= z.center) else "SELL"
                macro = self.structure[self.macro_tf(ztf, d)].snapshot()
                zones.append({"tf": ztf, "z_min": z.z_min, "z_max": z.z_max, "touches": z.touches,
                              "valid": z.valid, "confined": is_confined(z, macro, d),
                              "side": "support" if d == BUY else "resistance"})
        swings = [{"time": p.time, "price": p.price, "kind": p.kind, "label": p.label}
                  for p in self.swings[tf].pivots if since is None or p.time >= since]
        events = [{"time": e.time, "type": e.type, "price": e.price}
                  for e in sm.events if e.type != "SNAKE_NO_PIVOT" and (since is None or e.time >= since)]
        ema = []
        if tf == self.exec_tf:
            s = self.series[tf]
            ema = [{"time": s.time[i], "value": v} for i, v in enumerate(self.ema.values)
                   if v is not None and (since is None or s.time[i] >= since)]
        return {"structure": {"state": snap.state, "active_HH": snap.active_HH, "active_HL": snap.active_HL,
                              "active_LH": snap.active_LH, "active_LL": snap.active_LL},
                "zones": zones, "swings": swings, "events": events, "ema": ema}


def merge_candle_events(candles_by_tf: dict[str, Iterable[Candle]], symbol: str = "") -> list[tuple[str, Candle]]:
    """Order closed candles of several TFs as they would close in real time (close time, then
    higher TF first)."""
    evs = [(event_sort_key(c.time, tf, symbol), tf, c) for tf, cs in candles_by_tf.items() for c in cs]
    evs.sort(key=lambda e: e[0])
    return [(tf, c) for _, tf, c in evs]
