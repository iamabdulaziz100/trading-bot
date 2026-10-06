"""Exact-ratio boundary tests for the trigger formations (02 §I)."""
from app.core.candles import (
    bear_engulf,
    bull_engulf,
    classify,
    evening_star,
    hammer,
    morning_star,
    shooting_star,
    trigger_for,
)
from app.models import BUY, HAMMER, MORNING_STAR, SELL, Candle


def C(o, h, l, c):  # noqa: E741
    return Candle(0, o, h, l, c)


# ── engulfing ─────────────────────────────────────────────────────────────────────────

def test_bull_engulf_must_cover_both_prior_bodies():
    c2 = C(1.1020, 1.1035, 1.1010, 1.1030)  # body 1.1020–1.1030
    c1 = C(1.1015, 1.1018, 1.1004, 1.1006)  # bearish, body 1.1006–1.1015
    ok = C(1.1005, 1.1040, 1.1000, 1.1035)  # closes above both bodies, opens ≤ min(o1,c1)
    assert bull_engulf([c2, c1, ok])
    only_t1 = C(1.1005, 1.1030, 1.1000, 1.1025)  # covers t−1 (1.1015) but not t−2 (1.1030)
    assert not bull_engulf([c2, c1, only_t1])
    late_open = C(1.1008, 1.1040, 1.1000, 1.1035)  # open above min(o1, c1)=1.1006
    assert not bull_engulf([c2, c1, late_open])


def test_bear_engulf_mirror():
    c2 = C(1.1000, 1.1012, 1.0995, 1.1010)
    c1 = C(1.1012, 1.1022, 1.1010, 1.1020)
    ok = C(1.1021, 1.1025, 1.0985, 1.0990)  # close < min of both bodies (1.1000), open ≥ max(o1,c1)
    assert bear_engulf([c2, c1, ok])
    only_t1 = C(1.1021, 1.1025, 1.1000, 1.1005)  # 1.1005 < 1.1012 but not < 1.1000
    assert not bear_engulf([c2, c1, only_t1])


# ── stars ─────────────────────────────────────────────────────────────────────────────

def _morning(mid_body: float, third_close: float) -> list[Candle]:
    impulse = C(1.1100, 1.1105, 1.0995, 1.1000)  # bearish, midpoint 1.1050
    middle = C(1.1000, 1.1000 + 0.0100 * (1 - 0.0), 1.1000 - 0.0, 1.1000 + mid_body)  # range 0.0100
    third = C(1.1002, max(1.1002, third_close) + 0.0002, 1.0998, third_close)
    return [impulse, middle, third]


def test_star_indecision_boundary_035_pass_036_fail():
    assert morning_star(_morning(0.0035, 1.1060))
    assert not morning_star(_morning(0.0036, 1.1060))


def test_star_third_candle_must_pass_impulse_midpoint():
    assert morning_star(_morning(0.0010, 1.1050))  # exactly the midpoint passes (≥)
    assert not morning_star(_morning(0.0010, 1.1049))


def test_evening_star_mirror():
    impulse = C(1.1000, 1.1105, 1.0995, 1.1100)  # bullish, midpoint 1.1050
    middle = C(1.1100, 1.1150, 1.1050, 1.1135)  # body 0.0035 = 0.35 × range 0.0100
    third = C(1.1098, 1.1100, 1.1040, 1.1050)
    assert evening_star([impulse, middle, third])
    third_short = C(1.1098, 1.1100, 1.1045, 1.1051)
    assert not evening_star([impulse, middle, third_short])


# ── hammer / shooting star ───────────────────────────────────────────────────────────

def test_hammer_exact_boundaries():
    # range 1.0000; lower wick 0.60; body 0.25 → both exactly at the boundary → pass
    assert hammer(C(100.60, 101.00, 100.00, 100.85))
    # body 0.26 → fail
    assert not hammer(C(100.60, 101.00, 100.00, 100.86))
    # lower wick 0.59 → fail
    assert not hammer(C(100.59, 101.00, 100.00, 100.80))


def test_shooting_star_exact_boundaries():
    assert shooting_star(C(100.40, 101.00, 100.00, 100.15))  # upper wick 0.60, body 0.25
    assert not shooting_star(C(100.41, 101.00, 100.00, 100.15))  # upper wick 0.59


def test_zero_range_candle_matches_nothing():
    flat = C(1.1000, 1.1000, 1.1000, 1.1000)
    prior = [C(1.1010, 1.1012, 1.0998, 1.1000), C(1.1000, 1.1002, 1.0990, 1.0992)]
    assert classify(prior + [flat]) == []
    assert not hammer(flat) and not shooting_star(flat)


def test_classify_sl_ref_and_direction_match():
    seq = _morning(0.0010, 1.1060)
    sig = trigger_for(seq, BUY)
    assert sig is not None and sig.type == MORNING_STAR
    assert sig.sl_ref == min(c.low for c in seq)  # formation extreme wick
    assert trigger_for(seq, SELL) is None
    h = C(100.60, 101.00, 100.00, 100.85)
    hs = trigger_for([h, h, h], BUY, enabled=[HAMMER])
    assert hs is not None and hs.type == HAMMER and hs.sl_ref == 100.00
    assert trigger_for([h, h, h], BUY, enabled=["BULL_ENGULF"]) is None
