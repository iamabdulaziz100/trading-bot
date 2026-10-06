"""Head & Shoulders / inverse H&S (02 §H.2), two-trough horizontal neckline, N = 2.

Path: S_L 1.1100 @5, T1 1.1050 @10, H 1.1150 @15, T2 1.1052 @20, S_R 1.1100 @25,
      decisive break: close 1.1040 @27 (< neckline 1.1050), down to 1.0980 @29,
      pull-back to 1.1048 @33 (retest of the neckline from below) → HS_NECKLINE_BREAK @33.
"""
from app.core.patterns import HeadShouldersEngine
from app.models import HS_NECKLINE_BREAK, INV_HS_NECKLINE_BREAK, BUY, HIGH, LOW, SELL
from tests.helpers import Rig, zigzag

PIP = 0.0001
LEGS = [(1.1100, 6), (1.1050, 5), (1.1150, 5), (1.1052, 5), (1.1100, 5), (1.0980, 4), (1.1048, 4),
        (1.0950, 5)]
MIRROR = 2.2200


def run(legs, start=1.1000):
    rig = Rig(n=2)
    eng = HeadShouldersEngine(0.6, retest_window_bars=15, retest_tolerance=5 * PIP, confirm_window_bars=20)
    events = []
    for c in zigzag(start, legs):
        rig.feed([c])
        t = len(rig.series) - 1
        evs = eng.on_bar(rig.series, t)
        if rig.log[-1]["pivots"]:
            evs += eng.detect(rig.swings, rig.series, t, rig.atr.last)
        events += [(t, e) for e in evs]
    return events, eng, rig


def test_match_formula():
    thr = 0.0010
    assert HeadShouldersEngine.match([(HIGH, 1.11), (LOW, 1.105), (HIGH, 1.115), (LOW, 1.1052), (HIGH, 1.11)],
                                     thr) == (SELL, 1.105, 1.115)
    # head not higher than left shoulder
    assert HeadShouldersEngine.match([(HIGH, 1.12), (LOW, 1.105), (HIGH, 1.115), (LOW, 1.1052), (HIGH, 1.11)],
                                     thr) is None
    # right shoulder above head / below V1
    assert HeadShouldersEngine.match([(HIGH, 1.11), (LOW, 1.105), (HIGH, 1.115), (LOW, 1.1052), (HIGH, 1.116)],
                                     thr) is None
    assert HeadShouldersEngine.match([(HIGH, 1.11), (LOW, 1.105), (HIGH, 1.115), (LOW, 1.1052), (HIGH, 1.1049)],
                                     thr) is None
    # troughs misaligned beyond the threshold
    assert HeadShouldersEngine.match([(HIGH, 1.11), (LOW, 1.105), (HIGH, 1.115), (LOW, 1.1065), (HIGH, 1.11)],
                                     thr) is None
    # inverse: neckline = max(V1, V2)
    assert HeadShouldersEngine.match([(LOW, 1.09), (HIGH, 1.095), (LOW, 1.085), (HIGH, 1.0948), (LOW, 1.09)],
                                     thr) == (BUY, 1.095, 1.085)


def test_hs_confirmed_break_then_retest_emits_event():
    events, eng, _ = run(LEGS)
    assert [(t, e.type, e.direction) for t, e in events] == [(33, HS_NECKLINE_BREAK, SELL)]
    assert abs(events[0][1].level - 1.1050) < 1e-9  # neckline = min(V1, V2), horizontal
    p = eng.history[-1]
    assert p.break_bar == 27 and p.sr_bar == 25


def test_right_shoulder_region_never_emits_before_neckline_break():
    """Strategy's 'high-risk error': nothing before the body close below the neckline."""
    events, _, _ = run(LEGS)
    assert all(t > 27 for t, _ in events)
    # price rolls over at the right shoulder but never closes below the neckline → no event ever
    no_break = LEGS[:5] + [(1.1056, 4), (1.1090, 4), (1.1160, 6)]
    events2, eng2, _ = run(no_break)
    assert events2 == []
    assert all(p.break_bar is None for p in eng2.history + eng2.active)


def test_misaligned_troughs_no_pattern():
    legs = list(LEGS)
    legs[3] = (1.1080, 5)  # T2 30 pips from T1 → beyond 0.6 × ATR
    events, eng, _ = run(legs)
    assert events == [] and eng.active == [] and eng.history == []


def test_inverse_hs_mirror():
    legs = [(round(MIRROR - p, 6), n) for p, n in LEGS]
    events, _, _ = run(legs, start=round(MIRROR - 1.1000, 6))
    assert [(t, e.type, e.direction) for t, e in events] == [(33, INV_HS_NECKLINE_BREAK, BUY)]
    assert abs(events[0][1].level - round(MIRROR - 1.1050, 6)) < 1e-9  # max(V1, V2)
