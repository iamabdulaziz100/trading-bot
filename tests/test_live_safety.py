"""Safety regressions for the live path (fake MT5): ambiguous replies, naked positions, offset
detection, PENDING recovery, panic."""
import asyncio
import time
from collections import Counter
from pathlib import Path

import pytest
import yaml

from app.api.ws import WSHub
from app.bot import TradingBot
from app.config import ConfigManager
from app.db.journal import Journal
from app.mt5 import connector
from tests.test_live_fake_mt5 import START, make_fake, run_bot

ROOT = Path(__file__).resolve().parent.parent
MAGIC = 20260922


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    data["symbols"] = ["EURUSD"]
    data["server"]["data_dir"] = str(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    clock = {"t": float(START)}
    monkeypatch.setattr(time, "time", lambda: clock["t"])
    monkeypatch.delenv("MSC_MT5_SERVER_OFFSET_HOURS", raising=False)
    return tmp_path, clock


def bot_with(tmp_path, fake, monkeypatch) -> TradingBot:
    monkeypatch.setattr(connector, "mt5", fake)
    bot = TradingBot(ConfigManager(tmp_path / "config.yaml", load_env=False), Journal(tmp_path / "bot.db"), WSHub())
    assert bot.conn.connect(1)
    return bot


def test_ambiguous_timeout_reply_never_duplicates(env, monkeypatch):
    tmp_path, clock = env
    fake = make_fake(clock, ambiguous_once=True)
    monkeypatch.setattr(connector, "mt5", fake)
    _, journal = run_bot(tmp_path, clock, fake)
    opens = Counter(d.comment for d in fake.deals if d.entry == 0)
    assert opens and all(n == 1 for n in opens.values()), opens  # one execution per signal
    first = journal.all_trades({"is_backtest": 0})[0]
    assert first["status"] in ("OPEN", "CLOSED") and first["ticket"]  # recognised as executed, not FAILED


def test_safety_sweep_attaches_missing_bracket(env, monkeypatch):
    tmp_path, clock = env
    fake = make_fake(clock)
    bot = bot_with(tmp_path, fake, monkeypatch)
    t = fake.open_naked("EURUSD", comment="MSC|EURUSD-1H-1")
    bot.journal.insert_trade({"signal_id": "EURUSD-1H-1", "symbol": "EURUSD", "direction": "BUY", "status": "OPEN",
                              "ticket": t, "sl": 1.0, "tp": 2.0, "risk_money": 10.0, "is_backtest": 0})
    bot.monitor.sync()
    assert fake.positions[t]["sl"] == 1.0 and fake.positions[t]["tp"] == 2.0  # bracket attached
    assert not bot.monitor.critical


def test_safety_sweep_closes_when_attach_fails(env, monkeypatch):
    tmp_path, clock = env
    fake = make_fake(clock, reject_modify=True)
    bot = bot_with(tmp_path, fake, monkeypatch)
    t = fake.open_naked("EURUSD", comment="MSC|EURUSD-1H-2")
    bot.journal.insert_trade({"signal_id": "EURUSD-1H-2", "symbol": "EURUSD", "direction": "BUY", "status": "OPEN",
                              "ticket": t, "sl": 1.0, "tp": 2.0, "risk_money": 10.0, "is_backtest": 0})
    bot.monitor.sync()  # attach attempt (rejected)
    assert t in fake.positions
    bot.monitor.sync()  # still naked → closed
    assert t not in fake.positions
    bot.monitor.sync()  # close recorded
    tr = bot.journal.get_trade("EURUSD-1H-2")
    assert tr["status"] == "CLOSED" and tr["exit_reason"] == "SAFETY_CLOSE"


def test_unknown_naked_position_is_adopted_and_closed(env, monkeypatch):
    tmp_path, clock = env
    fake = make_fake(clock)
    bot = bot_with(tmp_path, fake, monkeypatch)
    t = fake.open_naked("EURUSD", comment="")
    bot.monitor.reconcile_startup()  # no journaled bracket to attach → close immediately
    assert t not in fake.positions
    assert bot.journal.trade_by_ticket(t)["signal_id"] == f"RECOVERED-{t}"


def test_pending_row_recovered_from_deal_history(env, monkeypatch):
    tmp_path, clock = env
    fake = make_fake(clock)
    bot = bot_with(tmp_path, fake, monkeypatch)
    bot.journal.insert_trade({"signal_id": "EURUSD-1H-9", "symbol": "EURUSD", "direction": "BUY", "status": "PENDING",
                              "sl": 1.0, "tp": 2.0, "risk_money": 50.0, "is_backtest": 0})
    # the order executed and was closed while the bot was down
    fake.order_send({"action": 1, "symbol": "EURUSD", "volume": 0.1, "type": 0, "price": 1.1, "sl": 1.0, "tp": 2.0,
                     "magic": MAGIC, "comment": "MSC|EURUSD-1H-9", "type_time": 0, "type_filling": 0})
    ticket = max(fake.positions)
    fake._close(fake.positions[ticket], 1.1050, 5)
    bot.monitor.reconcile_startup()
    assert bot.journal.get_trade("EURUSD-1H-9")["status"] == "OPEN"
    bot.monitor.sync()
    tr = bot.journal.get_trade("EURUSD-1H-9")
    assert tr["status"] == "CLOSED" and tr["exit_reason"] == "TP" and tr["pnl_money"] == pytest.approx(50.0)


def test_offset_detection_rules(env, monkeypatch):
    tmp_path, clock = env
    fake = make_fake(clock, offset=7200)
    bot = bot_with(tmp_path, fake, monkeypatch)
    conn = bot.conn
    assert conn.detect_server_offset(["EURUSD"], wait=2) == 7200
    # weekend: ticks frozen → no reading, offset kept
    clock["t"] = START + 86400 + 12 * 3600  # Saturday noon
    assert conn.detect_server_offset(["EURUSD"], wait=1) is None and conn.server_offset == 7200
    clock["t"] = START  # market open again
    fake.offset = 10800  # DST: +1 h accepted directly
    assert conn.detect_server_offset(["EURUSD"], wait=2) == 10800
    fake.offset = 0  # implausible jump: needs a second confirming reading
    assert conn.detect_server_offset(["EURUSD"], wait=2) is None and conn.server_offset == 10800
    assert conn.detect_server_offset(["EURUSD"], wait=2) == 0


def test_offset_override_env(env, monkeypatch):
    tmp_path, clock = env
    monkeypatch.setenv("MSC_MT5_SERVER_OFFSET_HOURS", "3")
    fake = make_fake(clock, offset=7200)
    bot = bot_with(tmp_path, fake, monkeypatch)
    assert bot.conn.detect_server_offset(["EURUSD"], wait=1) == 10800


def test_panic_closes_all_bot_positions_only(env, monkeypatch):
    tmp_path, clock = env
    fake = make_fake(clock)
    bot = bot_with(tmp_path, fake, monkeypatch)
    a = fake.open_naked("EURUSD", comment="MSC|a")
    b = fake.open_naked("EURUSD", comment="MSC|b")
    manual = fake.open_naked("EURUSD", magic=1, comment="manual")
    res = asyncio.run(bot.panic())
    assert sorted(res["closed"]) == sorted([a, b]) and res["failed"] == [] and res["enabled"] is False
    assert list(fake.positions) == [manual] and bot.enabled is False
