"""In-memory stand-in for the ``MetaTrader5`` package, used to exercise the live code path
(connector → warm-up → candle polling → risk → execution → position monitor) on any OS.

Times served to the bot are broker SERVER time (UTC + ``offset``), like the real terminal.
"""
from __future__ import annotations

from collections import namedtuple
from typing import Callable

import numpy as np

from app.models import Candle
from app.timeframes import TF_SECONDS

TerminalInfo = namedtuple("TerminalInfo", "connected trade_allowed name")
AccountInfo = namedtuple("AccountInfo", "balance equity margin margin_free margin_level currency leverage login "
                                        "server name trade_allowed")
SymbolInfo = namedtuple("SymbolInfo", "digits point trade_tick_size trade_tick_value trade_contract_size volume_min "
                                      "volume_max volume_step filling_mode trade_stops_level currency_base "
                                      "currency_profit trade_mode")
Tick = namedtuple("Tick", "time bid ask")
Position = namedtuple("Position", "ticket identifier time type magic volume price_open sl tp price_current profit "
                                  "swap symbol comment")
Deal = namedtuple("Deal", "ticket order time type entry magic position_id reason volume price commission swap "
                          "profit fee symbol comment")
Result = namedtuple("Result", "retcode deal order volume price comment")

RATE_DTYPE = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
                       ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")])


class FakeMT5:
    TIMEFRAME_W1, TIMEFRAME_D1, TIMEFRAME_H4, TIMEFRAME_H1, TIMEFRAME_M30, TIMEFRAME_M15 = (
        32769, 16408, 16388, 16385, 30, 15)
    _TF = {32769: "1W", 16408: "1D", 16388: "4H", 16385: "1H", 30: "30M", 15: "15M"}

    def __init__(self, data: dict[str, dict[str, list[Candle]]], clock: Callable[[], float], offset: int = 7200,
                 spread: float = 0.00008, balance: float = 10_000.0, reject_fok: bool = False,
                 drop_sltp_once: bool = False, ambiguous_once: bool = False, reject_modify: bool = False):
        self.data = data
        self.clock = clock
        self.offset = offset
        self.spread = spread
        self.balance = balance
        self.reject_fok = reject_fok
        self.drop_sltp_once = drop_sltp_once
        self.ambiguous_once = ambiguous_once  # first open order executes but replies TIMEOUT
        self.reject_modify = reject_modify
        self._tick_seq, self._tick_clock = 0, None
        self.positions: dict[int, dict] = {}
        self.deals: list[Deal] = []
        self.requests: list[dict] = []
        self.next_ticket = 1000
        self.last_check = None
        self._times = {(s, tf): np.array([c.time for c in cs], dtype="int64")
                       for s, tfs in data.items() for tf, cs in tfs.items()}

    # ── session ────────────────────────────────────────────────────────────────────────
    def initialize(self, **kw):
        return True

    def shutdown(self):
        return None

    def last_error(self):
        return (1, "Success")

    def terminal_info(self):
        return TerminalInfo(True, True, "FakeMT5")

    def account_info(self):
        eq = self.balance + sum(self._profit(p) for p in self.positions.values())
        return AccountInfo(self.balance, eq, 0.0, eq, 0.0, "USD", 100, 42, "Fake-Demo", "tester", True)

    def symbol_select(self, symbol, enable):
        return True

    def symbol_info(self, symbol):
        if symbol not in self.data:
            return None
        return SymbolInfo(5, 0.00001, 0.00001, 1.0, 100000.0, 0.01, 100.0, 0.01, 3, 0, symbol[:3], symbol[3:6], 4)

    # ── prices ─────────────────────────────────────────────────────────────────────────
    def _idx(self, symbol: str, tf: str) -> int:
        """Index of the bar forming at the clock (its open ≤ now)."""
        return int(np.searchsorted(self._times[(symbol, tf)], self.clock(), side="right")) - 1

    def _bid(self, symbol: str) -> float:
        i = self._idx(symbol, "1H")
        return self.data[symbol]["1H"][max(i, 0)].open

    def symbol_info_tick(self, symbol):
        b = self._bid(symbol)
        i = self._idx(symbol, "1H")
        bar_open = self.data[symbol]["1H"][max(i, 0)].time
        # ticks keep arriving while the market is open; in a weekend gap the last tick is frozen
        if self._tick_clock != self.clock():
            self._tick_clock, self._tick_seq = self.clock(), 0
        self._tick_seq = min(self._tick_seq + 1, 50)
        t = min(int(self.clock()) + self._tick_seq, bar_open + 3599)
        return Tick(t + self.offset, b, round(b + self.spread, 5))

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        tf = self._TF[timeframe]
        cs = self.data[symbol][tf]
        i = self._idx(symbol, tf)
        if i < 0:
            return np.zeros(0, dtype=RATE_DTYPE)
        sel = cs[max(0, i - count + 1): i + 1]
        arr = np.zeros(len(sel), dtype=RATE_DTYPE)
        for k, c in enumerate(sel):
            if k == len(sel) - 1:  # still forming: only the open is known
                arr[k] = (c.time + self.offset, c.open, c.open, c.open, c.open, 0, 0, 0)
            else:
                arr[k] = (c.time + self.offset, c.open, c.high, c.low, c.close, 0, 0, 0)
        return arr

    # ── trading ────────────────────────────────────────────────────────────────────────
    def _profit(self, p: dict, price: float | None = None) -> float:
        bid = self._bid(p["symbol"]) if price is None else price
        if p["type"] == 0:
            return (bid - p["price_open"]) * p["volume"] * 100000
        ask = bid + self.spread if price is None else price
        return (p["price_open"] - ask) * p["volume"] * 100000

    def positions_get(self, **kw):
        out = []
        for p in self.positions.values():
            cur = self._bid(p["symbol"])
            out.append(Position(p["ticket"], p["ticket"], p["time"], p["type"], p["magic"], p["volume"],
                                p["price_open"], p["sl"], p["tp"], cur, round(self._profit(p), 2), 0.0, p["symbol"],
                                p["comment"]))
        return tuple(out)

    def _deal(self, p: dict, entry: int, price: float, reason: int, profit: float) -> Deal:
        self.next_ticket += 1
        d = Deal(self.next_ticket, p["ticket"], int(self.clock()) + self.offset, p["type"], entry, p["magic"],
                 p["ticket"], reason, p["volume"], price, 0.0, 0.0, round(profit, 2), 0.0, p["symbol"], p["comment"])
        self.deals.append(d)
        return d

    def _close(self, p: dict, price: float, reason: int) -> Deal:
        profit = self._profit(p, price)
        self.balance += profit
        del self.positions[p["ticket"]]
        return self._deal(p, 1, price, reason, profit)

    def order_send(self, req):
        self.requests.append(dict(req))
        if req["action"] == 6:  # SLTP
            p = self.positions.get(req["position"])
            if self.reject_modify:
                return Result(10016, 0, 0, 0.0, 0.0, "invalid stops")
            if p is None:
                return Result(10013, 0, 0, 0.0, 0.0, "invalid")
            p["sl"], p["tp"] = req["sl"], req["tp"]
            return Result(10009, 0, p["ticket"], p["volume"], 0.0, "done")
        if self.reject_fok and req.get("type_filling") == 0:
            return Result(10030, 0, 0, 0.0, 0.0, "unsupported filling mode")
        if "position" in req:  # close
            p = self.positions.get(req["position"])
            if p is None:
                return Result(10013, 0, 0, 0.0, 0.0, "no position")
            d = self._close(p, req["price"], 3)
            return Result(10009, d.ticket, p["ticket"], p["volume"], req["price"], "done")
        self.next_ticket += 1
        t = self.next_ticket
        sl, tp = req.get("sl", 0.0), req.get("tp", 0.0)
        if self.drop_sltp_once:
            sl = tp = 0.0
            self.drop_sltp_once = False
        p = {"ticket": t, "time": int(self.clock()) + self.offset, "type": req["type"], "magic": req["magic"],
             "volume": req["volume"], "price_open": req["price"], "sl": sl, "tp": tp, "symbol": req["symbol"],
             "comment": req["comment"]}
        self.positions[t] = p
        d = self._deal(p, 0, req["price"], 3, 0.0)
        if self.ambiguous_once:
            self.ambiguous_once = False
            return Result(10012, 0, 0, 0.0, 0.0, "timeout")  # executed, but the reply says timeout
        return Result(10009, d.ticket, t, req["volume"], req["price"], "done")

    def open_naked(self, symbol: str, lots: float = 0.1, magic: int = 20260922, comment: str = "manual") -> int:
        """Simulate a bot position that somehow has no SL/TP."""
        self.next_ticket += 1
        t = self.next_ticket
        self.positions[t] = {"ticket": t, "time": int(self.clock()) + self.offset, "type": 0, "magic": magic,
                             "volume": lots, "price_open": self._bid(symbol) + self.spread, "sl": 0.0, "tp": 0.0,
                             "symbol": symbol, "comment": comment}
        return t

    def history_deals_get(self, *args, position=None, **kw):
        if position is not None:
            return tuple(d for d in self.deals if d.position_id == position)
        if len(args) >= 2:
            lo, hi = args[0], args[1]
            return tuple(d for d in self.deals if lo <= d.time <= hi)
        return tuple(self.deals)

    # ── server-side SL/TP ──────────────────────────────────────────────────────────────
    def on_clock(self) -> None:
        now = self.clock()
        if self.last_check is None:
            self.last_check = now
            return
        for p in list(self.positions.values()):
            if not p["sl"] or not p["tp"]:
                continue
            cs = self.data[p["symbol"]]["1H"]
            for c in cs:
                if c.time < self.last_check or c.time + TF_SECONDS["1H"] > now:
                    continue
                if p["type"] == 0:
                    if c.low <= p["sl"]:
                        self._close(p, p["sl"], 4)
                        break
                    if c.high >= p["tp"]:
                        self._close(p, p["tp"], 5)
                        break
                else:
                    if c.high + self.spread >= p["sl"]:
                        self._close(p, p["sl"], 4)
                        break
                    if c.low + self.spread <= p["tp"]:
                        self._close(p, p["tp"], 5)
                        break
        self.last_check = now
