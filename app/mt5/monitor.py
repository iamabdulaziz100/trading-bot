"""Position monitoring & crash recovery (04 §5, 01 §5).

* Sync bot (magic-number) positions with the DB; detect closes from deal history and record
  exit price / reason (TP | SL | STOP_OUT | MANUAL_EXTERNAL | bot-initiated reason), P/L, R.
* On startup, reconcile: positions unknown to the DB are adopted (signal_id from the order
  comment), PENDING rows without a position are marked FAILED. Positions are never modified.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.db.journal import Journal, iso
from app.mt5 import connector as C
from app.mt5.connector import MT5Connector

log = logging.getLogger("monitor")

EXIT_DEAL_ENTRIES = {C.DEAL_ENTRY_OUT, C.DEAL_ENTRY_OUT_BY, C.DEAL_ENTRY_INOUT}


def exit_reason_from_deal(reason: int, preset: str | None) -> str:
    if reason == C.DEAL_REASON_SL:
        return "SL"
    if reason == C.DEAL_REASON_TP:
        return "TP"
    if reason == C.DEAL_REASON_SO:
        return "STOP_OUT"
    if reason == C.DEAL_REASON_EXPERT:
        return preset or "BOT_CLOSE"
    if reason in (C.DEAL_REASON_CLIENT, C.DEAL_REASON_MOBILE, C.DEAL_REASON_WEB):
        return preset or "MANUAL_EXTERNAL"
    return preset or "MANUAL_EXTERNAL"


def summarize_deals(deals: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Exit price (volume-weighted), time, reason code and total P/L of a closed position."""
    exits = [d for d in deals if int(d.get("entry", -1)) in EXIT_DEAL_ENTRIES]
    if not exits:
        return None
    vol = sum(float(d["volume"]) for d in exits) or 1.0
    price = sum(float(d["price"]) * float(d["volume"]) for d in exits) / vol
    pnl = sum(float(d.get("profit", 0)) + float(d.get("commission", 0)) + float(d.get("swap", 0))
              + float(d.get("fee", 0)) for d in deals)
    last = max(exits, key=lambda d: d["time_utc"])
    return {"exit_price": price, "exit_time": last["time_utc"], "reason": int(last.get("reason", -1)), "pnl": pnl}


class PositionMonitor:
    def __init__(self, conn: MT5Connector, journal: Journal, magic: int):
        self.conn = conn
        self.journal = journal
        self.magic = magic
        self._missing_cycles: dict[str, int] = {}

    def reconcile_startup(self) -> None:
        positions = self.conn.positions(self.magic)
        by_comment = {p["comment"]: p for p in positions}
        for tr in self.journal.open_trades():
            if tr["status"] == "PENDING":
                p = by_comment.get(f"MSC|{tr['signal_id']}"[:31])
                if p:
                    self.journal.update_trade(tr["signal_id"], status="OPEN", ticket=p["ticket"],
                                              entry_price=p["entry_price"], entry_time=iso(p["open_time_utc"]),
                                              lots=p["lots"])
                    log.warning("recovered PENDING trade %s → OPEN (ticket %s)", tr["signal_id"], p["ticket"])
                else:
                    self.journal.update_trade(tr["signal_id"], status="FAILED", exit_reason="CRASH_BEFORE_FILL")
                    log.warning("PENDING trade %s has no position — marked FAILED", tr["signal_id"])
        self.adopt_unknown(positions)
        log.info("startup reconcile: %d bot positions open on MT5", len(positions))

    def adopt_unknown(self, positions: list[dict[str, Any]]) -> None:
        for p in positions:
            if self.journal.trade_by_ticket(p["ticket"]) is not None:
                continue
            comment = p.get("comment") or ""
            sid = comment[4:] if comment.startswith("MSC|") else f"RECOVERED-{p['ticket']}"
            existing = self.journal.get_trade(sid)
            fields = dict(status="OPEN", ticket=p["ticket"], entry_price=p["entry_price"],
                          entry_time=iso(p["open_time_utc"]), lots=p["lots"], sl=p["sl"], tp=p["tp"])
            if existing:
                self.journal.update_trade(sid, **fields)
            else:
                self.journal.insert_trade({"signal_id": sid, "symbol": p["symbol"], "direction": p["direction"],
                                           "is_backtest": 0, **fields})
            log.warning("adopted MT5 position %s (%s) as %s", p["ticket"], p["symbol"], sid)

    def sync(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Returns (open position views, trades closed since last sync)."""
        positions = self.conn.positions(self.magic)
        tickets = {p["ticket"] for p in positions}
        closed: list[dict[str, Any]] = []
        for tr in self.journal.open_trades() + self._closing():
            if tr["status"] == "PENDING" or not tr.get("ticket"):
                continue
            if tr["ticket"] in tickets:
                continue
            summary = summarize_deals(self.conn.history_deals_for_position(tr["ticket"]))
            sid = tr["signal_id"]
            if summary is None:
                n = self._missing_cycles.get(sid, 0) + 1
                self._missing_cycles[sid] = n
                if n < 10:
                    continue  # deal history not synced yet
                summary = {"exit_price": None, "exit_time": time.time(), "reason": -1, "pnl": None}
                log.error("position %s gone but no exit deals found — marking closed (unknown P/L)", tr["ticket"])
            self._missing_cycles.pop(sid, None)
            reason = exit_reason_from_deal(summary["reason"], tr.get("exit_reason"))
            pnl = summary["pnl"]
            r_mult = (pnl / tr["risk_money"]) if (pnl is not None and tr.get("risk_money")) else None
            self.journal.update_trade(sid, status="CLOSED", exit_price=summary["exit_price"],
                                      exit_time=iso(summary["exit_time"]), exit_reason=reason, pnl_money=pnl,
                                      r_multiple=r_mult)
            log.info("CLOSED %s %s reason=%s pnl=%s R=%s", sid, tr["symbol"], reason,
                     None if pnl is None else round(pnl, 2), None if r_mult is None else round(r_mult, 2))
            closed.append(self.journal.get_trade(sid) or {})
        self.adopt_unknown(positions)
        return self.position_views(positions), closed

    def _closing(self) -> list[dict[str, Any]]:
        return [self.journal._trade_out(r) for r in
                self.journal._query("SELECT * FROM trades WHERE is_backtest=0 AND status='CLOSING'")]

    def position_views(self, positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for p in positions:
            tr = self.journal.trade_by_ticket(p["ticket"]) or {}
            risk = tr.get("risk_money")
            out.append({"ticket": p["ticket"], "signal_id": tr.get("signal_id"), "symbol": p["symbol"],
                        "direction": p["direction"], "lots": p["lots"], "entry_price": p["entry_price"],
                        "sl": p["sl"], "tp": p["tp"], "current_price": p["current_price"], "profit": p["profit"],
                        "risk_money": risk, "floating_r": (p["profit"] / risk) if risk else None,
                        "pillars": tr.get("pillars"), "open_time_utc": iso(p["open_time_utc"])})
        return out
