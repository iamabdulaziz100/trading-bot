import pytest

from app.pip_engine import PipEngineError, instrument_class, is_v1_tradeable, pip_from_digits, pip_size, split_fx


def test_class_pips():
    assert pip_size("USDJPY") == 0.01
    assert pip_size("EURUSD") == 0.0001
    assert pip_size("XAUUSD") == 0.10
    assert pip_size("US30") == 1.00
    assert pip_size("SPX500") == 1.00


def test_digits_derivation():
    assert pip_from_digits(5, 0.00001) == pytest.approx(0.0001)
    assert pip_from_digits(4, 0.0001) == pytest.approx(0.0001)
    assert pip_from_digits(3, 0.001) == pytest.approx(0.01)
    assert pip_from_digits(2, 0.01) == pytest.approx(0.01)
    assert pip_size("EURUSD", 5, 0.00001) == 0.0001
    assert pip_size("USDJPY", 3, 0.001) == 0.01


def test_unknown_layout_refused():
    with pytest.raises(PipEngineError):
        pip_size("BTCUSD")
    with pytest.raises(PipEngineError):
        pip_size("EURUSD", 1, 0.1)  # unsupported digits
    with pytest.raises(PipEngineError):
        pip_size("EURUSD", 3, 0.001)  # digits say JPY-style but pair is not JPY-quoted


def test_broker_suffixes_and_scope():
    assert split_fx("EURUSDm") == ("EUR", "USD")
    assert split_fx("GBPJPY.pro") == ("GBP", "JPY")
    assert pip_size("EURJPY#") == 0.01
    assert instrument_class("XAUUSD") == "METAL"
    assert is_v1_tradeable("EURGBP")
    assert not is_v1_tradeable("XAUUSD")
    assert not is_v1_tradeable("US30")
