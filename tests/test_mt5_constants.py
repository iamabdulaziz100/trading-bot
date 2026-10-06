"""Runs only where the official MetaTrader5 package is installed (Windows / CI): every constant
and timeframe name the connector relies on must exist with the expected value."""
import pytest

mt5 = pytest.importorskip("MetaTrader5")

from app.mt5 import connector as C  # noqa: E402

EXPECTED = {
    "TRADE_ACTION_DEAL": 1, "TRADE_ACTION_SLTP": 6, "ORDER_TYPE_BUY": 0, "ORDER_TYPE_SELL": 1,
    "ORDER_TIME_GTC": 0, "ORDER_FILLING_FOK": 0, "ORDER_FILLING_IOC": 1, "ORDER_FILLING_RETURN": 2,
    "POSITION_TYPE_BUY": 0, "DEAL_ENTRY_IN": 0,
    "DEAL_ENTRY_OUT": 1, "DEAL_ENTRY_INOUT": 2, "DEAL_ENTRY_OUT_BY": 3, "DEAL_REASON_CLIENT": 0,
    "DEAL_REASON_MOBILE": 1, "DEAL_REASON_WEB": 2, "DEAL_REASON_EXPERT": 3, "DEAL_REASON_SL": 4,
    "DEAL_REASON_TP": 5, "DEAL_REASON_SO": 6,
    "TRADE_RETCODE_REQUOTE": C.RET_REQUOTE, "TRADE_RETCODE_PLACED": C.RET_PLACED, "TRADE_RETCODE_DONE": C.RET_DONE,
    "TRADE_RETCODE_DONE_PARTIAL": C.RET_DONE_PARTIAL, "TRADE_RETCODE_INVALID_STOPS": C.RET_INVALID_STOPS,
    "TRADE_RETCODE_MARKET_CLOSED": C.RET_MARKET_CLOSED, "TRADE_RETCODE_NO_MONEY": C.RET_NO_MONEY,
    "TRADE_RETCODE_PRICE_CHANGED": C.RET_PRICE_CHANGED, "TRADE_RETCODE_PRICE_OFF": C.RET_PRICE_OFF,
    "TRADE_RETCODE_INVALID_FILL": C.RET_INVALID_FILL, "TRADE_RETCODE_CONNECTION": C.RET_CONNECTION,
    "TRADE_RETCODE_TIMEOUT": C.RET_TIMEOUT, "TRADE_RETCODE_TRADE_DISABLED": C.RET_TRADE_DISABLED,
}


@pytest.mark.parametrize("name,value", sorted(EXPECTED.items()))
def test_constant_matches_package(name, value):
    assert getattr(mt5, name) == value


def test_symbol_filling_flags_fallback():
    """The Python package does not export the SYMBOL_FILLING_* flags (verified on Windows CI); the
    connector uses the documented MQL5 values SYMBOL_FILLING_FOK=1, SYMBOL_FILLING_IOC=2."""
    assert getattr(mt5, "SYMBOL_FILLING_FOK", 1) == C.SYMBOL_FILLING_FOK == 1
    assert getattr(mt5, "SYMBOL_FILLING_IOC", 2) == C.SYMBOL_FILLING_IOC == 2


@pytest.mark.parametrize("tf,name", sorted(C.TF_MAP_NAMES.items()))
def test_timeframe_names_exist(tf, name):
    assert isinstance(getattr(mt5, name), int)


def test_api_functions_exist():
    for fn in ("initialize", "shutdown", "last_error", "terminal_info", "account_info", "symbol_select",
               "symbol_info", "symbol_info_tick", "copy_rates_from_pos", "positions_get", "order_send",
               "history_deals_get"):
        assert callable(getattr(mt5, fn)), fn
