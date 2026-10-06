import pytest

from app.risk.sizing import RISK_INVALID_SL, RISK_SKIP_UNDER_MIN_LOT, compute_lots, floor_to_step, risk_pct_for
from app.symbols import SymbolSpec, default_bt_spec
from tests.helpers import cfg


def eurusd() -> SymbolSpec:
    return SymbolSpec("EURUSD", 5, 0.00001, 0.0001, tick_size=0.00001, tick_value=1.0)


def usdjpy(price: float = 150.0) -> SymbolSpec:
    return SymbolSpec("USDJPY", 3, 0.001, 0.01, tick_size=0.001, tick_value=100000 * 0.001 / price)


def test_eurusd_units_formula():
    r = compute_lots(10_000, 1.0, 1.1000, 1.0980, eurusd())  # 20 pips, $10/pip/lot
    assert r.ok and r.lots == pytest.approx(0.50) and r.risk_money == pytest.approx(100.0)


def test_usdjpy_known_tick_value():
    r = compute_lots(10_000, 1.0, 150.00, 149.70, usdjpy())  # 30 pips, ≈ $6.67/pip/lot
    assert r.ok and r.lots == pytest.approx(0.50)


def test_floor_to_lot_step_never_rounds_up():
    r = compute_lots(10_000, 1.0, 1.1000, 1.0979, eurusd())  # raw 0.476 lots
    assert r.lots == pytest.approx(0.47)
    assert floor_to_step(0.4999999, 0.01) == pytest.approx(0.49)
    assert floor_to_step(0.5, 0.01) == pytest.approx(0.50)
    assert floor_to_step(1.37, 0.1) == pytest.approx(1.3)


def test_under_min_lot_skips():
    r = compute_lots(50, 1.0, 1.1000, 1.0900, eurusd())  # $0.50 risk over 100 pips
    assert not r.ok and r.reason == RISK_SKIP_UNDER_MIN_LOT and r.lots == 0


def test_invalid_sl_and_max_lot_clamp():
    assert compute_lots(10_000, 1.0, 1.1, 1.1, eurusd()).reason == RISK_INVALID_SL
    spec = eurusd()
    spec.max_lot = 2.0
    r = compute_lots(10_000_000, 1.0, 1.1000, 1.0980, spec)
    assert r.lots == 2.0


def test_pillar_risk_scaling():
    c = cfg()
    assert risk_pct_for(4, c.risk) == 1.0
    c2 = cfg(risk__pillar_risk_scaling=True)
    assert risk_pct_for(3, c2.risk) == 1.0 and risk_pct_for(4, c2.risk) == 1.5


def test_backtest_spec_auto_pip_values():
    eu = default_bt_spec("EURUSD")
    assert eu.pip_value(1.1) == pytest.approx(10.0)
    uj = default_bt_spec("USDJPY")
    assert uj.pip_value(150.0) == pytest.approx(1000 / 150)
    s = uj.to_symbol_spec(150.0)
    r = compute_lots(10_000, 1.0, 150.00, 149.70, s)
    assert r.lots == pytest.approx(0.50)
