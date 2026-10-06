"""AOI clustering engine — Pillar 2 (02 §G).

Construction (G.1):
  1. A vertical window of width W = ``max_width_pips`` is swept over the price range of the
     confirmed pivot body-prices P with step ``sweep_step_pips``. Greedily, the window position
     with the most intersecting pivots forms a candidate (tie → tightest pivot extent, then
     lowest); its pivots are removed and the sweep repeats while the best count ≥ min_touches.
     Support and resistance pivots count alike (role flips are valid).
  2. Candidates whose centres are within ``merge_tolerance`` (default max(0.25×ATR, 10 pips))
     merge.
Validity (G.2):  Touches(Z) ≥ 3  AND  5×pip ≤ (z_max − z_min) ≤ 60×pip.
  Zones narrower than the minimum are padded symmetrically before the touch recount; zones
  wider than the maximum discard their outermost pivots until they fit, else are dropped.
Confinement (G.3): long ⇒ active_HL < z_min AND z_max < active_HH;
                   short ⇒ active_LL < z_min AND z_max < active_LH.
"""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from app.models import BEARISH, BULLISH, BUY, EPS, StructureSnapshot, Zone


def is_valid_zone(z_min: float, z_max: float, touches: int, pip: float, min_touches: int = 3,
                  min_width_pips: float = 5, max_width_pips: float = 60) -> bool:
    """IsValid(Z) = Touches(Z) ≥ 3 AND 5×pip ≤ (z_max − z_min) ≤ 60×pip  (02 §G.2, exact)."""
    width = z_max - z_min
    return (touches >= min_touches
            and width + EPS >= min_width_pips * pip
            and width <= max_width_pips * pip + EPS)


def count_touches(prices: np.ndarray, z_min: float, z_max: float) -> int:
    """Touches(Z) = Σ 1(p_k ∈ [z_min, z_max])."""
    lo = np.searchsorted(prices, z_min - EPS, side="left")
    hi = np.searchsorted(prices, z_max + EPS, side="right")
    return int(hi - lo)


def _greedy_candidates(prices: np.ndarray, window: float, step: float, min_touches: int) -> list[np.ndarray]:
    remaining = np.sort(prices)
    out: list[np.ndarray] = []
    while len(remaining) >= min_touches:
        starts = np.arange(remaining[0] - window, remaining[-1] + step / 2, step)
        lo = np.searchsorted(remaining, starts - EPS, side="left")
        hi = np.searchsorted(remaining, starts + window + EPS, side="right")
        counts = hi - lo
        best = int(counts.max())
        if best < min_touches:
            break
        idx = np.nonzero(counts == best)[0]
        extents = remaining[hi[idx] - 1] - remaining[lo[idx]]
        j = idx[int(np.argmin(extents))]
        a, b = int(lo[j]), int(hi[j])
        out.append(remaining[a:b].copy())
        remaining = np.concatenate([remaining[:a], remaining[b:]])
    return out


def _merge(groups: list[np.ndarray], tol: float) -> list[np.ndarray]:
    groups = sorted(groups, key=lambda g: (g.min() + g.max()) / 2)
    merged = True
    while merged and len(groups) > 1:
        merged = False
        for i in range(len(groups) - 1):
            a, b = groups[i], groups[i + 1]
            ca, cb = (a.min() + a.max()) / 2, (b.min() + b.max()) / 2
            if abs(ca - cb) <= tol + EPS:
                groups[i] = np.sort(np.concatenate([a, b]))
                del groups[i + 1]
                merged = True
                break
    return groups


def _fit_width(members: np.ndarray, max_width: float, min_touches: int) -> np.ndarray | None:
    m = np.sort(members)
    while len(m) >= min_touches and (m[-1] - m[0]) > max_width + EPS:
        med = float(np.median(m))
        if (m[-1] - med) >= (med - m[0]):
            m = m[:-1]
        else:
            m = m[1:]
    if len(m) < min_touches:
        return None
    return m


def build_zones(pivot_prices: Iterable[float], pip: float, tf: str, *, min_touches: int = 3,
                min_width_pips: float = 5, max_width_pips: float = 60, sweep_step_pips: float = 1,
                merge_tolerance: float | None = None) -> list[Zone]:
    prices = np.sort(np.asarray(list(pivot_prices), dtype=float))
    if len(prices) < min_touches:
        return []
    max_w, min_w = max_width_pips * pip, min_width_pips * pip
    tol = merge_tolerance if merge_tolerance is not None else 10 * pip
    groups = _greedy_candidates(prices, max_w, sweep_step_pips * pip, min_touches)
    groups = _merge(groups, tol)
    zones: list[Zone] = []
    for g in groups:
        fitted = _fit_width(g, max_w, min_touches)
        if fitted is None:
            continue
        z_min, z_max = float(fitted[0]), float(fitted[-1])
        if (z_max - z_min) < min_w:
            c = (z_min + z_max) / 2
            z_min, z_max = c - min_w / 2, c + min_w / 2
        touches = count_touches(prices, z_min, z_max)
        valid = is_valid_zone(z_min, z_max, touches, pip, min_touches, min_width_pips, max_width_pips)
        zones.append(Zone(tf, z_min, z_max, touches, valid, tuple(float(x) for x in fitted)))
    zones.sort(key=lambda z: z.z_min)
    return zones


def is_confined(zone: Zone, structure: StructureSnapshot, direction: str) -> bool:
    """Boundary confinement rule (02 §G.3, exact)."""
    if direction == BUY:
        return (structure.state == BULLISH and structure.active_HL is not None
                and structure.active_HH is not None
                and structure.active_HL < zone.z_min and zone.z_max < structure.active_HH)
    return (structure.state == BEARISH and structure.active_LL is not None
            and structure.active_LH is not None
            and structure.active_LL < zone.z_min and zone.z_max < structure.active_LH)


def interacts(zone: Zone, high: float, low: float) -> bool:
    """Candle range intersects [z_min, z_max] (02 §I)."""
    return low <= zone.z_max + EPS and high >= zone.z_min - EPS


def correct_side(zone: Zone, close: float, direction: str) -> bool:
    """Cardinal rule (02 §G.4): BUY only at support-side interaction (close above/inside the
    zone), SELL only at resistance-side interaction (close below/inside)."""
    if direction == BUY:
        return close >= zone.z_min - EPS
    return close <= zone.z_max + EPS
