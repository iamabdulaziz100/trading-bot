"""Live trading path against an in-memory fake MT5 (connector, warm-up, polling, risk,
execution with filling fallback + SL/TP verification, position monitor, idempotency)."""
import asyncio
import shutil
import time
from pathlib import Path

import pytest
import yaml

from app.api.ws import WSHub
from app.bot import TradingBot
from app.config import ConfigManager
from app.db.journal import Journal
from app.mt5 import connector
from app.mt5.execution import DUPLICATE
from tests.fake_mt5 import FakeMT5
from tests.synth import hourly_df, multi_tf

ROOT = Path(__file__).resolve().parent.parent
START = 1701388800  # 2023-12-01T00:00:00Z
DAYS = 150


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    data["symbols"] = ["EURUSD"]
    data["server"]["data_dir"] = str(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    clock = {"t": float(START)}
    monkeypatch.setattr(time, "time", lambda: clock["t"])
    return tmp_path, clock


def run_bot(tmp_path, clock, fake: FakeMT5, days: int = DAYS):
    cm = ConfigManager(tmp_path / "config.yaml", load_env=False)
    journal = Journal(tmp_path / "bot.db")

    async def scenario():
        bot = TradingBot(cm, journal, WSHub())
        bot.hub.bind(asyncio.get_running_loop())
        bot._running = True
        assert await bot._ensure_connected()
        pipe = bot.pipelines["EURUSD"]
        for step in range(24 * days):
            clock["t"] += 3600
            fake.on_clock()
            for tf, c in await bot._blocking(bot._fetch_new_bars, "EURUSD", pipe):
                await bot._handle_step(pipe.on_candle_closed(tf, c))
            if step % 4 == 0:
                bot.positions_view, _ = await bot._blocking(bot.monitor.sync)
                await bot._update_daily()
        bot._running = False
        bot.pool.shutdown(wait=True)
        return bot

    return asyncio.run(scenario()), journal


def make_fake(clock, **kw) -> FakeMT5:
    return FakeMT5({"EURUSD": multi_tf(hourly_df(days=700, seed=7))}, clock=lambda: clock["t"], **kw)


def test_live_cycle_end_to_end(env, monkeypatch):
    tmp_path, clock = env
    fake = make_fake(clock, reject_fok=True)
    monkeypatch.setattr(connector, "mt5", fake)
    bot, journal = run_bot(tmp_path, clock, fake)

    assert bot.conn.server_offset == 7200  # broker offset detected and removed
    assert bot.warmed_up and not bot.halted
    pipe = bot.pipelines["EURUSD"]
    assert len(pipe.series["1H"]) > 3000
    # candles are UTC: the last processed 1H bar closed at or before "now"
    assert pipe.last_time("1H") + 3600 <= clock["t"]

    signals, n_sig = journal.list_signals({}, 1, 500)
    assert n_sig > 0
    executed = [s for s in signals if s["decision"] == "EXECUTED"]
    assert executed, "expected at least one executed trade"
    trades = journal.all_trades({"is_backtest": 0})
    assert all(t["status"] in ("OPEN", "CLOSED") for t in trades)
    closed = [t for t in trades if t["status"] == "CLOSED"]
    assert closed and all(t["exit_reason"] in ("SL", "TP") and t["pnl_money"] is not None for t in closed)
    assert all(t["r_multiple"] is not None for t in closed)
    # every order carried SL+TP (bracket), FOK was rejected → IOC fallback used
    deals = [r for r in fake.requests if r["action"] == 1 and "position" not in r]
    assert deals and all(r["sl"] and r["tp"] for r in deals)
    assert any(r.get("type_filling") == 1 for r in deals)
    # set & forget: the bot never modified a position after placement
    assert not [r for r in fake.requests if r["action"] == 6]
    # one trade per symbol at a time
    opens = sorted((t["entry_time"], t["exit_time"] or "9999") for t in trades)
    assert all(a[1] <= b[0] for a, b in zip(opens, opens[1:]))
    # DB open trades mirror the broker
    assert {t["ticket"] for t in trades if t["status"] == "OPEN"} == set(fake.positions)
    st = bot.status()
    assert st["mt5"]["connected"] and st["account"]["currency"] == "USD"


def test_sltp_missing_after_fill_is_modified_once(env, monkeypatch):
    tmp_path, clock = env
    fake = make_fake(clock, drop_sltp_once=True)
    monkeypatch.setattr(connector, "mt5", fake)
    bot, journal = run_bot(tmp_path, clock, fake)
    modifies = [r for r in fake.requests if r["action"] == 6]
    assert len(modifies) == 1  # the first fill landed naked → exactly one order_modify
    assert all(p["sl"] and p["tp"] for p in fake.positions.values())


def test_idempotent_execution(env, monkeypatch):
    tmp_path, clock = env
    fake = make_fake(clock)
    monkeypatch.setattr(connector, "mt5", fake)
    bot, journal = run_bot(tmp_path, clock, fake, days=60)
    trades = journal.all_trades({"is_backtest": 0})
    if not trades:
        pytest.skip("no trade in window")
    from app.models import TradeSignal
    from app.risk.manager import RiskDecision

    t = trades[0]
    sig = TradeSignal(t["signal_id"], "EURUSD", "1H", t["direction"], t["planned_entry"], t["sl"], t["tp"], 2.0, 3, [],
                      0, int(clock["t"]), "1D", 0.0, 9.9, "BULL_ENGULF", None, True, 0.0001)
    fake.positions.clear()
    res = bot.execution.execute(sig, RiskDecision(True, 0.1, 10.0), bot.specs["EURUSD"], clock["t"])
    assert res.status == DUPLICATE
    shutil.rmtree(tmp_path / "history", ignore_errors=True)


def test_parity_live_vs_backtest(env, monkeypatch):
    """B2 (M5 exit criterion) on synthetic data: the live path (fake broker) and the backtester
    must take the same trades — same signal, direction, SL/TP within 2 pips — for ≥ 90 %."""
    from datetime import datetime, timezone

    from app.backtest.simulator import BacktestEngine, BTParams
    from app.symbols import default_bt_spec
    from app.timeframes import TF_SECONDS, WARMUP_BARS
    from tests.helpers import cfg

    tmp_path, clock = env
    fake = make_fake(clock, spread=0.00008)
    monkeypatch.setattr(connector, "mt5", fake)
    _, journal = run_bot(tmp_path, clock, fake)
    live = {t["signal_id"]: t for t in journal.all_trades({"is_backtest": 0})}

    full = multi_tf(hourly_df(days=700, seed=7))
    end = START + DAYS * 86400
    per_tf = {tf: [c for c in cs if c.time + TF_SECONDS[tf] <= START][-WARMUP_BARS[tf]:]
              + [c for c in cs if c.time + TF_SECONDS[tf] > START and c.time <= end] for tf, cs in full.items()}
    spec = default_bt_spec("EURUSD")
    spec.spread_pips = 0.8
    day = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    res = BacktestEngine(cfg(), BTParams(["EURUSD"], day(START), day(end - 86400), slippage_pips=0.0),
                         {"EURUSD": per_tf}, {"EURUSD": spec}).run()
    bt = {t["signal_id"]: t for t in res["trades"]}
    assert live and bt
    matched = [s for s in live if s in bt and live[s]["direction"] == bt[s]["direction"]
               and abs(live[s]["sl"] - bt[s]["sl"]) <= 2 * 0.0001 and abs(live[s]["tp"] - bt[s]["tp"]) <= 2 * 0.0001]
    assert len(matched) >= 0.9 * max(len(live), len(bt))
