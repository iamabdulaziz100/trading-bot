"""Multi-timeframe alignment matrix — Pillar 1 (02 §F).

Long_Allowed  = (T_1W = +1 AND T_1D = +1) OR (T_1D = +1 AND T_4H = +1)
Short_Allowed = (T_1W = −1 AND T_1D = −1) OR (T_1D = −1 AND T_4H = −1)

A TF in UNDEFINED state counts as 0 and can satisfy neither clause.
"""
from __future__ import annotations

from app.models import BUY, LONG, NEUTRAL_FILTER, SELL, SHORT


def alignment_matrix(t_1w: int, t_1d: int, t_4h: int) -> str:
    long_allowed = (t_1w == 1 and t_1d == 1) or (t_1d == 1 and t_4h == 1)
    short_allowed = (t_1w == -1 and t_1d == -1) or (t_1d == -1 and t_4h == -1)
    if long_allowed and not short_allowed:
        return LONG
    if short_allowed and not long_allowed:
        return SHORT
    return NEUTRAL_FILTER


def alignment_direction(alignment: str) -> str | None:
    return BUY if alignment == LONG else SELL if alignment == SHORT else None
