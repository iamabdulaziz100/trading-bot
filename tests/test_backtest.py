"""Backtest acceptance (08 §3): B1 no-lookahead, B3 metrics, B4 pessimism, B5 spec fidelity."""
from pathlib import Path

import numpy as np
import pytest

from app.backtest.data_loader import parse_csv_bytes, resample
from app.backtest.metrics import compute_metrics
from app.backtest.simulator import BacktestEngine, BTParams, BTState, SimPosition
from app.core.pipeline import SymbolPipeline, merge_candle_events
from app.models import BUY, SELL, TradeSignal
from app.symbols import default_bt_spec
from app.timeframes import TF_SECONDS
from tests.helpers import cfg
from tests.synth import hourly_df, multi_tf

ROOT = Path(__file__).resolve().parent.parent


# ── B1: no lookahead ─────────────────────────────────────────────────────────────────────

def _evaluations(data: dict, cutoff: int) -> list[tuple]:
    pipe = SymbolPipeline("EURUSD", cfg(), 0.0001)
    out = []
    for tf, c in merge_candle_events(data, "EURUSD"):
        if c.time + TF_SECONDS[tf] > cutoff:
            break
        res = pipe.on_candle_closed(tf, c)
        if res.evaluation:
            e = res.evaluation
            out.append((e.signal_id, e.decision, e.reason, e.pillar_count, e.candle_signal, e.pattern,
                        e.signal and round(e.signal.sl, 6), e.signal and round(e.signal.tp, 6)))
    return out


def test_no_lookahead_future_data_cannot_change_past_decisions():
    df = hourly_df(days=700, seed=7)  # evaluations occur on both sides of the cutoff
    cutoff = 1713139200  # 2024-04-15T00:00:00Z
    base = _evaluations(multi_tf(df), cutoff)
    assert len(base) > 20  # the scenario actually exercises the checklist
    rng = np.random.default_rng(99)
    df2 = df.copy()
    future = df2["time"] >= cutoff
    noise = rng.normal(0, 0.003, future.sum())
    for col in ("open", "high", "low", "close"):
        df2.loc[future, col] = df2.loc[future, col] + noise
    df2.loc[future, "high"] = df2.loc[future, ["open", "high", "low", "close"]].max(axis=1)
    df2.loc[future, "low"] = df2.loc[future, ["open", "high", "low", "close"]].min(axis=1)
    assert _evaluations(multi_tf(df2), cutoff) == base


def test_htf_candles_revealed_only_after_close():
    df = hourly_df(days=60, seed=3)
    events = merge_candle_events(multi_tf(df))
    closes = [c.time + TF_SECONDS[tf] for tf, c in events]
    assert closes == sorted(closes)
    for i in range(1, len(events)):  # at equal close time the higher TF goes first
        if closes[i] == closes[i - 1]:
            from app.timeframes import TF_RANK
            assert TF_RANK[events[i - 1][0]] >= TF_RANK[events[i][0]]


# ── B3: metrics on a hand-verified 5-trade dataset ───────────────────────────────────────

def test_metrics_hand_verified():
    day = 86400
    t0 = 1_700_000_000
    trades = [
        {"exit_ts": t0 + 1 * day, "pnl_money": 200.0, "r_multiple": 2.0, "symbol": "EURUSD", "pillars": 3, "rr_realized": 2.0},
        {"exit_ts": t0 + 2 * day, "pnl_money": -100.0, "r_multiple": -1.0, "symbol": "EURUSD", "pillars": 3, "rr_realized": 2.0},
        {"exit_ts": t0 + 3 * day, "pnl_money": -100.0, "r_multiple": -1.0, "symbol": "USDJPY", "pillars": 4, "rr_realized": 2.0},
        {"exit_ts": t0 + 6 * day, "pnl_money": 300.0, "r_multiple": 3.0, "symbol": "USDJPY", "pillars": 4, "rr_realized": 3.0},
        {"exit_ts": t0 + 8 * day, "pnl_money": -50.0, "r_multiple": -0.5, "symbol": "EURUSD", "pillars": 3, "rr_realized": 2.0},
    ]
    out = compute_metrics(trades, 10_000, t0, t0 + 9 * day, rejected_signals=7)
    m = out["metrics"]
    assert m["trades"] == 5 and m["wins"] == 2 and m["losses"] == 3
    assert m["win_rate"] == 0.4
    assert m["net_pnl"] == 250.0 and m["net_pnl_pct"] == 2.5
    assert m["gross_profit"] == 500.0 and m["gross_loss"] == -250.0
    assert m["profit_factor"] == 2.0
    assert m["expectancy_r"] == 0.5
    assert m["avg_rr_realized"] == 2.2
    assert m["max_drawdown_money"] == 200.0  # 10 200 → 10 000
    assert m["max_drawdown_pct"] == pytest.approx(200 / 10_200 * 100, abs=1e-4)
    assert m["max_drawdown_duration_days"] == 5.0  # peak day 1 → recovered day 6
    assert m["final_balance"] == 10_250.0
    assert m["pillars_distribution"] == {"3": 3, "4": 2}
    assert m["rejected_signals"] == 7
    per = {r["symbol"]: r for r in out["per_symbol"]}
    assert per["EURUSD"]["trades"] == 3 and per["EURUSD"]["net_pnl"] == 50.0
    assert per["USDJPY"]["win_rate"] == 0.5 and per["USDJPY"]["sum_r"] == 2.0
    assert [p["equity"] for p in out["equity"]] == [10_000, 10_200, 10_100, 10_000, 10_300, 10_250]


# ── B4: pessimism & gap rules ────────────────────────────────────────────────────────────

def _engine():
    return BacktestEngine(cfg(), BTParams(["EURUSD"], "2024-01-01", "2024-01-31"), {},
                          {"EURUSD": default_bt_spec("EURUSD")})


def _pos(direction: str) -> SimPosition:
    if direction == BUY:
        sig = TradeSignal("s", "EURUSD", "1H", BUY, 1.1000, 1.0970, 1.1060, 2.0, 3, [], 0, 3600, "1D", 1.0990,
                          1.1000, "BULL_ENGULF", None, True, 0.0001)
    else:
        sig = TradeSignal("s", "EURUSD", "1H", SELL, 1.1000, 1.1030, 1.0940, 2.0, 3, [], 0, 3600, "1D", 1.1000,
                          1.1010, "BEAR_ENGULF", None, True, 0.0001)
    return SimPosition(sig, 1.0, 300.0, 0, 1.1000, 2.0, default_bt_spec("EURUSD"), 0.0001, 0.00005, 0.0, 0, 3)


def _exit(direction: str, o, h, l, c):  # noqa: E741
    from app.models import Candle

    eng, st = _engine(), BTState(balance=10_000)
    st.positions["EURUSD"] = _pos(direction)
    eng._on_exec_candle(st, "EURUSD", Candle(7200, o, h, l, c))
    return st.trades[-1] if st.trades else None


def test_candle_spanning_sl_and_tp_hits_sl_first():
    t = _exit(BUY, 1.1010, 1.1070, 1.0960, 1.1000)
    assert t["exit_reason"] == "SL" and t["exit_price"] == pytest.approx(1.0970 - 0.00005)
    s = _exit(SELL, 1.0990, 1.1040, 1.0930, 1.1000)
    assert s["exit_reason"] == "SL" and s["exit_price"] == pytest.approx(1.1030 + 0.00005)


def test_gap_fills_at_open():
    t = _exit(BUY, 1.0950, 1.0960, 1.0940, 1.0955)
    assert t["exit_reason"] == "SL" and t["exit_price"] == pytest.approx(1.0950 - 0.00005)
    t = _exit(BUY, 1.1080, 1.1090, 1.1075, 1.1085)
    assert t["exit_reason"] == "TP" and t["exit_price"] == pytest.approx(1.1080)
    s = _exit(SELL, 1.0920, 1.0925, 1.0910, 1.0915)  # ask open 1.0921 ≤ TP
    assert s["exit_reason"] == "TP" and s["exit_price"] == pytest.approx(1.0921)


def test_tp_only_and_no_exit():
    t = _exit(BUY, 1.1010, 1.1065, 1.0990, 1.1050)
    assert t["exit_reason"] == "TP" and t["pnl_money"] == pytest.approx(600.0)  # 60 pips × $10 × 1 lot
    assert _exit(BUY, 1.1010, 1.1030, 1.0990, 1.1020) is None


# ── B5: spec fidelity ────────────────────────────────────────────────────────────────────

def test_no_is_backtest_branches_in_strategy_engines():
    core = ROOT / "app" / "core"
    offenders = [p.name for p in core.glob("*.py")
                 if "is_backtest" in p.read_text(encoding="utf-8") or "app.backtest" in p.read_text(encoding="utf-8") or "app.mt5" in p.read_text(encoding="utf-8")]
    assert offenders == []


# ── end-to-end engine run on synthetic data ──────────────────────────────────────────────

def test_engine_end_to_end():
    df = hourly_df(days=700, seed=7)
    data = {"EURUSD": multi_tf(df)}
    params = BTParams(["EURUSD"], "2023-10-01", "2024-11-30")
    res = BacktestEngine(cfg(), params, data, {"EURUSD": default_bt_spec("EURUSD")}).run()
    m = res["metrics"]
    assert m["trades"] == len(res["trades"]) > 0
    assert m["final_balance"] == pytest.approx(m["start_balance"] + m["net_pnl"], abs=0.05)
    assert {t["exit_reason"] for t in res["trades"]} <= {"SL", "TP", "RR_SLIPPAGE_ABORT", "END_OF_TEST"}
    assert all(t["pillars"] >= 3 for t in res["trades"])
    assert res["rejected"] and all(r["decision"] == "REJECTED" for r in res["rejected"])
    # one trade per symbol at a time
    spans = sorted((t["entry_time"], t["exit_time"]) for t in res["trades"])
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))


def test_csv_formats_and_resample():
    canonical = "time_utc,open,high,low,close\n2024-01-01T00:00:00Z,1.1,1.2,1.0,1.15\n"
    df = parse_csv_bytes(canonical)
    assert int(df["time"].iloc[0]) == 1704067200
    mt5 = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\n2024.01.01\t02:00:00\t1.1\t1.2\t1.0\t1.15\t10\n"
    df2 = parse_csv_bytes(mt5, tz_offset_hours=2)
    assert int(df2["time"].iloc[0]) == 1704067200  # server GMT+2 → UTC
    duka = "Gmt time,Open,High,Low,Close,Volume\n01.01.2024 00:00:00.000,1.1,1.2,1.0,1.15,5\n"
    assert int(parse_csv_bytes(duka)["time"].iloc[0]) == 1704067200
    epoch = "time_utc,open,high,low,close\n1704067200,1.1,1.2,1.0,1.15\n"
    assert int(parse_csv_bytes(epoch)["time"].iloc[0]) == 1704067200
    h = hourly_df(days=21, seed=1)
    w = resample(h, "1W")
    assert all(((t // 86400) + 4) % 7 == 0 for t in w["time"])  # weeks open on Sunday 00:00 UTC
    d = resample(h, "1D")
    assert d["high"].max() == h["high"].max() and d["low"].min() == h["low"].min()
