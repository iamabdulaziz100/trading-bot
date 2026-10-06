"""Position monitoring, crash recovery and the naked-position safety sweep (04 §5, 03 §2, 01 §5).

* Sync bot (magic-number) positions with the DB; detect closes from deal history and record
  exit price / reason (TP | SL | STOP_OUT | MANUAL_EXTERNAL | bot-initiated reason), P/L, R.
* PENDING rows (an order whose outcome was never recorded — crash, interruption) are resolved
  from open positions or deal history by their order comment, so no fill or P/L is lost.
* Safety sweep: a bot position without SL or TP gets the journaled bracket attached once; if it
  is still naked on the next pass it is closed. A close that keeps failing is reported as
  CRITICAL so the bot halts. This is the only modification the bot ever makes to a position.
* Positions unknown to the DB are adopted (signal_id from the order comment).
All of it runs under ``conn.op_lock`` so it never interleaves with an order in flight.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.db.journal import Journal, iso
from app.mt5 import connector as C
from app.mt5.connector import MT5Connector
from app.mt5.execution import ExecutionEngine, order_comment

log = logging.getLogger("monitor")

EXIT_DEAL_ENTRIES = {C.DEAL_ENTRY_OUT, C.DEAL_ENTRY_OUT_BY, C.DEAL_ENTRY_INOUT}
PENDING_GRACE_SEC = 120


def exit_reason_from_deal(reason: int, preset: str | None) -> str:
    if reason == C.DEAL_REASON_SL:
        return "SL"
    if reason == C.DEAL_REASON_TP:
        return "TP"
    if reason == C.DEAL_REASON_SO:
        return "STOP_OUT"
    if reason == C.DEAL_REASON_EXPERT:
        return preset or "BOT_CLOSE"
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


def _epoch(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    return datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


class PositionMonitor:
    def __init__(self, conn: MT5Connector, journal: Journal, magic: int, execution: ExecutionEngine,
                 spec_for=None):
        self.conn = conn
        self.journal = journal
        self.magic = magic  # fixed for the process lifetime
        self.execution = execution
        self.spec_for = spec_for  # callable(symbol) -> SymbolSpec, for safety closes
        self._missing_cycles: dict[str, int] = {}
        self._attach_attempted: set[int] = set()
        self.critical: list[str] = []

    # ── recovery ───────────────────────────────────────────────────────────────────────
    def reconcile_startup(self) -> None:
        with self.conn.op_lock:
            positions = self.conn.positions(self.magic)
            for tr in self.journal.open_trades():
                if tr["status"] == "PENDING":
                    self._resolve_pending(tr, positions)
            self.adopt_unknown(positions)
            self.safety_sweep(self.conn.positions(self.magic))
            log.info("startup reconcile: %d bot positions open on MT5", len(positions))

    def _resolve_pending(self, tr: dict[str, Any], positions: list[dict[str, Any]]) -> None:
        sid = tr["signal_id"]
        comment = order_comment(sid)
        p = next((q for q in positions if q["comment"] == comment and q["symbol"] == tr["symbol"]), None)
        if p:
            self.journal.update_trade(sid, status="OPEN", ticket=p["ticket"], entry_price=p["entry_price"],
                                      entry_time=iso(p["open_time_utc"]), lots=p["lots"])
            log.warning("recovered PENDING trade %s → OPEN (ticket %s)", sid, p["ticket"])
            return
        since = (_epoch(tr.get("created_at")) or time.time()) - 3600
        deal = self.execution.entry_deal_by_comment(comment, since)
        if deal:
            pos_id = int(deal.get("position_id") or deal.get("order") or 0)
            self.journal.update_trade(sid, status="OPEN", ticket=pos_id, entry_price=float(deal["price"]),
                                      entry_time=iso(deal["time_utc"]), lots=float(deal["volume"]))
            log.warning("recovered PENDING trade %s from deal history (position %s) — closed while offline?",
                        sid, pos_id)
            return  # sync() will now find it closed and record exit/P&L
        self.journal.update_trade(sid, status="FAILED", exit_reason="NOT_EXECUTED")
        log.warning("PENDING trade %s has no position or deal — marked FAILED (order never executed)", sid)

    def adopt_unknown(self, positions: list[dict[str, Any]]) -> None:
        for p in positions:
            if self.journal.trade_by_ticket(p["ticket"]) is not None:
                continue
            comment = p.get("comment") or ""
            sid = comment[4:] if comment.startswith("MSC|") else f"RECOVERED-{p['ticket']}"
            existing = self.journal.get_trade(sid)
            fields = dict(status="OPEN", ticket=p["ticket"], entry_price=p["entry_price"],
                          entry_time=iso(p["open_time_utc"]), lots=p["lots"])
            if existing and existing["status"] in ("PENDING", "FAILED", "OPEN") and not existing.get("ticket"):
                self.journal.update_trade(sid, **fields)
            elif existing:
                sid = f"RECOVERED-{p['ticket']}"
                self.journal.insert_trade({"signal_id": sid, "symbol": p["symbol"], "direction": p["direction"],
                                           "is_backtest": 0, "sl": p["sl"], "tp": p["tp"], **fields})
            else:
                self.journal.insert_trade({"signal_id": sid, "symbol": p["symbol"], "direction": p["direction"],
                                           "is_backtest": 0, "sl": p["sl"], "tp": p["tp"], **fields})
            log.warning("adopted MT5 position %s (%s) as %s", p["ticket"], p["symbol"], sid)

    # ── naked-position safety sweep ────────────────────────────────────────────────────
    def safety_sweep(self, positions: list[dict[str, Any]]) -> None:
        for p in positions:
            if p["sl"] and p["tp"]:
                self._attach_attempted.discard(p["ticket"])
                continue
            tr = self.journal.trade_by_ticket(p["ticket"]) or {}
            sl, tp = tr.get("sl") or p["sl"], tr.get("tp") or p["tp"]
            if p["ticket"] not in self._attach_attempted and sl and tp:
                self._attach_attempted.add(p["ticket"])
                res = self.execution.modify_sltp(p, sl, tp)
                log.critical("position %s (%s) had no SL/TP — attaching journaled bracket SL %s TP %s → retcode %s",
                             p["ticket"], p["symbol"], sl, tp, res.get("retcode"))
                continue
            log.critical("position %s (%s) still without SL/TP — CLOSING (naked positions are never tolerated)",
                         p["ticket"], p["symbol"])
            if tr:
                self.journal.update_trade(tr["signal_id"], status="CLOSING", exit_reason="SAFETY_CLOSE")
            spec = self.spec_for(p["symbol"]) if self.spec_for else None
            res = self.execution.close_position(p, spec, "MSC|safety") if spec else {"retcode": -1}
            if int(res.get("retcode", -1)) not in (C.RET_DONE, C.RET_DONE_PARTIAL, C.RET_PLACED):
                self.critical.append(f"naked position {p['ticket']} ({p['symbol']}) could not be closed")

    # ── periodic sync ──────────────────────────────────────────────────────────────────
    def sync(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Returns (open position views, trades closed since last sync)."""
        with self.conn.op_lock:
            positions = self.conn.positions(self.magic)
            now = time.time()
            for tr in self.journal.open_trades():
                if tr["status"] == "PENDING" and now - (_epoch(tr.get("created_at")) or now) > PENDING_GRACE_SEC:
                    self._resolve_pending(tr, positions)
            tickets = {p["ticket"] for p in positions}
            closed: list[dict[str, Any]] = []
            for tr in self.journal.open_trades() + self._closing():
                if tr["status"] == "PENDING" or not tr.get("ticket") or tr["ticket"] in tickets:
                    continue
                summary = summarize_deals(self.conn.history_deals_for_position(tr["ticket"]))
                sid = tr["signal_id"]
                if summary is None:
                    n = self._missing_cycles.get(sid, 0) + 1
                    self._missing_cycles[sid] = n
                    if n < 10:
                        continue  # deal history not synced yet
                    summary = {"exit_price": None, "exit_time": now, "reason": -1, "pnl": None}
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
            self.safety_sweep(positions)
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
