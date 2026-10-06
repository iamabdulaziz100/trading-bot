import pytest

from app.core.candles import trigger_for
from app.core.trade_params import (
    TradeParams,
    TradeParamsReject,
    build_trade_params,
    entry_still_valid,
    realized_rr,
)
from app.models import BUY, INVALID_SL, MORNING_STAR, RR_CAP_REJECT, SELL, Candle

PIP = 0.0001


def test_long_sl_tp_uncapped():
    p = build_trade_params(BUY, 1.1000, 1.0985, PIP, sl_buffer_pips=15, tp_r_multiple=2.0,
                           macro_boundary=1.2000, min_rr=2.0)
    assert isinstance(p, TradeParams)
    assert p.sl == pytest.approx(1.0970)  # wick 1.0985 − 15 pips
    assert p.tp == pytest.approx(1.1060)  # 2 × 30 pips
    assert p.rr == pytest.approx(2.0) and not p.capped


def test_short_sl_tp_uncapped():
    p = build_trade_params(SELL, 1.1000, 1.1012, PIP, macro_boundary=1.0000)
    assert isinstance(p, TradeParams)
    assert p.sl == pytest.approx(1.1027) and p.tp == pytest.approx(1.0946)


def test_cap_below_min_rr_rejects():
    p = build_trade_params(BUY, 1.1000, 1.0985, PIP, tp_r_multiple=2.0, macro_boundary=1.1040, min_rr=2.0)
    assert isinstance(p, TradeParamsReject) and p.reason == RR_CAP_REJECT
    assert p.tp == pytest.approx(1.1040) and p.rr == pytest.approx(40 / 30)
    s = build_trade_params(SELL, 1.1000, 1.1015, PIP, tp_r_multiple=2.0, macro_boundary=1.0970, min_rr=2.0)
    assert isinstance(s, TradeParamsReject) and s.reason == RR_CAP_REJECT


def test_cap_that_still_meets_min_rr_is_accepted():
    p = build_trade_params(BUY, 1.1000, 1.0985, PIP, tp_r_multiple=3.0, macro_boundary=1.1075, min_rr=2.0)
    assert isinstance(p, TradeParams) and p.capped
    assert p.tp == pytest.approx(1.1075) and p.rr == pytest.approx(2.5)


def test_structure_boundary_behind_entry_rejects():
    p = build_trade_params(BUY, 1.1000, 1.0985, PIP, macro_boundary=1.0990)
    assert isinstance(p, TradeParamsReject) and p.reason == RR_CAP_REJECT


def test_invalid_sl():
    p = build_trade_params(BUY, 1.0900, 1.1000, PIP, sl_buffer_pips=15)
    assert isinstance(p, TradeParamsReject) and p.reason == INVALID_SL


def test_star_uses_formation_extreme_wick():
    impulse = Candle(0, 1.1100, 1.1105, 1.0995, 1.1000)
    middle = Candle(1, 1.1000, 1.1060, 1.0960, 1.1010)  # lowest wick of the formation
    third = Candle(2, 1.1002, 1.1065, 1.0990, 1.1060)
    sig = trigger_for([impulse, middle, third], BUY)
    assert sig is not None and sig.type == MORNING_STAR and sig.sl_ref == pytest.approx(1.0960)
    p = build_trade_params(BUY, third.close, sig.sl_ref, PIP)
    assert p.sl == pytest.approx(1.0945)


def test_realized_rr_and_zone_tolerance():
    assert realized_rr(BUY, 1.1002, 1.0970, 1.1060) == pytest.approx(58 / 32)
    assert realized_rr(SELL, 1.0998, 1.1030, 1.0940) == pytest.approx(58 / 32)
    assert entry_still_valid(BUY, 1.1015, 1.1000, 1.1010, PIP, 10)
    assert entry_still_valid(BUY, 1.1020, 1.1000, 1.1010, PIP, 10)
    assert not entry_still_valid(BUY, 1.10201, 1.1000, 1.1010, PIP, 10)
    assert entry_still_valid(SELL, 1.0990, 1.1000, 1.1010, PIP, 10)
