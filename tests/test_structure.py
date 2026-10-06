"""Structure state machine (02 §E) + Snake Trick (02 §D) golden scenarios (N = 2).

Path (closes, open = previous close):
  idx 0..9   up to A = 1.1100 (peak, pivot 9)
  idx 10..14 down to B = 1.1050 (trough, pivot 14)
  idx 15..24 up to 1.1150 — first close > A at idx 20 → INIT_BULLISH, HL = B (snake over [9, 20])
  idx 25..31 down to C = 1.1080 (trough, pivot 31)
  idx 32..35 up to 1.1120 (no break)
  idx 36..40 down to E = 1.1095 (trough, pivot 40, HIGHER than C)
  idx 41..50 up to 1.1200 — close > HH at idx 46 → HL = C (absolute lowest of the run, not E)
  idx 51..64 down to 1.1060 — close < HL (1.1080) at idx 63 → BOS_BEARISH, LH = 1.1200 (pivot 50)
"""
from app.models import (
    BEARISH,
    BOS_BEARISH,
    BOS_BULLISH,
    BULLISH,
    HIGH,
    INIT_BEARISH,
    INIT_BULLISH,
    LIQUIDITY_SWEEP,
    LOW,
    SNAKE_NO_PIVOT,
    UNDEFINED,
    Candle,
)
from tests.helpers import Rig, zigzag

LEGS = [(1.1100, 10), (1.1050, 5), (1.1150, 10), (1.1080, 7), (1.1120, 4), (1.1095, 5), (1.1200, 10),
        (1.1060, 14)]
MIRROR = 2.2200


def scenario() -> Rig:
    return Rig(n=2).feed(zigzag(1.1000, LEGS))


def mirrored() -> Rig:
    legs = [(round(MIRROR - p, 6), n) for p, n in LEGS]
    return Rig(n=2).feed(zigzag(round(MIRROR - 1.1000, 6), legs))


def types(rig: Rig, t: int) -> list[str]:
    return [e.type for e in rig.log[t]["events"]]


def approx(a, b):
    return a is not None and abs(a - b) < 1e-9


# ── structure ────────────────────────────────────────────────────────────────────────────

def test_undefined_until_pivots_and_first_break():
    rig = scenario()
    for t in range(20):
        assert rig.log[t]["state"] == UNDEFINED
    assert rig.log[20]["state"] == BULLISH
    assert INIT_BULLISH in types(rig, 20)
    assert approx(rig.log[20]["HL"], 1.1050)
    assert approx(rig.log[20]["HH"], 1.1110)  # BodyHigh of the break candle


def test_extension_uses_body_high_and_keeps_hl_without_pivot():
    rig = scenario()
    for t in range(21, 25):
        assert approx(rig.log[t]["HH"], 1.1100 + (t - 19) * 0.0010)
        assert approx(rig.log[t]["HL"], 1.1050)
        assert SNAKE_NO_PIVOT in types(rig, t)


def test_snake_trick_picks_absolute_lowest_body_of_run():
    rig = scenario()
    assert 31 in rig.swings.low_at and 40 in rig.swings.low_at  # two candidate lows
    assert rig.log[45]["state"] == BULLISH and approx(rig.log[45]["HH"], 1.1150)
    # break at 46: HL = C (1.1080 @31), not the most recent trough E (1.1095 @40)
    assert approx(rig.log[46]["HH"], 1.1158)
    assert approx(rig.log[46]["HL"], 1.1080)


def test_bearish_shift_on_close_below_hl():
    rig = scenario()
    assert rig.log[62]["state"] == BULLISH  # close 1.1080 is not < HL
    assert rig.log[63]["state"] == BEARISH
    assert BOS_BEARISH in types(rig, 63)
    assert approx(rig.log[63]["LH"], 1.1200)  # argmax BodyHigh over [last_break, t]
    assert approx(rig.log[63]["LL"], 1.1070)
    assert rig.log[63]["HH"] is None and rig.log[63]["HL"] is None
    assert rig.sm.trend == -1


def test_wick_below_hl_with_body_close_above_is_a_sweep_not_a_shift():
    candles = zigzag(1.1000, LEGS[:3])  # ends BULLISH, HL = 1.1050, HH = 1.1150
    last = candles[-1]
    sweep = Candle(last.time + 3600, 1.1150, 1.1152, 1.1040, 1.1140)  # low < HL, close > HL
    rig = Rig(n=2).feed(candles + [sweep])
    t = len(candles)
    assert rig.log[t]["state"] == BULLISH
    assert LIQUIDITY_SWEEP in types(rig, t)
    assert approx(rig.log[t]["HL"], 1.1050)


def test_mirror_bearish_init_extension_and_bullish_shift():
    rig = mirrored()
    m = lambda p: round(MIRROR - p, 6)  # noqa: E731
    assert rig.log[19]["state"] == UNDEFINED
    assert rig.log[20]["state"] == BEARISH and INIT_BEARISH in types(rig, 20)
    assert approx(rig.log[20]["LH"], m(1.1050))
    assert approx(rig.log[20]["LL"], m(1.1110))
    assert approx(rig.log[46]["LH"], m(1.1080))  # argmax BodyHigh = absolute highest of the run
    assert rig.log[63]["state"] == BULLISH and BOS_BULLISH in types(rig, 63)
    assert approx(rig.log[63]["HL"], m(1.1200))
    assert approx(rig.log[63]["HH"], m(1.1070))


# ── snake trace function (02 §D) ─────────────────────────────────────────────────────────

def test_snake_trace_direct():
    rig = scenario()
    sm = rig.sm
    p = sm.snake_trace(24, 46, LOW)
    assert p is not None and p.index == 31
    # argmin bar (32, start of range) is not a pivot → nearest confirmed pivot in K
    p = sm.snake_trace(32, 46, LOW)
    assert p is not None and p.index == 40
    # no confirmed pivot in K → None (caller keeps the previous boundary)
    assert sm.snake_trace(20, 23, LOW) is None
    p = sm.snake_trace(50, 63, HIGH)
    assert p is not None and p.index == 50


def test_sweep_reported_once_per_boundary_level():
    candles = zigzag(1.1000, LEGS[:3])
    last = candles[-1]
    sweeps = [Candle(last.time + 3600 * (k + 1), 1.1150, 1.1152, 1.1040, 1.1140) for k in range(3)]
    rig = Rig(n=2).feed(candles + sweeps)
    n = len(candles)
    assert [LIQUIDITY_SWEEP in types(rig, n + k) for k in range(3)] == [True, False, False]
