import itertools

from app.core.confluence import decide, ema_confluence
from app.core.pipeline import NO_AOI, SymbolPipeline
from app.models import BUY, LOW_CONFLUENCE, NEUTRAL_FILTER, NO_TRIGGER, SELL, PillarResult
from tests.helpers import cfg, zigzag


def test_all_16_pillar_combinations():
    for p1, p2, p3, p4 in itertools.product((False, True), repeat=4):
        pr = PillarResult(p1, p2, p3, p4, ema_ok=True)
        fire, n, reason = decide(pr, min_pillars=3)
        assert n == p1 + p2 + p3 + p4
        # only ≥3 pillars may emit, and the trigger candle (pillar 4) defines entry/SL (02 §M)
        assert fire == (n >= 3 and p4), (p1, p2, p3, p4)
        if n < 3:
            assert reason == LOW_CONFLUENCE
        elif not p4:
            assert reason == NO_TRIGGER


def test_ema_never_counts_by_default():
    pr = PillarResult(True, True, False, False, ema_ok=True)
    assert decide(pr, 3) == (False, 2, LOW_CONFLUENCE)  # EMA is not a 5th pillar
    pr2 = PillarResult(True, True, False, True, ema_ok=True)
    assert decide(pr2, 3, ema_counts_as_confluence=False)[1] == 3
    assert decide(pr2, 3, ema_counts_as_confluence=True)[1] == 4
    assert decide(PillarResult(False, False, False, True, ema_ok=True), 2)[0] is False


def test_ema_direction():
    assert ema_confluence(1.11, 1.10, BUY) is True
    assert ema_confluence(1.09, 1.10, BUY) is False
    assert ema_confluence(1.09, 1.10, SELL) is True
    assert ema_confluence(1.09, None, SELL) is None


def test_neutral_filter_blocks_evaluation():
    c = cfg()
    pipe = SymbolPipeline("EURUSD", c, 0.0001)
    reasons = set()
    for candle in zigzag(1.1000, [(1.1100, 30), (1.1000, 30)]):
        res = pipe.on_candle_closed("1H", candle)
        reasons.add(res.skip_reason)
        assert res.evaluation is None
    assert reasons == {NEUTRAL_FILTER}  # no HTF structure → no setups evaluated
    assert NO_AOI != NEUTRAL_FILTER
