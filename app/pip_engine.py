"""Instrument normalization — the Pip Scale Engine (02 §B, formal spec §1).

Pip size is derived from the instrument class and cross-checked against the broker's
digits/point layout. Unknown layouts raise :class:`PipEngineError`: the bot refuses to
trade a symbol rather than guess.
"""
from __future__ import annotations

import math
import re

CURRENCIES = frozenset(
    "USD EUR GBP JPY AUD NZD CAD CHF SEK NOK DKK SGD HKD ZAR MXN TRY PLN CNH CZK HUF".split()
)
INDEX_ROOTS = (
    "US30", "SPX500", "US500", "NAS100", "US100", "USTEC", "GER40", "GER30", "DE40", "DE30",
    "UK100", "JP225", "US2000", "FRA40", "EU50", "AUS200", "HK50",
)


class PipEngineError(ValueError):
    """Raised for symbols whose pip layout cannot be determined safely."""


def normalize_symbol(symbol: str) -> str:
    """Strip broker decorations (suffixes like ``.m``, ``#``, ``-ECN``) and upper-case."""
    return re.sub(r"[^A-Z0-9]", "", symbol.upper())


def split_fx(symbol: str) -> tuple[str, str] | None:
    """Return (base, quote) for a forex pair, else None. Broker suffixes are ignored."""
    s = normalize_symbol(symbol)
    if len(s) < 6:
        return None
    base, quote = s[:3], s[3:6]
    if base in CURRENCIES and quote in CURRENCIES and base != quote:
        return base, quote
    return None


def instrument_class(symbol: str) -> str:
    s = normalize_symbol(symbol)
    if s.startswith("XAU"):
        return "METAL"
    if any(s.startswith(root) for root in INDEX_ROOTS):
        return "INDEX"
    fx = split_fx(s)
    if fx:
        return "FX_JPY" if fx[1] == "JPY" else "FX"
    return "UNKNOWN"


_CLASS_PIP = {"FX_JPY": 0.01, "FX": 0.0001, "METAL": 0.10, "INDEX": 1.00}


def pip_from_digits(digits: int, point: float) -> float:
    """digits 3/5 ⇒ pip = 10×point; digits 2/4 ⇒ pip = point (04 §2)."""
    if digits in (3, 5):
        return round(point * 10, 10)
    if digits in (2, 4):
        return round(point, 10)
    raise PipEngineError(f"unsupported digits layout: digits={digits} point={point}")


def pip_size(symbol: str, digits: int | None = None, point: float | None = None) -> float:
    """Pip size for ``symbol``; cross-checked against broker digits/point when given."""
    cls = instrument_class(symbol)
    if cls == "UNKNOWN":
        raise PipEngineError(f"unknown instrument layout for {symbol!r} — refusing to trade")
    expected = _CLASS_PIP[cls]
    if cls in ("FX", "FX_JPY") and digits is not None and point is not None:
        derived = pip_from_digits(int(digits), float(point))
        if abs(derived - expected) > 1e-12:
            raise PipEngineError(
                f"{symbol}: digits-derived pip {derived} != class pip {expected} — refusing to trade"
            )
    return expected


def is_v1_tradeable(symbol: str) -> bool:
    """v1 trade scope is forex majors/minors only (owner decision)."""
    return instrument_class(symbol) in ("FX", "FX_JPY")


def price_decimals(pip: float) -> int:
    """Display decimals for prices: one more than the pip position (fractional pips)."""
    return max(0, round(-math.log10(pip))) + 1
