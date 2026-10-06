"""Execution engine (04 §4, 03 §2) — bracket market orders, strict set & forget.

1. Pre-trade: bot still enabled, signal age ≤ signal_ttl_seconds, price within
   entry_zone_tolerance_pips of the AOI edge, SL/TP outside the broker's stops level.
2. Idempotency: a PENDING trade row is inserted under the UNIQUE signal_id BEFORE sending.
3. Filling mode: FOK → IOC → RETURN filtered by symbol capabilities; INVALID_FILL cycles on.
4. Retries: clear rejections (requote, price changed/off, reject, too many requests) are retried
   up to 3× with a fresh tick — each retry re-checks enabled state, zone tolerance and R:R at the
   new price. AMBIGUOUS results (no reply, timeout, error, connection, locked) may have executed on
   the server: the bot first looks for a position/deal carrying this order's comment and only
   re-sends if none exists — never a blind duplicate. NO_MONEY / MARKET_CLOSED / TRADE_DISABLED
   fail permanently.
5. Post-fill: verify SL/TP landed on the position; else order_modify once; else close
   immediately (CRITICAL). Any position still left without SL/TP (e.g. the sequence was
   interrupted) is caught by the position monitor's safety sweep. Naked positions are never
   tolerated.
6. Realized R:R from the actual fill < min_rr − rr_slippage_tolerance → close immediately
   (RR_SLIPPAGE_ABORT).
After placement the bot NEVER modifies a position (no breakeven / trailing / partials); the only
modification ever sent is attaching a missing SL/TP bracket.

The whole sequence runs under ``conn.op_lock`` so the watchdog cannot reconnect the terminal
session halfway through an order.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import BotConfig
from app.core.trade_params import entry_still_valid, rr_slippage_abort
from app.db.journal import DuplicateSignal, Journal, iso
from app.models import BUY, TradeSignal
from app.mt5 import connector as C
from app.mt5.connector import MT5Connector
from app.risk.manager import RiskDecision
from app.risk.sizing import floor_to_step
from app.symbols import SymbolSpec

log = logging.getLogger("execution")

CLEAR_RETRY = {C.RET_REQUOTE, C.RET_PRICE_CHANGED, C.RET_PRICE_OFF, C.RET_REJECT, C.RET_TOO_MANY_REQUESTS}
AMBIGUOUS = {-1, C.RET_ERROR, C.RET_TIMEOUT, C.RET_CONNECTION, C.RET_LOCKED}
PERMANENT = {C.RET_NO_MONEY, C.RET_MARKET_CLOSED, C.RET_TRADE_DISABLED}
FILLED = {C.RET_DONE, C.RET_DONE_PARTIAL, C.RET_PLACED}
ABORTED_BEFORE_SEND = -2

FILLED_STATUS, STALE, DUPLICATE, FAILED, ABORTED = "FILLED", "STALE", "DUPLICATE", "FAILED", "ABORTED"


@dataclass
class ExecResult:
    status: str
    message: str
    ticket: int | None = None
    fill_price: float | None = None
    lots: float = 0.0
    retcode: int | None = None
    gate_events: list[tuple[str, str, str]] = field(default_factory=list)


def filling_candidates(symbol_filling_mode: int) -> list[int]:
    """FOK → IOC → RETURN preference filtered by the symbol's SYMBOL_FILLING_MODE flags."""
    out = []
    if symbol_filling_mode & C.SYMBOL_FILLING_FOK:
        out.append(C.ORDER_FILLING_FOK)
    if symbol_filling_mode & C.SYMBOL_FILLING_IOC:
        out.append(C.ORDER_FILLING_IOC)
    out.append(C.ORDER_FILLING_RETURN)
    return out


def order_comment(signal_id: str) -> str:
    return f"MSC|{signal_id}"[:31]


def _ok(res: dict) -> bool:
    return int(res.get("retcode", -1)) in FILLED


class ExecutionEngine:
    def __init__(self, conn: MT5Connector, journal: Journal, cfg: Callable[[], BotConfig], magic: int,
                 can_trade: Callable[[], bool] = lambda: True):
        self.conn = conn
        self.journal = journal
        self.cfg = cfg
        self.magic = magic  # fixed for the process lifetime (changing it requires a restart)
        self.can_trade = can_trade
        self.preferred_filling: dict[str, int] = {}

    # ── lookups ────────────────────────────────────────────────────────────────────────
    def position_by_comment(self, symbol: str, comment: str) -> dict | None:
        for p in self.conn.positions(self.magic):
            if p["symbol"] == symbol and p["comment"] == comment:
                return p
        return None

    def entry_deal_by_comment(self, comment: str, since_utc: float) -> dict | None:
        for d in self.conn.deals_between(since_utc, time.time()):
            if int(d.get("magic", 0)) == self.magic and d.get("comment") == comment and \
                    int(d.get("entry", -1)) == C.DEAL_ENTRY_IN:
                return d
        return None

    def _find_position(self, ticket_hint: int, comment: str, symbol: str) -> dict | None:
        for _ in range(6):
            for p in self.conn.positions(self.magic):
                if p["ticket"] == ticket_hint or p["identifier"] == ticket_hint or \
                        (p["symbol"] == symbol and p["comment"] == comment):
                    return p
            time.sleep(0.5)
        return None

    def _verify_after_ambiguous(self, req: dict, sent_at: float) -> dict | None:
        """Did an order with an ambiguous reply actually execute? Returns a synthetic DONE result."""
        time.sleep(1.0)
        if "position" in req:  # a close: done if the position is gone
            if not any(p["ticket"] == req["position"] for p in self.conn.positions(self.magic)):
                return {"retcode": C.RET_DONE, "order": req["position"], "price": req.get("price", 0.0),
                        "comment": "verified closed after ambiguous reply"}
            return None
        comment = req.get("comment", "")
        p = self.position_by_comment(req["symbol"], comment)
        if p:
            return {"retcode": C.RET_DONE, "order": p["ticket"], "price": p["entry_price"], "volume": p["lots"],
                    "comment": "verified open after ambiguous reply"}
        d = self.entry_deal_by_comment(comment, sent_at - 300)
        if d:
            return {"retcode": C.RET_DONE, "order": int(d.get("position_id") or d.get("order") or 0),
                    "price": float(d["price"]), "volume": float(d["volume"]),
                    "comment": "verified deal after ambiguous reply"}
        return None

    # ── sending ────────────────────────────────────────────────────────────────────────
    def _send(self, req: dict, spec: SymbolSpec, retry_check: Callable[[float], str | None] | None = None) -> dict:
        fillings = filling_candidates(spec.filling_mode)
        pref = self.preferred_filling.get(spec.symbol)
        if pref in fillings:
            fillings.remove(pref)
            fillings.insert(0, pref)
        res: dict = {"retcode": -1, "comment": "not sent"}
        fi, retries = 0, 0
        while fi < len(fillings):
            req["type_filling"] = fillings[fi]
            if req.get("action") == C.TRADE_ACTION_DEAL:
                tick = self.conn.tick(spec.symbol)
                req["price"] = tick["ask"] if req["type"] == C.ORDER_TYPE_BUY else tick["bid"]
                if retries and retry_check is not None:
                    why = retry_check(req["price"])
                    if why:
                        return {"retcode": ABORTED_BEFORE_SEND, "comment": why}
            sent_at = time.time()
            res = self.conn.order_send(dict(req))
            rc = int(res.get("retcode", -1))
            if rc in FILLED:
                self.preferred_filling[spec.symbol] = fillings[fi]
                return res
            if rc == C.RET_INVALID_FILL:
                log.info("%s filling mode %s rejected — trying next", spec.symbol, fillings[fi])
                fi += 1
                continue
            if rc in AMBIGUOUS:
                found = self._verify_after_ambiguous(req, sent_at)
                if found:
                    log.warning("%s ambiguous order_send reply %s — order DID execute (%s)", spec.symbol, rc,
                                found["comment"])
                    return found
                if retries < 3:
                    retries += 1
                    log.warning("%s ambiguous order_send reply %s (%s), nothing executed — retry %d/3", spec.symbol,
                                rc, res.get("comment"), retries)
                    continue
                return res
            if rc in CLEAR_RETRY and retries < 3:
                retries += 1
                log.warning("%s order rejected %s (%s) — retry %d/3", spec.symbol, rc, res.get("comment"), retries)
                time.sleep(0.5)
                continue
            return res
        return res

    def close_position(self, pos: dict, spec: SymbolSpec, comment: str = "MSC|close") -> dict:
        cfg = self.cfg()
        req = {"action": C.TRADE_ACTION_DEAL, "symbol": pos["symbol"], "volume": pos["lots"],
               "type": C.ORDER_TYPE_SELL if pos["direction"] == BUY else C.ORDER_TYPE_BUY,
               "position": pos["ticket"], "deviation": cfg.mt5.max_slippage_points, "magic": self.magic,
               "comment": comment[:31], "type_time": C.ORDER_TIME_GTC}
        with self.conn.op_lock:
            res = self._send(req, spec)
        if not _ok(res):
            log.critical("close of position %s (%s) FAILED: retcode %s %s", pos["ticket"], pos["symbol"],
                         res.get("retcode"), res.get("comment"))
        return res

    def modify_sltp(self, pos: dict, sl: float, tp: float) -> dict:
        """Only used to ATTACH a missing SL/TP bracket — never to move an existing one."""
        req = {"action": C.TRADE_ACTION_SLTP, "symbol": pos["symbol"], "position": pos["ticket"],
               "sl": sl, "tp": tp, "magic": self.magic}
        return self.conn.order_send(req)

    # ── main ───────────────────────────────────────────────────────────────────────────
    def execute(self, sig: TradeSignal, plan: RiskDecision, spec: SymbolSpec, now: float | None = None) -> ExecResult:
        with self.conn.op_lock:
            sid = sig.signal_id
            try:
                return self._execute(sig, plan, spec, time.time() if now is None else now)
            except DuplicateSignal:
                return ExecResult(DUPLICATE, f"signal {sid} already executed (idempotency)")
            except Exception as exc:  # interrupted sequence — never assume success
                log.critical("%s execution interrupted (%s: %s) — order state unknown; the position monitor will "
                             "reconcile it and enforce SL/TP", sid, type(exc).__name__, exc)
                return ExecResult(FAILED, f"execution interrupted: {exc}",
                                  gate_events=[("EXECUTION", "BLOCK", f"interrupted: {exc}")])

    def _execute(self, sig: TradeSignal, plan: RiskDecision, spec: SymbolSpec, now: float) -> ExecResult:
        cfg = self.cfg()
        sid = sig.signal_id
        comment = order_comment(sid)
        tol, min_rr, rr_tol = cfg.trade.entry_zone_tolerance_pips, cfg.trade.min_rr, cfg.trade.rr_slippage_tolerance

        if not self.can_trade():
            return ExecResult(STALE, "bot disabled/halted before sending", gate_events=[("ENABLED", "BLOCK", "disabled")])
        age = now - sig.signal_time
        if age > cfg.trade.signal_ttl_seconds:
            return ExecResult(STALE, f"SIGNAL_TTL: signal age {age:.0f}s > {cfg.trade.signal_ttl_seconds}s",
                              gate_events=[("EXECUTION", "BLOCK", f"signal age {age:.0f}s > ttl")])
        tick = self.conn.tick(sig.symbol)
        px = tick["ask"] if sig.direction == BUY else tick["bid"]
        if not entry_still_valid(sig.direction, px, sig.z_min, sig.z_max, sig.pip_size, tol):
            return ExecResult(STALE, f"PRICE_LEFT_ZONE: price {px} > {tol} pips from zone {sig.z_min:.5f}-{sig.z_max:.5f}",
                              gate_events=[("EXECUTION", "BLOCK", "price left the AOI tolerance")])
        digits = spec.digits
        sl, tp = round(sig.sl, digits), round(sig.tp, digits)
        min_dist = spec.stops_level * spec.point
        if min_dist > 0 and (abs(px - sl) < min_dist or abs(tp - px) < min_dist):
            return ExecResult(STALE, f"INVALID_STOPS: SL/TP within broker stops level ({spec.stops_level} points)",
                              gate_events=[("EXECUTION", "BLOCK", "SL/TP inside stops level")])
        lots = floor_to_step(plan.lots, spec.lot_step)

        self.journal.insert_trade({  # raises DuplicateSignal → idempotency
            "signal_id": sid, "symbol": sig.symbol, "direction": sig.direction, "status": "PENDING",
            "lots": lots, "sl": sl, "tp": tp, "planned_entry": sig.entry, "rr_planned": sig.rr,
            "pillars": sig.pillars, "pattern_type": sig.pattern_type, "candle_signal": sig.candle_signal,
            "risk_money": plan.risk_money, "timeframe": sig.timeframe, "is_backtest": 0})

        def retry_check(price: float) -> str | None:
            if not self.can_trade():
                return "bot disabled during retries"
            if not entry_still_valid(sig.direction, price, sig.z_min, sig.z_max, sig.pip_size, tol):
                return "PRICE_LEFT_ZONE during retries"
            abort, rr = rr_slippage_abort(sig.direction, price, sl, tp, min_rr, rr_tol)
            return f"R:R {rr:.2f} at retry price below threshold" if abort else None

        req = {"action": C.TRADE_ACTION_DEAL, "symbol": sig.symbol, "volume": lots,
               "type": C.ORDER_TYPE_BUY if sig.direction == BUY else C.ORDER_TYPE_SELL,
               "sl": sl, "tp": tp, "deviation": cfg.mt5.max_slippage_points, "magic": self.magic,
               "comment": comment, "type_time": C.ORDER_TIME_GTC}
        res = self._send(req, spec, retry_check)
        rc = int(res.get("retcode", -1))
        naked = False
        if rc == C.RET_INVALID_STOPS:
            # 03 §2: broker rejected SL/TP on the market order → place, then attach immediately
            log.warning("%s SL/TP rejected on placement (%s) — placing then attaching", sid, res.get("comment"))
            req.pop("sl"), req.pop("tp")
            res = self._send(req, spec, retry_check)
            rc = int(res.get("retcode", -1))
            naked = True
        if rc not in FILLED:
            msg = f"order not executed: retcode={rc} ({res.get('comment')})"
            self.journal.update_trade(sid, status="FAILED", exit_reason="ORDER_REJECTED")
            log.error("%s %s%s", sid, msg, " (permanent)" if rc in PERMANENT else "")
            return ExecResult(FAILED, msg, retcode=rc, gate_events=[("EXECUTION", "BLOCK", msg)])

        fill = float(res.get("price") or px)
        order_ticket = int(res.get("order") or 0)
        pos = self._find_position(order_ticket, comment, sig.symbol)
        if pos is None:
            log.critical("%s executed (order %s) but the position is not visible yet — the monitor will verify it",
                         sid, order_ticket)
            self.journal.update_trade(sid, status="OPEN", ticket=order_ticket, entry_price=fill, entry_time=iso(now))
            return ExecResult(FILLED_STATUS, "filled; position verification deferred to the monitor", order_ticket,
                              fill, lots, rc)
        ticket, fill = pos["ticket"], pos["entry_price"] or fill
        self.journal.update_trade(sid, status="OPEN", ticket=ticket, entry_price=fill, entry_time=iso(now),
                                  lots=pos["lots"])

        if pos["sl"] == 0 or pos["tp"] == 0 or naked:
            mres = self.modify_sltp(pos, sl, tp)
            pos2 = self._find_position(ticket, comment, sig.symbol)
            if pos2 is not None and (pos2["sl"] == 0 or pos2["tp"] == 0):
                log.critical("%s SL/TP could not be attached (modify retcode %s) — CLOSING position %s",
                             sid, mres.get("retcode"), ticket)
                self.journal.update_trade(sid, status="CLOSING", exit_reason="SAFETY_CLOSE")
                cres = self.close_position(pos2, spec, "MSC|safety")
                note = "" if _ok(cres) else " — CLOSE FAILED, the monitor will retry"
                return ExecResult(ABORTED, f"SL/TP attach failed — position closed{note}", ticket, fill, pos["lots"],
                                  rc, [("EXECUTION", "BLOCK", f"SL/TP attach failed → safety close{note}")])
            if pos2 is None:
                log.critical("%s position %s vanished during SL/TP verification — monitor will reconcile", sid, ticket)

        abort, rr = rr_slippage_abort(sig.direction, fill, sl, tp, min_rr, rr_tol)
        if abort:
            log.warning("%s RR_SLIPPAGE_ABORT: fill %.5f realized R:R %.2f < %.2f − %.2f — closing", sid, fill, rr,
                        min_rr, rr_tol)
            self.journal.update_trade(sid, status="CLOSING", exit_reason="RR_SLIPPAGE_ABORT")
            cres = self.close_position(pos, spec, "MSC|rr-abort")
            note = "" if _ok(cres) else " (close failed — position keeps its SL/TP)"
            return ExecResult(ABORTED, f"RR_SLIPPAGE_ABORT realized R:R {rr:.2f}{note}", ticket, fill, pos["lots"], rc,
                              [("RR_SLIPPAGE", "BLOCK", f"realized R:R {rr:.2f} after slippage{note}")])

        log.info("EXECUTED %s %s %.2f lots @ %.5f SL %.5f TP %.5f (R:R %.2f) ticket %s", sid, sig.direction,
                 pos["lots"], fill, sl, tp, rr, ticket)
        return ExecResult(FILLED_STATUS, f"filled @ {fill}", ticket, fill, pos["lots"], rc,
                          [("RR_SLIPPAGE", "PASS", f"realized R:R {rr:.2f}"), ("EXECUTION", "PASS", f"ticket {ticket}")])
