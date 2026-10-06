"""Configuration schema (07_configuration_schema.md) — validation, persistence, env overrides.

* ``config.yaml`` is the source of truth at startup; UI edits write through to YAML (comments
  preserved) and to the ``config_history`` table.
* Unknown keys are rejected (``extra="forbid"``).
* MT5 credentials may be supplied through environment variables / a git-ignored ``.env`` file
  (``MSC_MT5_LOGIN``, ``MSC_MT5_PASSWORD``, ``MSC_MT5_SERVER``, ``MSC_MT5_PATH``). They are
  applied on top of the file config at runtime and never written back to ``config.yaml``.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import threading
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
PASSWORD_MASK = "********"

TFName = Literal["1W", "1D", "4H", "1H", "30M", "15M"]
TriggerName = Literal["BULL_ENGULF", "BEAR_ENGULF", "MORNING_STAR", "EVENING_STAR", "HAMMER", "SHOOTING_STAR"]


def F(default: Any, description: str, spec_ref: str = "", *, warn_min: float | None = None,
      warn_max: float | None = None, **constraints: Any) -> Any:
    """Field with UI metadata (description, formal-spec reference, soft warning band)."""
    extra = {"spec_ref": spec_ref, "warn_min": warn_min, "warn_max": warn_max}
    if isinstance(default, (list, dict)):
        return Field(default_factory=lambda d=default: copy.deepcopy(d), description=description,
                     json_schema_extra=extra, **constraints)
    return Field(default, description=description, json_schema_extra=extra, **constraints)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


# ── Sections ─────────────────────────────────────────────────────────────────────────────

class MT5Config(_Model):
    path: str = F("", "Optional terminal64.exe path", "04 §1")
    login: int = F(0, "Account login (0 = use the already logged-in terminal)", "04 §1", ge=0)
    password: str = F("", "Account password (prefer MSC_MT5_PASSWORD in .env)", "04 §1")
    server: str = F("", "Broker server name", "04 §1")
    magic_number: int = F(20260922, "Magic number namespacing all bot orders/positions", "03 §6", ge=1)
    max_slippage_points: int = F(10, "Max deviation in points for market orders", "04 §4", ge=0, le=1000)


class TimeframesConfig(_Model):
    htf: list[TFName] = F(["1W", "1D", "4H"], "Trend state machine chain (pillar 1)", "02 §F")
    execution: Literal["1H", "30M", "15M"] = F("1H", "Execution timeframe", "02 §A")

    @field_validator("htf")
    @classmethod
    def _htf_chain(cls, v: list[str]) -> list[str]:
        if sorted(v) != sorted(["1W", "1D", "4H"]):
            raise ValueError("htf must be exactly ['1W','1D','4H'] (the §F alignment matrix uses all three)")
        return ["1W", "1D", "4H"]


class LookbackSpec(_Model):
    years: float = Field(0, ge=0)
    months: float = Field(0, ge=0)
    days: float = Field(0, ge=0)

    def to_seconds(self) -> int:
        return int((self.years * 365.25 + self.months * 30.4375 + self.days) * 86400)


class StructureConfig(_Model):
    body_based_swings: bool = F(True, "Pivots on candle bodies (DO NOT disable for spec fidelity)", "02 §C")
    pivot_window_n: int = F(5, "Symmetric N-bar pivot window", "02 §C", ge=2, le=20)
    pivot_tie_rule: Literal["first_of_equal", "strict"] = F(
        "first_of_equal", "Equal body extremes on the right side: first_of_equal keeps the first bar of a "
        "plateau as the pivot; strict = formula as written (no pivot on ties)", "02 §C [DECISION]")
    min_swing_atr_mult: float = F(0.5, "Bump filter ATR multiplier (0 = pure N-window)", "02 §C", ge=0, le=10)
    min_swing_pips: float = F(10, "Absolute floor for the bump filter (pips)", "02 §C", ge=0)
    atr_period: int = F(14, "ATR period (Wilder)", "02 §E", ge=2, le=200)
    lookback: dict[str, LookbackSpec] = F(
        {"1W": {"years": 6}, "1D": {"years": 2}, "4H": {"months": 12}},
        "AOI pivot lookback per TF", "02 §G.1")

    @field_validator("lookback")
    @classmethod
    def _lookback_tfs(cls, v: dict[str, LookbackSpec]) -> dict[str, LookbackSpec]:
        bad = [k for k in v if k not in ("1W", "1D", "4H")]
        if bad:
            raise ValueError(f"lookback keys must be 1W/1D/4H, got {bad}")
        return v


class AOIConfig(_Model):
    min_touches: int = F(3, "Touches(Z) ≥ min_touches", "02 §G.2", ge=1, le=20)
    min_width_pips: float = F(5, "Minimum zone width (pips)", "02 §G.2", gt=0)
    max_width_pips: float = F(60, "Maximum zone width (pips)", "02 §G.2", gt=0)
    sweep_step_pips: float = F(1, "Vertical window sweep granularity (pips)", "02 §G.1", gt=0)
    merge_tolerance_atr_mult: float = F(0.25, "Merge tolerance ATR multiplier", "02 §G.1", ge=0)
    merge_tolerance_min_pips: float = F(10, "Merge tolerance floor (pips)", "02 §G.1", ge=0)
    zone_timeframes: list[Literal["1W", "1D", "4H"]] = F(["1W", "1D"], "Pillar-2 qualifying TFs", "02 §G.3")
    allow_4h_zones: bool = F(False, "Also build/qualify 4H zones", "02 §G.3")
    boundary_confinement: bool = F(True, "Zones strictly inside the active structural range", "02 §G.3")

    @model_validator(mode="after")
    def _check(self) -> "AOIConfig":
        if self.min_width_pips >= self.max_width_pips:
            raise ValueError("aoi.min_width_pips must be < aoi.max_width_pips")
        if "4H" in self.zone_timeframes and not self.allow_4h_zones:
            raise ValueError("4H in aoi.zone_timeframes requires aoi.allow_4h_zones: true")
        if not self.zone_timeframes and not self.allow_4h_zones:
            raise ValueError("aoi.zone_timeframes must not be empty")
        return self

    @property
    def effective_zone_tfs(self) -> list[str]:
        tfs = [tf for tf in ("1W", "1D", "4H") if tf in self.zone_timeframes]
        if self.allow_4h_zones and "4H" not in tfs:
            tfs.append("4H")
        return tfs


class BreakRetestConfig(_Model):
    retest_window_bars: int = F(15, "Retest must occur within N bars of the breakout", "02 §H.1", ge=1, le=500)
    retest_tolerance_pips: float = F(5, "Body tolerance beyond the zone during retest", "02 §H.1", ge=0)
    expiry_bars: int = F(20, "PatternEvent validity (bars) awaiting pillar 4", "02 §H.1", ge=1, le=500)


class HeadShouldersConfig(_Model):
    neckline_threshold_atr_mult: float = F(0.6, "|V2−V1| ≤ mult × ATR(14) neckline alignment", "02 §H.2", gt=0, le=20)
    expiry_bars: int = F(20, "PatternEvent validity (bars)", "02 §H.2", ge=1, le=500)


class PatternsConfig(_Model):
    break_retest: BreakRetestConfig = Field(default_factory=BreakRetestConfig)
    head_shoulders: HeadShouldersConfig = Field(default_factory=HeadShouldersConfig)


class CandlesConfig(_Model):
    star_indecision_body_ratio: float = F(0.35, "Star middle candle: |C−O| ≤ ratio × (H−L)", "02 §I.1", gt=0, le=1)
    hammer_wick_ratio: float = F(0.60, "Rejection wick ≥ ratio × range", "02 §I.1", gt=0, le=1)
    hammer_body_ratio: float = F(0.25, "Body ≤ ratio × range", "02 §I.1", gt=0, le=1)
    engulf_lookback: int = F(2, "Engulf the bodies of the previous N candles", "02 §I.1", ge=1, le=5)
    triggers: list[TriggerName] = F(
        ["BULL_ENGULF", "BEAR_ENGULF", "MORNING_STAR", "EVENING_STAR", "HAMMER", "SHOOTING_STAR"],
        "Enabled trigger formations", "02 §I")


class ConfluenceConfig(_Model):
    min_pillars: int = F(3, "Pillars required to fire a TradeSignal", "02 §K", ge=1, le=4)
    ema_period: int = F(50, "EMA period for the bonus filter (α = 2/(n+1))", "02 §J", ge=2, le=500)
    ema_counts_as_confluence: bool = F(False, "Count EMA as an extra pillar (spec: no)", "02 §J")


class TradeConfig(_Model):
    sl_buffer_pips: float = F(15, "SL buffer beyond the trigger wick (pips)", "02 §L", ge=5, le=30,
                              warn_min=10, warn_max=20)
    tp_r_multiple: float = F(2.0, "TP = entry ± R × risk", "02 §L", ge=2.0, le=4.0)
    tp_cap_at_structure: bool = F(True, "Cap TP at active_HH (long) / active_LL (short)", "02 §L")
    min_rr: float = F(2.0, "Reject if the structure cap forces R:R below this", "02 §L", gt=0, le=10)
    rr_slippage_tolerance: float = F(0.2, "Abort if fill R:R < min_rr − tolerance", "03 §2", ge=0, le=5)
    signal_ttl_seconds: int = F(300, "Max signal age before execution", "04 §4", ge=1, le=86400)
    entry_zone_tolerance_pips: float = F(10, "Max distance of price from the AOI edge at execution", "04 §4", ge=0)
    one_trade_per_symbol: bool = F(True, "No stacking on a symbol", "03 §3")
    breakeven_enabled: bool = F(False, "v2 — not implemented (strict set & forget)", "02 §L")
    trailing_enabled: bool = F(False, "v2 — not implemented (strict set & forget)", "02 §L")
    partial_close_enabled: bool = F(False, "v2 — not implemented (strict set & forget)", "02 §L")

    @model_validator(mode="after")
    def _check(self) -> "TradeConfig":
        if self.min_rr > self.tp_r_multiple + 1e-12:
            raise ValueError("trade.min_rr must be ≤ trade.tp_r_multiple")
        for flag in ("breakeven_enabled", "trailing_enabled", "partial_close_enabled"):
            if getattr(self, flag):
                raise ValueError(f"trade.{flag} is a v2 feature and is not implemented (strict set & forget)")
        return self


class RiskConfig(_Model):
    risk_per_trade_pct: float = F(1.0, "Risk per trade (% of balance)", "03 §1", gt=0, le=5, warn_max=2)
    risk_per_trade_pct_max: float = F(1.5, "4-pillar risk when pillar_risk_scaling is on", "03 §5", gt=0, le=5,
                                      warn_max=2)
    pillar_risk_scaling: bool = F(False, "Scale risk with pillar count", "03 §5")
    max_daily_loss_pct: float = F(3.0, "Halt new entries when day P/L ≤ −pct of day-start balance", "03 §3",
                                  gt=0, le=100)
    max_concurrent_trades: int = F(3, "Global cap on open bot positions", "03 §3", ge=1, le=100)
    max_trades_per_day: int = F(0, "0 = disabled", "03 §3", ge=0, le=1000)
    day_boundary: Literal["server", "utc"] = F("server", "Day boundary for daily stats/cutoff", "01 §7")


class NewsConfig(_Model):
    enabled: bool = F(True, "News blackout filter", "03 §4")
    feed_url: str = F("", "Calendar JSON feed (empty = built-in default)", "01 §6")
    poll_minutes: int = F(60, "Feed poll interval (minutes)", "03 §4", ge=5, le=1440)
    impact_levels: list[Literal["high", "medium", "low"]] = F(["high"], "Impact levels that block", "03 §4")
    block_before_min: int = F(30, "Block new entries N minutes before the event", "03 §4", ge=0, le=1440)
    block_after_min: int = F(30, "Block new entries N minutes after the event", "03 §4", ge=0, le=1440)
    feed_failure_mode: Literal["allow", "block_all"] = F("allow", "Behaviour while the feed is down", "01 §6")


class BacktestConfig(_Model):
    initial_balance: float = F(10000, "Starting balance (USD)", "05 §4", gt=0)
    leverage: float = F(100, "Leverage for the simplified margin model", "05 §4", ge=1, le=5000)
    spread_pips_default: float = F(1.0, "Spread when a symbol has no spec entry", "05 §3", ge=0)
    slippage_pips: float = F(0.5, "Adverse slippage on entries and SL exits", "05 §3", ge=0)
    commission_per_lot: float = F(0.0, "Round-turn commission per lot (USD)", "05 §3", ge=0)
    apply_news_filter: bool = F(False, "Apply news blackout using the stored calendar", "05 §4")
    symbol_specs_file: str = F("config/bt_symbol_specs.yaml", "Backtest symbol spec table", "05 §2")


class ServerConfig(_Model):
    host: str = F("127.0.0.1", "Bind address (keep 127.0.0.1)", "06")
    port: int = F(8000, "HTTP port", "06", ge=1, le=65535)
    api_token: str = F("", "Required only if host != 127.0.0.1", "06")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = F("INFO", "Log level", "01 §5")
    data_dir: str = F("data", "SQLite / uploads directory", "01 §5")


class BotConfig(_Model):
    mt5: MT5Config = Field(default_factory=MT5Config)
    symbols: list[str] = F(["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "EURGBP"],
                           "Traded symbols (forex majors/minors only)", "00 §2")
    timeframes: TimeframesConfig = Field(default_factory=TimeframesConfig)
    structure: StructureConfig = Field(default_factory=StructureConfig)
    aoi: AOIConfig = Field(default_factory=AOIConfig)
    patterns: PatternsConfig = Field(default_factory=PatternsConfig)
    candles: CandlesConfig = Field(default_factory=CandlesConfig)
    confluence: ConfluenceConfig = Field(default_factory=ConfluenceConfig)
    trade: TradeConfig = Field(default_factory=TradeConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: list[str]) -> list[str]:
        from app.pip_engine import is_v1_tradeable

        out: list[str] = []
        for s in v:
            s = s.strip()
            if not s:
                continue
            if not is_v1_tradeable(s):
                raise ValueError(f"{s!r} is not a forex pair (v1 scope: forex majors/minors only)")
            if s in out:
                raise ValueError(f"duplicate symbol {s!r}")
            out.append(s)
        if not out:
            raise ValueError("at least one symbol is required")
        return out

    @model_validator(mode="after")
    def _cross(self) -> "BotConfig":
        if self.risk.pillar_risk_scaling and self.risk.risk_per_trade_pct_max < self.risk.risk_per_trade_pct:
            raise ValueError("risk.risk_per_trade_pct_max must be ≥ risk.risk_per_trade_pct when pillar_risk_scaling is on")
        return self

    # convenience
    @property
    def exec_tf(self) -> str:
        return self.timeframes.execution

    @property
    def all_tfs(self) -> list[str]:
        return list(self.timeframes.htf) + [self.timeframes.execution]


# ── Warnings, diffs, hashing ─────────────────────────────────────────────────────────────

def config_warnings(cfg: BotConfig) -> list[str]:
    w: list[str] = []
    if not 10 <= cfg.trade.sl_buffer_pips <= 20:
        w.append(f"trade.sl_buffer_pips={cfg.trade.sl_buffer_pips} is outside the strategy spec band 10–20")
    if cfg.risk.risk_per_trade_pct > 2:
        w.append(f"risk.risk_per_trade_pct={cfg.risk.risk_per_trade_pct}% is above 2% — high risk")
    if cfg.server.host not in ("127.0.0.1", "localhost") and not cfg.server.api_token:
        w.append("server.host is not localhost and server.api_token is empty — the UI is exposed without auth")
    if not cfg.structure.body_based_swings:
        w.append("structure.body_based_swings is off — deviates from the formal spec")
    if cfg.confluence.ema_counts_as_confluence:
        w.append("confluence.ema_counts_as_confluence is on — deviates from the 'car hood' rule")
    return w


REBUILD_SECTIONS = ("symbols", "timeframes", "structure", "aoi", "patterns", "candles")
RESTART_SECTIONS = ("mt5", "server")


def diff_flags(old: BotConfig, new: BotConfig) -> tuple[bool, bool]:
    """Return (rebuild_required, restart_required) for a config change."""
    o, n = old.model_dump(), new.model_dump()
    rebuild = any(o[s] != n[s] for s in REBUILD_SECTIONS) or (
        o["confluence"]["ema_period"] != n["confluence"]["ema_period"])
    restart = any(o[s] != n[s] for s in RESTART_SECTIONS)
    return rebuild, restart


def config_hash(cfg: BotConfig | dict) -> str:
    data = cfg.model_dump() if isinstance(cfg, BotConfig) else cfg
    data = copy.deepcopy(data)
    data.get("mt5", {}).pop("password", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def validation_details(exc: ValidationError) -> list[dict[str, str]]:
    out = []
    for err in exc.errors():
        path = ".".join(str(p) for p in err.get("loc", ()) if not str(p).startswith("function-"))
        out.append({"path": path, "msg": err.get("msg", "invalid")})
    return out


# ── Env overrides ────────────────────────────────────────────────────────────────────────

ENV_MAP = {
    "MSC_MT5_LOGIN": ("mt5", "login", int),
    "MSC_MT5_PASSWORD": ("mt5", "password", str),
    "MSC_MT5_SERVER": ("mt5", "server", str),
    "MSC_MT5_PATH": ("mt5", "path", str),
}


def load_dotenv_file(root: Path = PROJECT_ROOT) -> None:
    env_file = root / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
    except ImportError:  # minimal fallback parser
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def apply_env_overrides(cfg: BotConfig) -> tuple[BotConfig, list[str]]:
    data = cfg.model_dump()
    applied: list[str] = []
    for env, (section, key, typ) in ENV_MAP.items():
        val = os.environ.get(env)
        if val not in (None, ""):
            data[section][key] = typ(val)
            applied.append(f"{section}.{key}")
    return BotConfig.model_validate(data), applied


# ── YAML persistence ─────────────────────────────────────────────────────────────────────

def load_config_file(path: Path = DEFAULT_CONFIG_PATH) -> BotConfig:
    if not path.exists():
        return BotConfig()
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return BotConfig.model_validate(data)


def _merge_into(node: Any, new: Any) -> Any:
    """Recursively merge plain data into a ruamel round-trip node, keeping comments/styles."""
    if isinstance(node, dict) and isinstance(new, dict):
        for k in list(node.keys()):
            if k not in new:
                del node[k]
        for k, v in new.items():
            if k in node and isinstance(node[k], (dict, list)) and isinstance(v, type(node[k])):
                node[k] = _merge_into(node[k], v)
            else:
                node[k] = v
        return node
    if isinstance(node, list) and isinstance(new, list):
        node[:] = new
        return node
    return new


def save_config_file(cfg: BotConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    data = cfg.model_dump(mode="json")
    tmp = path.with_suffix(".yaml.tmp")
    try:
        from ruamel.yaml import YAML

        ry = YAML()
        ry.preserve_quotes = True
        ry.width = 120
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                doc = ry.load(fh) or {}
            doc = _merge_into(doc, data)
        else:
            doc = data
        buf = io.StringIO()
        ry.dump(doc, buf)
        text = buf.getvalue()
    except ImportError:
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ── Manager ──────────────────────────────────────────────────────────────────────────────

@dataclass
class UpdateResult:
    config: BotConfig
    rebuild_required: bool
    restart_required: bool
    warnings: list[str] = field(default_factory=list)


class ConfigManager:
    """Holds the file config (as in YAML) and the effective config (file + env overrides)."""

    def __init__(self, path: Path = DEFAULT_CONFIG_PATH, *, load_env: bool = True):
        self.path = Path(path)
        self._lock = threading.RLock()
        if load_env:
            load_dotenv_file(self.path.parent)
        self.file_config = load_config_file(self.path)
        self.config, self.env_overrides = apply_env_overrides(self.file_config)
        self._listeners: list[typing.Callable[[BotConfig, BotConfig], None]] = []

    def on_change(self, fn: typing.Callable[[BotConfig, BotConfig], None]) -> None:
        self._listeners.append(fn)

    def masked_dict(self) -> dict[str, Any]:
        data = self.file_config.model_dump(mode="json")
        if data["mt5"]["password"]:
            data["mt5"]["password"] = PASSWORD_MASK
        return data

    def update(self, data: dict[str, Any]) -> UpdateResult:
        """Validate + persist a full config dict. Raises pydantic.ValidationError."""
        with self._lock:
            data = copy.deepcopy(data)
            mt5 = data.get("mt5")
            if isinstance(mt5, dict) and mt5.get("password") == PASSWORD_MASK:
                mt5["password"] = self.file_config.mt5.password
            new_file = BotConfig.model_validate(data)
            old_eff = self.config
            new_eff, applied = apply_env_overrides(new_file)
            rebuild, restart = diff_flags(old_eff, new_eff)
            save_config_file(new_file, self.path)
            self.file_config, self.config, self.env_overrides = new_file, new_eff, applied
        for fn in self._listeners:
            fn(old_eff, new_eff)
        return UpdateResult(new_eff, rebuild, restart, config_warnings(new_eff))


# ── UI schema metadata ───────────────────────────────────────────────────────────────────

SECTION_TITLES = {
    "mt5": "MT5 connection", "universe": "Universe", "structure": "Structure engine",
    "aoi": "AOI clustering", "patterns": "Structural patterns", "candles": "Candlestick triggers",
    "confluence": "Confluence", "trade": "Trade parameters", "risk": "Risk management",
    "news": "News blackout", "backtest": "Backtesting", "server": "Server / UI",
}


def _type_info(annotation: Any) -> tuple[str, list[Any] | None]:
    origin = typing.get_origin(annotation)
    if origin is Literal:
        return "enum", list(typing.get_args(annotation))
    if origin in (list, tuple):
        args = typing.get_args(annotation)
        if args and typing.get_origin(args[0]) is Literal:
            return "list", list(typing.get_args(args[0]))
        return "list", None
    if origin is dict:
        return "dict", None
    if annotation is bool:
        return "bool", None
    if annotation is int:
        return "int", None
    if annotation is float:
        return "float", None
    return "str", None


def _bounds(finfo: Any) -> tuple[float | None, float | None]:
    lo = hi = None
    for m in finfo.metadata:
        for attr in ("ge", "gt"):
            if getattr(m, attr, None) is not None:
                lo = getattr(m, attr)
        for attr in ("le", "lt"):
            if getattr(m, attr, None) is not None:
                hi = getattr(m, attr)
    return lo, hi


def _fields(model: type[BaseModel], prefix: str, section: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    defaults = model()
    for name, finfo in model.model_fields.items():
        ann = finfo.annotation
        path = f"{prefix}{name}"
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            out.extend(_fields(ann, f"{path}.", section))
            continue
        typ, options = _type_info(ann)
        extra = finfo.json_schema_extra or {}
        lo, hi = _bounds(finfo)
        default = getattr(defaults, name)
        if isinstance(default, BaseModel):
            default = default.model_dump()
        elif isinstance(default, dict):
            default = {k: (v.model_dump() if isinstance(v, BaseModel) else v) for k, v in default.items()}
        top = path.split(".")[0]
        rebuild = top in REBUILD_SECTIONS or path == "confluence.ema_period"
        out.append({
            "path": path, "type": typ, "default": default,
            "description": finfo.description or "", "spec_ref": extra.get("spec_ref", ""),
            "min": lo, "max": hi, "warn_min": extra.get("warn_min"), "warn_max": extra.get("warn_max"),
            "options": options, "restart_required": top in RESTART_SECTIONS, "rebuild_required": rebuild,
        })
    return out


def config_schema() -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    for key, finfo in BotConfig.model_fields.items():
        ann = finfo.annotation
        if key in ("symbols",):
            continue
        if key == "timeframes":
            # universe = symbols + timeframes
            sym_field = {
                "path": "symbols", "type": "list", "default": BotConfig().symbols,
                "description": BotConfig.model_fields["symbols"].description, "spec_ref": "00 §2",
                "min": None, "max": None, "warn_min": None, "warn_max": None, "options": None,
                "restart_required": False, "rebuild_required": True,
            }
            sections.append({"key": "universe", "title": SECTION_TITLES["universe"],
                             "fields": [sym_field] + _fields(TimeframesConfig, "timeframes.", "universe")})
            continue
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            sections.append({"key": key, "title": SECTION_TITLES.get(key, key),
                             "fields": _fields(ann, f"{key}.", key)})
    order = list(SECTION_TITLES)
    sections.sort(key=lambda s: order.index(s["key"]) if s["key"] in order else 99)
    return {"sections": sections}
