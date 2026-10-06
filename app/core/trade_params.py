"""Trade parameter construction — "Set and Forget" (02 §L, 03 §2).

Entry : close of the trigger candle (market order).
SL    : long  = low(trigger candle / star formation) − sl_buffer_pips × pip
        short = high(trigger candle / star formation) + sl_buffer_pips × pip
TP    : entry ± R × |entry − SL|, capped at the macro structural boundary
        (long: min(·, active_HH); short: max(·, active_LL)).
Gate  : realized R:R < min_rr after the cap → RR_CAP_REJECT.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import BUY, EPS, INVALID_SL, RR_CAP_REJECT


@dataclass(slots=True)
class TradeParams:
    entry: float
    sl: float
    tp: float
    rr: float
    capped: bool


@dataclass(slots=True)
class TradeParamsReject:
    reason: str
    detail: str
    entry: float
    sl: float
    tp: float
    rr: float


def build_trade_params(direction: str, entry: float, sl_ref: float, pip: float, *,
                       sl_buffer_pips: float = 15, tp_r_multiple: float = 2.0,
                       tp_cap_at_structure: bool = True, macro_boundary: float | None = None,
                       min_rr: float = 2.0) -> TradeParams | TradeParamsReject:
    if direction == BUY:
        sl = sl_ref - sl_buffer_pips * pip
        risk = entry - sl
    else:
        sl = sl_ref + sl_buffer_pips * pip
        risk = sl - entry
    if risk <= 0:
        return TradeParamsReject(INVALID_SL, f"non-positive risk {risk}", entry, sl, entry, 0.0)
    if direction == BUY:
        tp = entry + tp_r_multiple * risk
        capped = False
        if tp_cap_at_structure and macro_boundary is not None and macro_boundary < tp:
            tp, capped = macro_boundary, True
        rr = (tp - entry) / risk
    else:
        tp = entry - tp_r_multiple * risk
        capped = False
        if tp_cap_at_structure and macro_boundary is not None and macro_boundary > tp:
            tp, capped = macro_boundary, True
        rr = (entry - tp) / risk
    if rr + EPS < min_rr:
        return TradeParamsReject(RR_CAP_REJECT, f"R:R {rr:.2f} < min_rr {min_rr} after structure cap",
                                 entry, sl, tp, rr)
    return TradeParams(entry, sl, tp, rr, capped)


def realized_rr(direction: str, fill: float, sl: float, tp: float) -> float:
    """R:R recomputed from the actual fill price (03 §2)."""
    if direction == BUY:
        risk, reward = fill - sl, tp - fill
    else:
        risk, reward = sl - fill, fill - tp
    if risk <= 0:
        return 0.0
    return reward / risk


def rr_slippage_abort(direction: str, fill: float, sl: float, tp: float, min_rr: float,
                      tolerance: float) -> tuple[bool, float]:
    """True if slippage dropped realized R:R below ``min_rr − tolerance`` (RR_SLIPPAGE_ABORT)."""
    rr = realized_rr(direction, fill, sl, tp)
    return rr + EPS < (min_rr - tolerance), rr


def entry_still_valid(direction: str, price: float, z_min: float, z_max: float, pip: float,
                      tolerance_pips: float) -> bool:
    """Pre-trade check (04 §4): price within ``entry_zone_tolerance_pips`` of the AOI edge."""
    if z_min - EPS <= price <= z_max + EPS:
        return True
    dist = price - z_max if price > z_max else z_min - price
    return dist <= tolerance_pips * pip + EPS
