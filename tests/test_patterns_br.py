"""Break & Retest state machine (02 §H.1)."""
from app.core.patterns import BreakRetestEngine, BRTracker
from app.core.series import CandleSeries
from app.models import BREAK_RETEST, BUY, SELL, Candle, Zone

PIP = 0.0001
Z = Zone("1D", 1.1000, 1.1010, 4, True)
WINDOW, TOL = 15, 5 * PIP


def run(ohlc: list[tuple[float, float, float, float]], zone: Zone = Z):
    s = CandleSeries("1H")
    tr = BRTracker(zone)
    events = []
    for i, (o, h, l, c) in enumerate(ohlc):  # noqa: E741
        t = s.append(Candle(1_700_000_000 + i * 3600, o, h, l, c))
        events += [(t, e) for e in tr.step(s, t, WINDOW, TOL)]
    return events, tr


BASE = [
    (1.0990, 1.0997, 1.0988, 1.0995),  # 0 below
    (1.0995, 1.1007, 1.0993, 1.1005),  # 1 inside
    (1.1005, 1.1022, 1.1003, 1.1020),  # 2 body close above z_max (from inside) → ARMED
    (1.1020, 1.1035, 1.1018, 1.1030),  # 3 away from zone
]


def test_arm_then_retest_emits_event():
    retest = (1.1030, 1.1032, 1.1008, 1.1015)  # low ≤ z_max, BodyLow ≥ z_min − tol
    events, _ = run(BASE + [retest])
    assert len(events) == 1
    t, e = events[0]
    assert t == 4 and e.type == BREAK_RETEST and e.direction == BUY and abs(e.level - 1.1010) < 1e-12


def test_no_retest_without_breakout():
    ohlc = [(1.0990, 1.0997, 1.0988, 1.0995), (1.0995, 1.1008, 1.0993, 1.1004),
            (1.1004, 1.1009, 1.1001, 1.1006), (1.1006, 1.1009, 1.1002, 1.1003)]
    events, _ = run(ohlc)
    assert events == []


def test_body_close_through_far_side_cancels():
    cancel = (1.1030, 1.1031, 1.0990, 1.0995)  # close < z_min
    later = (1.0995, 1.1012, 1.0994, 1.1009)
    events, tr = run(BASE + [cancel, later])
    assert all(e.direction != BUY for _, e in events)
    assert tr.bull_armed_bar is None


def test_body_falling_through_is_not_a_retest():
    deep = (1.1030, 1.1031, 1.0990, 1.1001)  # BodyLow 1.1001 ≥ z_min − tol → still a retest
    events, _ = run(BASE + [deep])
    assert [e.direction for _, e in events] == [BUY]
    too_deep_open = (1.0992, 1.1031, 1.0990, 1.1012)  # BodyLow 1.0992 < z_min − tol (1.0995)
    events2, tr = run(BASE + [too_deep_open])
    assert events2 == [] and tr.bull_armed_bar == 2


def test_retest_window_expiry():
    away = [(1.1030, 1.1035, 1.1025, 1.1030)] * 20
    retest = (1.1030, 1.1032, 1.1008, 1.1015)
    # armed at 2: retest at t = 2 + 15 = 17 is inside the window
    events, _ = run(BASE + away[:13] + [retest])
    assert len(events) == 1 and events[0][0] == 17
    # retest at t = 18 (16 bars after arming) → expired
    events, _ = run(BASE + away[:14] + [retest])
    assert events == []


def test_bearish_mirror():
    ohlc = [(1.1015, 1.1018, 1.1012, 1.1014), (1.1014, 1.1016, 1.1002, 1.1004),
            (1.1004, 1.1006, 1.0985, 1.0990),  # close < z_min from inside → ARMED (bear)
            (1.0990, 1.0992, 1.0975, 1.0980),
            (1.0980, 1.1003, 1.0978, 1.0995)]  # high ≥ z_min, BodyHigh ≤ z_max + tol
    events, _ = run(ohlc)
    assert [(t, e.direction, round(e.level, 4)) for t, e in events] == [(4, SELL, 1.1000)]


def test_engine_carries_armed_state_across_zone_rebuild():
    eng = BreakRetestEngine(WINDOW, TOL)
    eng.set_zones([Z], 10 * PIP)
    eng.trackers[0].bull_armed_bar = 7
    eng.set_zones([Zone("1D", 1.1001, 1.1011, 5, True)], 10 * PIP)
    assert eng.trackers[0].bull_armed_bar == 7
    eng.set_zones([Zone("1D", 1.1500, 1.1510, 3, True)], 10 * PIP)
    assert eng.trackers[0].bull_armed_bar is None
