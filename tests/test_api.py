"""API smoke tests (no MT5 required — the bot reports MT5 as unavailable on non-Windows hosts)."""
import shutil
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.backtest.data_loader import write_canonical
from app.main import create_app
from tests.synth import hourly_df

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def client(tmp_path):
    data = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    data["server"]["data_dir"] = str(tmp_path / "data")
    data["symbols"] = ["EURUSD"]
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "bt_symbol_specs.yaml", tmp_path / "config" / "bt_symbol_specs.yaml")
    write_canonical(hourly_df(days=700, seed=7), tmp_path / "data" / "history" / "EURUSD_1H.csv")
    app = create_app(cfg_path, start_bot=False, db_path=tmp_path / "bot.db", log_dir=tmp_path / "logs")
    with TestClient(app) as c:
        yield c


def test_status_and_basic_reads(client):
    st = client.get("/api/status").json()
    assert set(st) >= {"mt5", "bot", "daily", "positions", "news", "symbols", "warnings", "server_time_utc"}
    assert st["symbols"][0]["symbol"] == "EURUSD"
    assert client.get("/api/symbols").json()["execution_tf"] == "1H"
    assert client.get("/api/trades").json() == {"items": [], "total": 0, "page": 1, "page_size": 50}
    assert client.get("/api/signals?is_backtest=0").json()["total"] == 0
    assert client.get("/api/positions").json() == {"positions": []}
    assert client.get("/api/account").status_code == 503
    assert client.get("/api/logs?level=DEBUG&tail=5").status_code == 200


def test_error_format(client):
    r = client.get("/api/backtests/999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = client.post("/api/backtests", json={"symbols": ["EURUSD"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_config_put_validation(client):
    cfg = client.get("/api/config").json()
    cfg["risk"]["risk_per_trade_pct"] = 9
    r = client.put("/api/config", json=cfg)
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["path"] == "risk.risk_per_trade_pct"
    cfg["risk"]["risk_per_trade_pct"] = 0.5
    cfg["structure"]["pivot_window_n"] = 6
    r = client.put("/api/config", json=cfg).json()
    assert r["ok"] and r["rebuild_required"] and not r["restart_required"]
    assert client.get("/api/status").json()["bot"]["rebuild_required"]
    assert client.post("/api/structure/rebuild").json()["ok"]


def test_bot_toggle(client):
    assert client.post("/api/bot/disable").json() == {"enabled": False}
    assert client.get("/api/status").json()["bot"]["enabled"] is False
    assert client.post("/api/bot/enable").json() == {"enabled": True}


def test_offline_charts_from_history(client):
    c = client.get("/api/candles?symbol=EURUSD&tf=1D&limit=50").json()
    assert c["pip_size"] == 0.0001 and len(c["candles"]) == 50
    o = client.get("/api/overlays?symbol=EURUSD&tf=1H").json()
    assert o["structure"]["state"] in ("BULLISH", "BEARISH", "UNDEFINED") and o["swings"] and o["ema"]


def test_news_csv_upload(client):
    csv = b"date,time_utc,currency,impact,title\n2099-01-01,12:30,USD,high,Test event\n"
    assert client.post("/api/news/upload_csv", files={"file": ("n.csv", csv, "text/csv")}).json() == {"imported": 1}
    evs = client.get("/api/news/events?from=2098-12-31&to=2099-01-02").json()["events"]
    assert evs[0]["title"] == "Test event" and evs[0]["source"] == "csv"


def test_backtest_lifecycle(client):
    files = client.get("/api/backtests/data").json()["files"]
    assert files[0]["symbol"] == "EURUSD" and files[0]["tf"] == "1H"
    r = client.post("/api/backtests", json={"symbols": ["EURUSD"], "date_from": "2023-10-01",
                                            "date_to": "2024-11-30", "initial_balance": 10000})
    run_id = r.json()["id"]
    for _ in range(300):
        d = client.get(f"/api/backtests/{run_id}").json()
        if d["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert d["status"] == "done", d.get("error")
    assert d["params"]["symbols"] == ["EURUSD"] and d["config_hash"]
    assert d["metrics"]["trades"] == len(d["trades"]) > 0
    assert d["equity"][0]["equity"] == 10000
    assert any("resampled" in n for n in d["notes"])
    csv = client.get(f"/api/backtests/{run_id}/trades.csv").text
    assert csv.splitlines()[0].startswith("signal_id,symbol,direction")
    bt = client.get(f"/api/trades?is_backtest=1&backtest_run_id={run_id}").json()
    assert bt["total"] == d["metrics"]["trades"]
    assert client.get("/api/backtests").json()["items"][0]["id"] == run_id
