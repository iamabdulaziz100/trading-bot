from datetime import datetime, timezone

import pytest

from app.core.trade_params import rr_slippage_abort
from app.models import BUY, SELL, TradeSignal
from app.risk.daily import DailyLossTracker, day_key
from app.risk.manager import BLOCK, PASS, RiskContext, RiskManager
from app.risk.news import NewsCalendar, NewsEvent, parse_csv, parse_ff_json
from app.symbols import SymbolSpec
from tests.helpers import cfg

T0 = int(datetime(2026, 10, 6, 12, 30, tzinfo=timezone.utc).timestamp())


def sig(symbol="EURUSD", direction=BUY, pillars=3) -> TradeSignal:
    return TradeSignal("EURUSD-1H-1", symbol, "1H", direction, 1.1000, 1.0980, 1.1040, 2.0, pillars, [], 0, 3600,
                       "1D", 1.0990, 1.1000, "BULL_ENGULF", None, True, 0.0001)


def spec() -> SymbolSpec:
    return SymbolSpec("EURUSD", 5, 0.00001, 0.0001, 0.00001, 1.0)


def ctx(**kw) -> RiskContext:
    base = dict(enabled=True, balance=10_000, equity=10_000, open_symbols=[], trades_today=0, daily_cutoff_hit=False)
    base.update(kw)
    return RiskContext(**base)


# ── daily loss ───────────────────────────────────────────────────────────────────────

def test_daily_cutoff_at_exact_boundary():
    d = DailyLossTracker(3.0)
    assert not d.update("2026-10-06", 10_000, 9_700.01)
    assert d.update("2026-10-06", 10_000, 9_700.00)  # exactly −3 % → halt
    assert d.update("2026-10-06", 10_000, 9_900.00)  # sticky for the rest of the day
    assert not d.update("2026-10-07", 9_700, 9_700)  # auto-reset at the next boundary
    assert d.start_balance == 9_700


def test_day_key_server_vs_utc():
    t = int(datetime(2026, 10, 6, 22, 30, tzinfo=timezone.utc).timestamp())
    assert day_key(t, "utc") == "2026-10-06"
    assert day_key(t, "server", 3 * 3600) == "2026-10-07"  # GMT+3 broker already in the next day


# ── news ─────────────────────────────────────────────────────────────────────────────

def news_cal() -> NewsCalendar:
    cal = NewsCalendar()
    cal.add([NewsEvent(T0, "USD", "high", "Non-Farm Payrolls"), NewsEvent(T0, "GBP", "medium", "GDP")])
    cal.last_fetch_ok = T0
    return cal


def test_news_window_blocks_correct_symbols_only():
    c = cfg().news
    cal = news_cal()
    assert cal.check("EURUSD", T0 - 30 * 60, c)[0]  # window start (inclusive)
    assert cal.check("USDJPY", T0 + 30 * 60, c)[0]  # window end (inclusive)
    assert not cal.check("EURUSD", T0 - 31 * 60, c)[0]
    assert not cal.check("EURUSD", T0 + 31 * 60, c)[0]
    assert not cal.check("EURGBP", T0, c)[0]  # GBP event is medium impact → not blocking by default
    c2 = cfg(news__impact_levels=["high", "medium"]).news
    assert cal.check("EURGBP", T0, c2)[0]


def test_news_feed_failure_modes():
    cal = NewsCalendar()  # never fetched
    assert cal.check("EURUSD", T0, cfg().news) == (False, "news feed down (feed_failure_mode=allow)")
    assert cal.check("EURUSD", T0, cfg(news__feed_failure_mode="block_all").news)[0]
    assert cal.warning(T0, cfg().news) is not None
    assert not cal.check("EURUSD", T0, cfg(news__enabled=False).news)[0]


def test_news_parsers():
    evs = parse_ff_json([{"title": "CPI m/m", "country": "USD", "date": "2026-10-06T08:30:00-04:00",
                          "impact": "High"}, {"title": "bad", "country": "USD", "date": "nope"}])
    assert len(evs) == 1 and evs[0].ts_utc == int(datetime(2026, 10, 6, 12, 30, tzinfo=timezone.utc).timestamp())
    assert evs[0].impact == "high"
    csv_evs = parse_csv("date,time_utc,currency,impact,title\n2026-10-06,12:30,usd,High,NFP\n")
    assert csv_evs[0].currency == "USD" and csv_evs[0].ts_utc == T0 and csv_evs[0].impact == "high"


# ── gates ────────────────────────────────────────────────────────────────────────────

def test_all_gates_pass_and_size():
    d = RiskManager(cfg()).evaluate(sig(), ctx(), spec())
    assert d.approved and d.lots == pytest.approx(0.5)
    assert [g.gate for g in d.gates] == ["ENABLED", "NEWS", "DAILY_LOSS", "CONCURRENCY", "SYMBOL_DUP", "SIZING"]
    assert all(g.result == PASS for g in d.gates)


@pytest.mark.parametrize("kw,gate", [
    ({"enabled": False}, "ENABLED"),
    ({"news_blocked": True, "news_reason": "USD NFP"}, "NEWS"),
    ({"daily_cutoff_hit": True}, "DAILY_LOSS"),
    ({"open_symbols": ["GBPUSD", "USDJPY", "AUDUSD"]}, "CONCURRENCY"),
    ({"open_symbols": ["EURUSD"]}, "SYMBOL_DUP"),
    ({"balance": 10, "equity": 10}, "SIZING"),
])
def test_each_gate_blocks(kw, gate):
    d = RiskManager(cfg()).evaluate(sig(), ctx(**kw), spec())
    assert not d.approved
    assert d.gates[-1].gate == gate and d.gates[-1].result == BLOCK


def test_max_trades_per_day_only_when_enabled():
    assert RiskManager(cfg()).evaluate(sig(), ctx(trades_today=50), spec()).approved
    d = RiskManager(cfg(risk__max_trades_per_day=2)).evaluate(sig(), ctx(trades_today=2), spec())
    assert not d.approved and d.gates[-1].gate == "MAX_TRADES_DAY"


def test_rr_slippage_abort():
    # planned: entry 1.1000, SL 1.0970, TP 1.1060 (2R); min_rr 2.0, tolerance 0.2 → abort below 1.8
    assert rr_slippage_abort(BUY, 1.1003, 1.0970, 1.1060, 2.0, 0.2)[0] is True  # 57/33 = 1.73
    assert rr_slippage_abort(BUY, 1.10014, 1.0970, 1.1060, 2.0, 0.2)[0] is False  # 1.80…
    ok, rr = rr_slippage_abort(SELL, 1.1000, 1.1030, 1.0940, 2.0, 0.2)
    assert not ok and rr == pytest.approx(2.0)
