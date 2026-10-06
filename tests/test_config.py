import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import (
    PASSWORD_MASK,
    BotConfig,
    ConfigManager,
    config_schema,
    config_warnings,
    diff_flags,
    load_config_file,
)
from tests.helpers import cfg

ROOT = Path(__file__).resolve().parent.parent


def test_config_yaml_equals_schema_defaults():
    assert load_config_file(ROOT / "config.yaml") == BotConfig()


@pytest.mark.parametrize("path,value", [
    ("aoi__min_width_pips", 60),  # min_width < max_width
    ("trade__tp_r_multiple", 1.5),  # R ∈ [2, 4]
    ("trade__tp_r_multiple", 4.5),
    ("structure__pivot_window_n", 1),  # N ∈ [2, 20]
    ("structure__pivot_window_n", 21),
    ("risk__risk_per_trade_pct", 5.5),  # hard cap 5 %
    ("risk__risk_per_trade_pct", 0),
    ("trade__sl_buffer_pips", 31),  # [5, 30]
    ("trade__breakeven_enabled", True),  # v2 flag — not implemented
    ("timeframes__execution", "4H"),
    ("risk__day_boundary", "local"),
])
def test_validation_rules(path, value):
    with pytest.raises(ValidationError):
        cfg(**{path: value})


def test_min_rr_must_not_exceed_r_multiple():
    with pytest.raises(ValidationError):
        cfg(trade__min_rr=3.0)
    assert cfg(trade__tp_r_multiple=3.0, trade__min_rr=3.0).trade.min_rr == 3.0


def test_unknown_keys_and_non_forex_rejected():
    data = BotConfig().model_dump()
    data["structure"]["bogus"] = 1
    with pytest.raises(ValidationError):
        BotConfig.model_validate(data)
    with pytest.raises(ValidationError):
        cfg(symbols=["EURUSD", "XAUUSD"])
    with pytest.raises(ValidationError):
        cfg(symbols=["EURUSD", "EURUSD"])


def test_warnings_soft_bands():
    assert config_warnings(BotConfig()) == []
    w = config_warnings(cfg(trade__sl_buffer_pips=25, risk__risk_per_trade_pct=3))
    assert any("sl_buffer_pips" in x for x in w) and any("risk_per_trade_pct" in x for x in w)


def test_diff_flags():
    a = BotConfig()
    assert diff_flags(a, cfg(structure__pivot_window_n=6)) == (True, False)
    assert diff_flags(a, cfg(risk__risk_per_trade_pct=0.5)) == (False, False)
    assert diff_flags(a, cfg(mt5__magic_number=1)) == (False, True)


def test_manager_round_trip_keeps_comments_and_env_secrets(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.yaml", p)
    monkeypatch.setenv("MSC_MT5_LOGIN", "12345")
    monkeypatch.setenv("MSC_MT5_PASSWORD", "s3cret")
    cm = ConfigManager(p, load_env=False)
    assert cm.config.mt5.login == 12345 and cm.config.mt5.password == "s3cret"
    assert cm.file_config.mt5.login == 0 and set(cm.env_overrides) == {"mt5.login", "mt5.password"}
    data = cm.masked_dict()
    assert data["mt5"]["password"] == ""  # env password never exposed via the file config
    data["risk"]["risk_per_trade_pct"] = 0.75
    res = cm.update(data)
    assert not res.rebuild_required and res.config.risk.risk_per_trade_pct == 0.75
    text = p.read_text(encoding="utf-8")
    assert "# ── MT5 connection" in text and "risk_per_trade_pct: 0.75" in text
    assert "12345" not in text and "s3cret" not in text
    assert load_config_file(p).risk.risk_per_trade_pct == 0.75


def test_masked_password_is_kept(tmp_path):
    p = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.yaml", p)
    cm = ConfigManager(p, load_env=False)
    d = cm.file_config.model_dump()
    d["mt5"]["password"] = "pw-in-file"
    cm.update(d)
    masked = cm.masked_dict()
    assert masked["mt5"]["password"] == PASSWORD_MASK
    cm.update(masked)
    assert cm.file_config.mt5.password == "pw-in-file"


def test_schema_metadata():
    s = config_schema()
    keys = [x["key"] for x in s["sections"]]
    assert keys[:3] == ["mt5", "universe", "structure"]
    fields = {f["path"]: f for sec in s["sections"] for f in sec["fields"]}
    assert fields["structure.pivot_window_n"]["min"] == 2 and fields["structure.pivot_window_n"]["rebuild_required"]
    assert fields["trade.sl_buffer_pips"]["warn_min"] == 10 and fields["trade.sl_buffer_pips"]["warn_max"] == 20
    assert fields["risk.day_boundary"]["options"] == ["server", "utc"]
    assert fields["mt5.login"]["restart_required"]
    assert fields["symbols"]["type"] == "list"
