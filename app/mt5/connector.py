"""MetaTrader 5 connector (04 §2).

* Every ``MetaTrader5`` call goes through one global lock (the Python API is not thread-safe)
  and is executed from executor threads by the bot (01 §4).
* Candle times are converted from broker server time to UTC at this boundary (04 §6).
* Results are returned as plain dicts / :class:`Candle` so nothing above this layer depends on
  the ``MetaTrader5`` package — the rest of the app (UI, backtester, tests) runs on any OS.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from app.config import MT5Config
from app.models import Candle
from app.pip_engine import PipEngineError, pip_size
from app.symbols import SymbolSpec

try:  # Windows only — the package talks to a locally installed MT5 terminal
    import MetaTrader5 as mt5  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - non-Windows / not installed
    mt5 = None

log = logging.getLogger("mt5")

_LOCK = threading.Lock()

# Trade server return codes (MqlTradeResult.retcode)
RET_REQUOTE = 10004
RET_REJECT = 10006
RET_PLACED = 10008
RET_DONE = 10009
RET_DONE_PARTIAL = 10010
RET_ERROR = 10011
RET_TIMEOUT = 10012
RET_INVALID_STOPS = 10016
RET_TRADE_DISABLED = 10017
RET_MARKET_CLOSED = 10018
RET_NO_MONEY = 10019
RET_PRICE_CHANGED = 10020
RET_PRICE_OFF = 10021
RET_TOO_MANY_REQUESTS = 10024
RET_LOCKED = 10028
RET_INVALID_FILL = 10030
RET_CONNECTION = 10031

# Enum values (stable MQL5 constants; read from the package when available)
def _c(name: str, default: int) -> int:
    return int(getattr(mt5, name, default)) if mt5 is not None else default


TRADE_ACTION_DEAL = _c("TRADE_ACTION_DEAL", 1)
TRADE_ACTION_SLTP = _c("TRADE_ACTION_SLTP", 6)
ORDER_TYPE_BUY = _c("ORDER_TYPE_BUY", 0)
ORDER_TYPE_SELL = _c("ORDER_TYPE_SELL", 1)
ORDER_TIME_GTC = _c("ORDER_TIME_GTC", 0)
ORDER_FILLING_FOK = _c("ORDER_FILLING_FOK", 0)
ORDER_FILLING_IOC = _c("ORDER_FILLING_IOC", 1)
ORDER_FILLING_RETURN = _c("ORDER_FILLING_RETURN", 2)
SYMBOL_FILLING_FOK = _c("SYMBOL_FILLING_FOK", 1)
SYMBOL_FILLING_IOC = _c("SYMBOL_FILLING_IOC", 2)
POSITION_TYPE_BUY = _c("POSITION_TYPE_BUY", 0)
DEAL_ENTRY_IN = _c("DEAL_ENTRY_IN", 0)
DEAL_ENTRY_OUT = _c("DEAL_ENTRY_OUT", 1)
DEAL_ENTRY_INOUT = _c("DEAL_ENTRY_INOUT", 2)
DEAL_ENTRY_OUT_BY = _c("DEAL_ENTRY_OUT_BY", 3)
DEAL_REASON_CLIENT = _c("DEAL_REASON_CLIENT", 0)
DEAL_REASON_MOBILE = _c("DEAL_REASON_MOBILE", 1)
DEAL_REASON_WEB = _c("DEAL_REASON_WEB", 2)
DEAL_REASON_EXPERT = _c("DEAL_REASON_EXPERT", 3)
DEAL_REASON_SL = _c("DEAL_REASON_SL", 4)
DEAL_REASON_TP = _c("DEAL_REASON_TP", 5)
DEAL_REASON_SO = _c("DEAL_REASON_SO", 6)

TF_MAP_NAMES = {"1W": "TIMEFRAME_W1", "1D": "TIMEFRAME_D1", "4H": "TIMEFRAME_H4", "1H": "TIMEFRAME_H1",
                "30M": "TIMEFRAME_M30", "15M": "TIMEFRAME_M15"}


class MT5Error(RuntimeError):
    pass


def _nt(obj: Any) -> dict[str, Any]:
    return obj._asdict() if hasattr(obj, "_asdict") else dict(obj)


class MT5Connector:
    def __init__(self, cfg: MT5Config):
        self.cfg = cfg
        self.available = mt5 is not None
        self.connected = False
        self.message = "not connected" if self.available else \
            "MetaTrader5 Python package not available (Windows + MT5 terminal required)"
        self.server_offset = 0  # seconds: server_time − UTC
        self.offset_known = False
        self._pending_offset: int | None = None
        # Optional fixed broker offset (e.g. MSC_MT5_SERVER_OFFSET_HOURS=3) disables auto-detection
        env_off = os.environ.get("MSC_MT5_SERVER_OFFSET_HOURS", "").strip()
        self.offset_override: int | None = int(float(env_off) * 3600) if env_off else None
        if self.offset_override is not None:
            self.server_offset, self.offset_known = self.offset_override, True
        self.terminal: dict[str, Any] | None = None
        self.account_cache: tuple[float, dict[str, Any]] | None = None
        # Held for whole multi-call operations (an order + its verification, a position sync) so
        # the watchdog cannot reconnect/shutdown the terminal session in the middle of them.
        self.op_lock = threading.RLock()

    # ── connection ─────────────────────────────────────────────────────────────────────
    def connect(self, attempts: int = 5) -> bool:
        """mt5.initialize(path?, login?, password?, server?) with 5 retries, backoff 2^n s."""
        with self.op_lock:
            return self._connect(attempts)

    def _connect(self, attempts: int) -> bool:
        if not self.available:
            self.connected = False
            return False
        kwargs: dict[str, Any] = {}
        if self.cfg.path:
            kwargs["path"] = self.cfg.path
        if self.cfg.login:
            kwargs.update(login=int(self.cfg.login), password=self.cfg.password, server=self.cfg.server)
        for n in range(attempts):
            with _LOCK:
                ok = mt5.initialize(**kwargs)
                err = mt5.last_error()
                info = mt5.terminal_info() if ok else None
            if ok and info is not None:
                self.terminal = _nt(info)
                self.connected = bool(self.terminal.get("connected", True))
                if self.connected:
                    self.message = "connected"
                    acc = self.account(max_age=0)
                    log.info("MT5 connected: terminal=%s account=%s server=%s trade_allowed=%s",
                             self.terminal.get("name"), acc and acc.get("login"), acc and acc.get("server"),
                             self.terminal.get("trade_allowed"))
                    if not self.terminal.get("trade_allowed", True):
                        log.warning("MT5 terminal: algorithmic trading is DISABLED (Tools → Options → "
                                    "Expert Advisors → Allow algorithmic trading)")
                    return True
            self.message = f"initialize failed: {err}"
            log.warning("MT5 initialize attempt %d/%d failed: %s", n + 1, attempts, err)
            with _LOCK:
                mt5.shutdown()
            time.sleep(2 ** n)
        self.connected = False
        return False

    def shutdown(self) -> None:
        with self.op_lock:
            if self.available:
                with _LOCK:
                    mt5.shutdown()
            self.connected = False

    def ping(self) -> bool:
        if not self.available:
            return False
        with self.op_lock, _LOCK:
            info = mt5.terminal_info()
        ok = info is not None and bool(_nt(info).get("connected", False))
        if info is not None:
            self.terminal = _nt(info)
        if not ok and self.connected:
            self.message = "terminal not responding / disconnected from broker"
        self.connected = ok
        return ok

    def _require(self) -> None:
        if not self.available:
            raise MT5Error(self.message)
        if not self.connected:
            raise MT5Error("MT5 disconnected")

    # ── account / symbols ──────────────────────────────────────────────────────────────
    def account(self, max_age: float = 5.0) -> dict[str, Any] | None:
        if not self.available:
            return None
        now = time.time()
        if self.account_cache and now - self.account_cache[0] <= max_age:
            return self.account_cache[1]
        with _LOCK:
            info = mt5.account_info()
        if info is None:
            return None
        d = _nt(info)
        acc = {"balance": float(d.get("balance", 0)), "equity": float(d.get("equity", 0)),
               "margin": float(d.get("margin", 0)), "free_margin": float(d.get("margin_free", 0)),
               "margin_level": float(d.get("margin_level", 0) or 0), "currency": d.get("currency", ""),
               "leverage": int(d.get("leverage", 0) or 0), "login": int(d.get("login", 0) or 0),
               "server": d.get("server", ""), "name": d.get("name", ""),
               "trade_allowed": bool(d.get("trade_allowed", True))}
        self.account_cache = (now, acc)
        return acc

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        self._require()
        with _LOCK:
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5Error(f"symbol_info({symbol}) returned None — check the symbol name in config.symbols")
        d = _nt(info)
        digits, point = int(d["digits"]), float(d["point"])
        pip = pip_size(symbol, digits, point)  # raises PipEngineError on unknown layouts
        tick_size = float(d.get("trade_tick_size") or point)
        tick_value = float(d.get("trade_tick_value") or 0.0)
        if tick_value <= 0:
            raise PipEngineError(f"{symbol}: trade_tick_value unavailable — refusing to size")
        return SymbolSpec(
            symbol=symbol, digits=digits, point=point, pip_size=pip, tick_size=tick_size, tick_value=tick_value,
            contract_size=float(d.get("trade_contract_size", 100000)), min_lot=float(d.get("volume_min", 0.01)),
            max_lot=float(d.get("volume_max", 100)), lot_step=float(d.get("volume_step", 0.01)),
            filling_mode=int(d.get("filling_mode", 0)), stops_level=int(d.get("trade_stops_level", 0)),
            currency_base=str(d.get("currency_base", "")), currency_profit=str(d.get("currency_profit", "")),
            trade_allowed=int(d.get("trade_mode", 4)) != 0)

    # ── market data ────────────────────────────────────────────────────────────────────
    def rates(self, symbol: str, tf: str, count: int) -> list[Candle]:
        """Last ``count`` bars (oldest first, the still-forming bar last), times in UTC."""
        self._require()
        tfc = getattr(mt5, TF_MAP_NAMES[tf])
        with _LOCK:
            arr = mt5.copy_rates_from_pos(symbol, tfc, 0, int(count))
            err = mt5.last_error() if arr is None else None
        if arr is None:
            raise MT5Error(f"copy_rates_from_pos({symbol},{tf}) failed: {err}")
        off = self.server_offset
        return [Candle(int(r["time"]) - off, float(r["open"]), float(r["high"]), float(r["low"]),
                       float(r["close"]), float(r["tick_volume"])) for r in arr]

    def tick(self, symbol: str) -> dict[str, Any]:
        self._require()
        with _LOCK:
            t = mt5.symbol_info_tick(symbol)
        if t is None:
            raise MT5Error(f"symbol_info_tick({symbol}) returned None")
        d = _nt(t)
        return {"bid": float(d["bid"]), "ask": float(d["ask"]), "time_server": int(d["time"]),
                "time_utc": int(d["time"]) - self.server_offset}

    def _freshest_tick_time(self, symbols: list[str]) -> int | None:
        best = None
        for sym in symbols:
            try:
                with _LOCK:
                    mt5.symbol_select(sym, True)
                    t = mt5.symbol_info_tick(sym)
                if t is not None:
                    best = max(best or 0, int(t.time))
            except Exception:
                continue
        return best

    def detect_server_offset(self, symbols: list[str], wait: float = 10.0) -> int | None:
        """Broker UTC offset = freshest tick server time − UTC now, rounded to 15 min (04 §6).

        Only trusted while ticks are actually ARRIVING (the freshest tick time must advance within
        ``wait`` seconds) — on weekends the last tick is days old and would yield garbage. After the
        first detection only a ±1 h change (DST) is accepted directly; any other jump must be read
        twice in a row. ``MSC_MT5_SERVER_OFFSET_HOURS`` fixes the offset and disables detection.
        Returns the confirmed offset, or None when it cannot be determined now."""
        if self.offset_override is not None:
            return self.server_offset
        if not self.available or not self.connected:
            return None
        first = self._freshest_tick_time(symbols)
        if first is None:
            return None
        deadline = time.monotonic() + wait
        best = first
        while best <= first and time.monotonic() < deadline:
            time.sleep(0.5)
            best = self._freshest_tick_time(symbols) or best
        if best <= first:
            log.info("server offset not detectable now (no fresh ticks — market closed?) — keeping %+.2fh",
                     self.server_offset / 3600)
            return None
        raw = best - time.time()
        rounded = int(round(raw / 900.0) * 900)
        if abs(raw - rounded) > 180 or abs(rounded) > 14 * 3600:
            log.info("server offset reading implausible (raw=%.0fs) — keeping %+.2fh", raw, self.server_offset / 3600)
            return None
        if self.offset_known and rounded != self.server_offset:
            if abs(rounded - self.server_offset) != 3600 and rounded != self._pending_offset:
                self._pending_offset = rounded
                log.warning("server offset reading %+.2fh differs from %+.2fh — waiting for confirmation",
                            rounded / 3600, self.server_offset / 3600)
                return None
        self._pending_offset = None
        if rounded != self.server_offset or not self.offset_known:
            log.info("broker server UTC offset: %+.2fh", rounded / 3600)
        self.server_offset, self.offset_known = rounded, True
        return rounded

    # ── trading ────────────────────────────────────────────────────────────────────────
    def positions(self, magic: int) -> list[dict[str, Any]]:
        self._require()
        with _LOCK:
            ps = mt5.positions_get()
            err = mt5.last_error() if ps is None else None
        if ps is None:
            raise MT5Error(f"positions_get failed: {err}")
        out = []
        for p in ps:
            d = _nt(p)
            if int(d.get("magic", 0)) != int(magic):
                continue  # manual / other EA positions are never touched
            out.append({"ticket": int(d["ticket"]), "identifier": int(d.get("identifier", d["ticket"])),
                        "symbol": d["symbol"], "direction": "BUY" if int(d["type"]) == POSITION_TYPE_BUY else "SELL",
                        "lots": float(d["volume"]), "entry_price": float(d["price_open"]), "sl": float(d["sl"]),
                        "tp": float(d["tp"]), "current_price": float(d["price_current"]),
                        "profit": float(d["profit"]) + float(d.get("swap", 0.0)),
                        "open_time_utc": int(d["time"]) - self.server_offset, "comment": d.get("comment", ""),
                        "magic": int(d["magic"])})
        return out

    def order_send(self, request: dict[str, Any]) -> dict[str, Any]:
        self._require()
        with _LOCK:
            res = mt5.order_send(request)
            err = mt5.last_error() if res is None else None
        if res is None:
            return {"retcode": -1, "comment": f"order_send returned None: {err}", "deal": 0, "order": 0,
                    "price": 0.0, "volume": 0.0}
        return _nt(res)

    def deals_between(self, start_utc: float, end_utc: float) -> list[dict[str, Any]]:
        """Deals in [start, end] (UTC epoch; padded by a day each side because the terminal
        interprets the bounds in server time)."""
        self._require()
        lo = int(start_utc + self.server_offset - 86400)
        hi = int(end_utc + self.server_offset + 86400)
        with _LOCK:
            deals = mt5.history_deals_get(lo, hi)
        if deals is None:
            return []
        out = []
        for d in deals:
            x = _nt(d)
            x["time_utc"] = int(x["time"]) - self.server_offset
            out.append(x)
        return out

    def history_deals_for_position(self, position_id: int) -> list[dict[str, Any]]:
        self._require()
        with _LOCK:
            deals = mt5.history_deals_get(position=int(position_id))
        if deals is None:
            return []
        out = []
        for d in deals:
            x = _nt(d)
            x["time_utc"] = int(x["time"]) - self.server_offset
            out.append(x)
        return out
