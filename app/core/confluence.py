"""Confluence decision rule (02 §K) and EMA bonus filter (02 §J).

| Pillar | Pass condition |
| 1 Trend Alignment   | §F holds in the trade direction |
| 2 Valid AOI         | price interacting with a valid, boundary-confined zone, correct side |
| 3 Structural Pattern| unexpired PatternEvent in trade direction |
| 4 Candle Trigger    | CandleSignal on the just-closed candle, direction-matched |

A TradeSignal fires iff pillar_count ≥ min_pillars AND a pillar-4 trigger exists — the trigger
candle defines entry and the SL invalidation wick (02 §M: ``if pillars.count < min_pillars or
csig is None: return``). EMA50 is shown/journaled but never counted unless
``ema_counts_as_confluence`` is enabled.
"""
from __future__ import annotations

from app.models import BUY, LOW_CONFLUENCE, NO_TRIGGER, PillarResult


def ema_confluence(close: float, ema: float | None, direction: str) -> bool | None:
    if ema is None:
        return None
    return close > ema if direction == BUY else close < ema


def decide(pillars: PillarResult, min_pillars: int, ema_counts_as_confluence: bool = False) -> tuple[bool, int, str | None]:
    """Return (fire, pillar_count, reject_reason)."""
    n = pillars.count(ema_counts_as_confluence)
    if n < min_pillars:
        return False, n, LOW_CONFLUENCE
    if not pillars.p4:
        return False, n, NO_TRIGGER
    return True, n, None
