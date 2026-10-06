import json
from pathlib import Path

from app.models import HIGH, LOW, Candle
from tests.helpers import Rig, zigzag

FIX = Path(__file__).parent / "fixtures" / "swings_golden.json"


def test_golden_body_pivots_n5():
    data = json.loads(FIX.read_text(encoding="utf-8"))
    candles = [Candle(1_700_000_000 + i * 3600, *ohlc) for i, ohlc in enumerate(data["candles"])]
    rig = Rig(n=data["n"]).feed(candles)
    got = [(p.index, p.kind, round(p.price, 5), p.confirmed_index) for p in rig.swings.pivots]
    want = [(e["index"], e["kind"], e["price"], e["confirmed_at"]) for e in data["expected_pivots"]]
    assert got == want


def test_wick_extreme_is_not_a_pivot():
    data = json.loads(FIX.read_text(encoding="utf-8"))
    candles = [Candle(1_700_000_000 + i * 3600, *ohlc) for i, ohlc in enumerate(data["candles"])]
    rig = Rig(n=5).feed(candles)
    # bar 6 has the series' highest wick (1.1200) but a small body
    assert 6 not in rig.swings.high_at
    # bar 25 has the lowest wick (1.0950) but its body is not the lowest → no pivot there
    assert 25 not in rig.swings.low_at


def test_confirmation_exactly_n_bars_later():
    n = 5
    candles = zigzag(1.1000, [(1.1100, 10), (1.1000, 10)])
    rig = Rig(n=n)
    peak = 9  # closes reach 1.1100 at bar index 9
    for i, c in enumerate(candles):
        rig.feed([c])
        highs = [p.index for p in rig.swings.highs]
        if i < peak + n:
            assert peak not in highs, f"pivot confirmed early at bar {i}"
        else:
            assert peak in highs
    p = rig.swings.high_at[peak]
    assert p.confirmed_index == peak + n
    assert abs(p.price - 1.1100) < 1e-12


def test_tie_rule_first_of_equal_vs_strict():
    # open == previous close → the peak body extreme is shared by two adjacent bars
    candles = zigzag(1.1000, [(1.1050, 5), (1.1000, 5)])
    lenient = Rig(n=2, tie_rule="first_of_equal").feed(candles)
    strict = Rig(n=2, tie_rule="strict").feed(candles)
    assert [p.index for p in lenient.swings.highs] == [4]
    assert strict.swings.highs == []  # the formula as written finds no pivot on ties


def test_strict_rule_matches_when_no_ties():
    bodies = [(1.0, 1.1), (1.1, 1.2), (1.25, 1.5), (1.4, 1.3), (1.3, 1.2), (1.2, 1.1)]
    candles = [Candle(i * 3600, o, max(o, c) + 0.01, min(o, c) - 0.01, c) for i, (o, c) in enumerate(bodies)]
    a = Rig(n=2, tie_rule="strict").feed(candles)
    b = Rig(n=2, tie_rule="first_of_equal").feed(candles)
    assert [p.index for p in a.swings.highs] == [p.index for p in b.swings.highs] == [2]


def test_bump_filter_on_off():
    # big swing up 50 pips, small 4-pip dip, continuation, then a real drop
    legs = [(1.1050, 10), (1.1046, 3), (1.1080, 6), (1.1000, 10)]
    candles = zigzag(1.1000, legs)
    off = Rig(n=2, mult=0.0).feed(candles)
    on = Rig(n=2, mult=0.5, min_pips=10).feed(candles)
    assert len(off.swings.lows) > len(on.swings.lows)  # the 4-pip dip is a pivot only without the filter
    dip_low = min(p.price for p in off.swings.lows)
    assert abs(dip_low - 1.1046) < 1e-9
    assert all(abs(p.price - 1.1046) > 1e-9 for p in on.swings.lows)
    assert on.swings.rejected_bumps >= 1


def test_labels():
    candles = zigzag(1.1000, [(1.1050, 5), (1.1020, 5), (1.1080, 5), (1.1040, 5), (1.1060, 5), (1.1030, 5),
                              (1.1050, 5)])
    rig = Rig(n=2).feed(candles)
    highs = [p.label for p in rig.swings.highs]
    lows = [p.label for p in rig.swings.lows]
    assert highs[:3] == ["HH", "HH", "LH"]
    assert lows[:3] == ["LL", "HL", "LL"]
    assert all(p.kind in (HIGH, LOW) for p in rig.swings.pivots)
