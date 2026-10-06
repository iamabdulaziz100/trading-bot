"""Position sizing — fixed % of balance (03 §1).

risk_money  = balance × risk_pct
lots        = risk_money × tick_size / (|entry − SL| × tick_value)
lots        = floor to lot_step, clamp to max_lot; below min_lot → skip (never round up)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.config import RiskConfig
from app.symbols import SymbolSpec

RISK_SKIP_UNDER_MIN_LOT = "RISK_SKIP_UNDER_MIN_LOT"
RISK_INVALID_SL = "RISK_INVALID_SL"
RISK_NO_SPEC = "RISK_NO_SPEC"


@dataclass(slots=True)
class SizingResult:
    ok: bool
    lots: float
    raw_lots: float
    risk_money: float
    reason: str | None = None
    detail: str = ""


def _step_decimals(step: float) -> int:
    s = f"{step:.10f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def floor_to_step(lots: float, step: float) -> float:
    if step <= 0:
        return lots
    return round(math.floor(lots / step + 1e-9) * step, _step_decimals(step))


def compute_lots(balance: float, risk_pct: float, entry: float, sl: float, spec: SymbolSpec | None) -> SizingResult:
    risk_money = balance * risk_pct / 100.0
    if spec is None or spec.tick_size <= 0 or spec.tick_value <= 0:
        return SizingResult(False, 0.0, 0.0, risk_money, RISK_NO_SPEC, "symbol spec unavailable")
    dist = abs(entry - sl)
    if dist <= 0:
        return SizingResult(False, 0.0, 0.0, risk_money, RISK_INVALID_SL, "sl_distance ≤ 0")
    raw = risk_money * spec.tick_size / (dist * spec.tick_value)
    lots = min(floor_to_step(raw, spec.lot_step), spec.max_lot)
    if lots + 1e-12 < spec.min_lot:
        return SizingResult(False, 0.0, raw, risk_money, RISK_SKIP_UNDER_MIN_LOT,
                            f"computed {raw:.4f} lots < min_lot {spec.min_lot}")
    # actual money at risk after flooring
    actual = lots * dist * spec.tick_value / spec.tick_size
    return SizingResult(True, lots, raw, actual, None, f"{lots} lots (raw {raw:.4f}), risk {actual:.2f}")


def risk_pct_for(pillars: int, cfg: RiskConfig) -> float:
    """03 §5: optional pillar risk scaling (off by default): 4 pillars → risk_per_trade_pct_max."""
    if cfg.pillar_risk_scaling and pillars >= 4:
        return cfg.risk_per_trade_pct_max
    return cfg.risk_per_trade_pct
