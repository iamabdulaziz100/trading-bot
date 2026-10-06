"""Symbol trading specification shared by live sizing (from MT5 ``symbol_info``) and the
backtester (from ``config/bt_symbol_specs.yaml``)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from app.pip_engine import pip_size as engine_pip_size
from app.pip_engine import split_fx


@dataclass
class SymbolSpec:
    symbol: str
    digits: int
    point: float
    pip_size: float
    tick_size: float
    tick_value: float  # account-currency value of one tick_size move for 1.0 lot
    contract_size: float = 100000.0
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01
    filling_mode: int = 0
    stops_level: int = 0
    currency_base: str = ""
    currency_profit: str = ""
    trade_allowed: bool = True

    @property
    def pip_value_per_lot(self) -> float:
        return self.tick_value * self.pip_size / self.tick_size if self.tick_size else 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Backtest symbol table ───────────────────────────────────────────────────────────────

@dataclass
class BTSymbolSpec:
    symbol: str
    pip_size: float
    digits: int
    pip_value_per_lot: float | str  # number or "auto"
    contract_size: float = 100000.0
    spread_pips: float | None = None
    commission_per_lot: float | None = None
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01
    base_to_usd: float | None = None

    def pip_value(self, price: float) -> float:
        """USD value of one pip per 1.0 lot at ``price`` (account currency = USD)."""
        if self.pip_value_per_lot != "auto":
            return float(self.pip_value_per_lot)
        fx = split_fx(self.symbol)
        if fx and fx[1] == "USD":
            return self.contract_size * self.pip_size
        if fx and fx[0] == "USD" and price > 0:
            return self.contract_size * self.pip_size / price
        raise ValueError(f"{self.symbol}: pip_value_per_lot 'auto' needs USD as base or quote — set a number")

    def base_value_usd(self, price: float) -> float:
        fx = split_fx(self.symbol)
        if fx and fx[0] == "USD":
            return 1.0
        if fx and fx[1] == "USD":
            return price
        return float(self.base_to_usd or 1.0)

    def to_symbol_spec(self, price: float) -> SymbolSpec:
        point = 10 ** -self.digits
        return SymbolSpec(symbol=self.symbol, digits=self.digits, point=point, pip_size=self.pip_size,
                          tick_size=point, tick_value=self.pip_value(price) * point / self.pip_size,
                          contract_size=self.contract_size, min_lot=self.min_lot, max_lot=self.max_lot,
                          lot_step=self.lot_step)


def load_bt_specs(path: Path) -> dict[str, BTSymbolSpec]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {sym: bt_spec_from_dict(sym, d) for sym, d in data.items()}


def bt_spec_from_dict(symbol: str, d: dict[str, Any]) -> BTSymbolSpec:
    pv = d.get("pip_value_per_lot", "auto")
    if not (pv == "auto" or isinstance(pv, (int, float))):
        raise ValueError(f"{symbol}: pip_value_per_lot must be a number or 'auto'")
    return BTSymbolSpec(
        symbol=symbol,
        pip_size=float(d.get("pip_size") or engine_pip_size(symbol)),
        digits=int(d.get("digits", 3 if (split_fx(symbol) or ("", ""))[1] == "JPY" else 5)),
        pip_value_per_lot=pv if pv == "auto" else float(pv),
        contract_size=float(d.get("contract_size", 100000)),
        spread_pips=None if d.get("spread_pips") is None else float(d["spread_pips"]),
        commission_per_lot=None if d.get("commission_per_lot") is None else float(d["commission_per_lot"]),
        min_lot=float(d.get("min_lot", 0.01)), max_lot=float(d.get("max_lot", 100)),
        lot_step=float(d.get("lot_step", 0.01)),
        base_to_usd=None if d.get("base_to_usd") is None else float(d["base_to_usd"]),
    )


def default_bt_spec(symbol: str) -> BTSymbolSpec:
    return bt_spec_from_dict(symbol, {})


def save_bt_specs(path: Path, specs: dict[str, dict[str, Any]]) -> None:
    for sym, d in specs.items():
        bt_spec_from_dict(sym, d)  # validate
    header = ("# Backtest symbol spec table (docs/spec/05_backtesting_spec.md §2). Edited from the UI.\n"
              "# pip_value_per_lot: USD per pip per lot, or \"auto\" (USD base/quote pairs only).\n")
    path.write_text(header + yaml.safe_dump(specs, sort_keys=True, default_flow_style=None), encoding="utf-8")
