"""Execution engine (04 §4, 03 §2) — bracket market orders, strict set & forget.

1. Pre-trade: signal age ≤ signal_ttl_seconds and price within entry_zone_tolerance_pips of
   the AOI edge, else drop.
2. Idempotency: a PENDING trade row is inserted under the UNIQUE signal_id BEFORE sending.
3. Filling mode: FOK → IOC → RETURN filtered by symbol capabilities; INVALID_FILL cycles on.
4. Retries: transient retcodes (requote, price changed/off, timeout, …) up to 3× with a fresh
   tick; NO_MONEY / MARKET_CLOSED / TRADE_DISABLED fail permanently.
5. Post-fill: verify SL/TP landed on the position; else order_modify once; else close
   immediately (CRITICAL). Naked positions are never tolerated.
6. Realized R:R from the actual fill < min_rr − rr_slippage_tolerance → close immediately
   (RR_SLIPPAGE_ABORT).
After placement the bot NEVER modifies a position (no breakeven / trailing / partials).
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

TRANSIENT = {C.RET_REQUOTE, C.RET_PRICE_CHANGED, C.RET_PRICE_OFF, C.RET_TIMEOUT, C.RET_TOO_MANY_REQUESTS,
             C.RET_CONNECTION, C.RET_LOCKED, C.RET_REJECT, C.RET_ERROR, -1}
PERMANENT = {C.RET_NO_MONEY, C.RET_MARKET_CLOSED, C.RET_TRADE_DISABLED}
FILLED = {C.RET_DONE, C.RET_DONE_PARTIAL, C.RET_PLACED}

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


def _comment(signal_id: str) -> str:
    return f"MSC|{signal_id}"[:31]


class ExecutionEngine:
    def __init__(self, conn: MT5Connector, journal: Journal, cfg: Callable[[], BotConfig]):
        self.conn = conn
        self.journal = journal
        self.cfg = cfg
        self.preferred_filling: dict[str, int] = {}

    # ── helpers ────────────────────────────────────────────────────────────────────────
    def _send(self, req: dict, spec: SymbolSpec) -> dict:
        """order_send with filling-mode fallback and transient-retcode retries (fresh tick)."""
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
            res = self.conn.order_send(dict(req))
            rc = int(res.get("retcode", -1))
            if rc in FILLED:
                self.preferred_filling[spec.symbol] = fillings[fi]
                return res
            if rc == C.RET_INVALID_FILL:
                log.info("%s filling mode %s rejected — trying next", spec.symbol, fillings[fi])
                fi += 1
                continue
            if rc in TRANSIENT and retries < 3:
                retries += 1
                log.warning("%s order_send transient retcode %s (%s) — retry %d/3", spec.symbol, rc,
                            res.get("comment"), retries)
                time.sleep(0.5)
                continue
            return res
        return res

    def _find_position(self, ticket_hint: int, comment: str, symbol: str) -> dict | None:
        magic = self.cfg().mt5.magic_number
        for _ in range(6):
            for p in self.conn.positions(magic):
                if p["ticket"] == ticket_hint or p["identifier"] == ticket_hint or \
                        (p["symbol"] == symbol and p["comment"] == comment):
                    return p
            time.sleep(0.5)
        return None

    def close_position(self, pos: dict, spec: SymbolSpec, comment: str = "MSC|close") -> dict:
        cfg = self.cfg()
        req = {"action": C.TRADE_ACTION_DEAL, "symbol": pos["symbol"], "volume": pos["lots"],
               "type": C.ORDER_TYPE_SELL if pos["direction"] == BUY else C.ORDER_TYPE_BUY,
               "position": pos["ticket"], "deviation": cfg.mt5.max_slippage_points, "magic": cfg.mt5.magic_number,
               "comment": comment[:31], "type_time": C.ORDER_TIME_GTC}
        return self._send(req, spec)

    def _modify_sltp(self, pos: dict, sl: float, tp: float) -> dict:
        req = {"action": C.TRADE_ACTION_SLTP, "symbol": pos["symbol"], "position": pos["ticket"],
               "sl": sl, "tp": tp, "magic": self.cfg().mt5.magic_number}
        return self.conn.order_send(req)

    # ── main ───────────────────────────────────────────────────────────────────────────
    def execute(self, sig: TradeSignal, plan: RiskDecision, spec: SymbolSpec, now: float | None = None) -> ExecResult:
        cfg = self.cfg()
        now = time.time() if now is None else now
        sid = sig.signal_id

        age = now - sig.signal_time
        if age > cfg.trade.signal_ttl_seconds:
            return ExecResult(STALE, f"SIGNAL_TTL: signal age {age:.0f}s > {cfg.trade.signal_ttl_seconds}s",
                              gate_events=[("EXECUTION", "BLOCK", f"signal age {age:.0f}s > ttl")])
        tick = self.conn.tick(sig.symbol)
        px = tick["ask"] if sig.direction == BUY else tick["bid"]
        if not entry_still_valid(sig.direction, px, sig.z_min, sig.z_max, sig.pip_size,
                                 cfg.trade.entry_zone_tolerance_pips):
            return ExecResult(STALE, f"PRICE_LEFT_ZONE: price {px} > {cfg.trade.entry_zone_tolerance_pips} pips "
                                     f"from zone {sig.z_min:.5f}-{sig.z_max:.5f}",
                              gate_events=[("EXECUTION", "BLOCK", "price left the AOI tolerance")])

        digits = spec.digits
        sl, tp = round(sig.sl, digits), round(sig.tp, digits)
        lots = floor_to_step(plan.lots, spec.lot_step)
        try:
            self.journal.insert_trade({
                "signal_id": sid, "symbol": sig.symbol, "direction": sig.direction, "status": "PENDING",
                "lots": lots, "sl": sl, "tp": tp, "planned_entry": sig.entry, "rr_planned": sig.rr,
                "pillars": sig.pillars, "pattern_type": sig.pattern_type, "candle_signal": sig.candle_signal,
                "risk_money": plan.risk_money, "timeframe": sig.timeframe, "is_backtest": 0})
        except DuplicateSignal:
            return ExecResult(DUPLICATE, f"signal {sid} already executed (idempotency)")

        req = {"action": C.TRADE_ACTION_DEAL, "symbol": sig.symbol, "volume": lots,
               "type": C.ORDER_TYPE_BUY if sig.direction == BUY else C.ORDER_TYPE_SELL,
               "sl": sl, "tp": tp, "deviation": cfg.mt5.max_slippage_points, "magic": cfg.mt5.magic_number,
               "comment": _comment(sid), "type_time": C.ORDER_TIME_GTC}
        res = self._send(req, spec)
        rc = int(res.get("retcode", -1))
        naked_retry = False
        if rc == C.RET_INVALID_STOPS:
            # 03 §2: broker rejected SL/TP on the market order → place without, then modify
            log.warning("%s SL/TP rejected on placement (%s) — placing then modifying", sid, res.get("comment"))
            req.pop("sl"), req.pop("tp")
            res = self._send(req, spec)
            rc = int(res.get("retcode", -1))
            naked_retry = True
        if rc not in FILLED:
            msg = f"order_send failed retcode={rc} ({res.get('comment')})"
            self.journal.update_trade(sid, status="FAILED", exit_reason="ORDER_REJECTED")
            kind = "permanent" if rc in PERMANENT else "after retries"
            log.error("%s %s %s", sid, msg, kind)
            return ExecResult(FAILED, msg, retcode=rc, gate_events=[("EXECUTION", "BLOCK", msg)])

        fill = float(res.get("price") or px)
        order_ticket = int(res.get("order") or 0)
        pos = self._find_position(order_ticket, _comment(sid), sig.symbol)
        if pos is None:
            log.critical("%s filled (order %s) but the position was not found — check MT5 manually", sid, order_ticket)
            self.journal.update_trade(sid, status="OPEN", ticket=order_ticket, entry_price=fill, entry_time=iso(now))
            return ExecResult(FILLED_STATUS, "filled; position not found for verification", order_ticket, fill, lots, rc)
        ticket, fill = pos["ticket"], pos["entry_price"] or fill
        self.journal.update_trade(sid, status="OPEN", ticket=ticket, entry_price=fill, entry_time=iso(now),
                                  lots=pos["lots"])

        # SL/TP must be on the position — modify once, else close immediately
        if pos["sl"] == 0 or pos["tp"] == 0 or naked_retry:
            mres = self._modify_sltp(pos, sl, tp)
            pos2 = self._find_position(ticket, _comment(sid), sig.symbol)
            if pos2 is None or pos2["sl"] == 0 or pos2["tp"] == 0:
                log.critical("%s SL/TP could not be attached (modify retcode %s) — CLOSING position %s",
                             sid, mres.get("retcode"), ticket)
                self.journal.update_trade(sid, status="CLOSING", exit_reason="SAFETY_CLOSE")
                if pos2 is not None:
                    self.close_position(pos2, spec, "MSC|safety")
                return ExecResult(ABORTED, "SL/TP attach failed — position closed", ticket, fill, pos["lots"], rc,
                                  [("EXECUTION", "BLOCK", "SL/TP attach failed → safety close")])

        abort, rr = rr_slippage_abort(sig.direction, fill, sl, tp, cfg.trade.min_rr, cfg.trade.rr_slippage_tolerance)
        if abort:
            log.warning("%s RR_SLIPPAGE_ABORT: fill %.5f realized R:R %.2f < %.2f − %.2f — closing", sid, fill, rr,
                        cfg.trade.min_rr, cfg.trade.rr_slippage_tolerance)
            self.journal.update_trade(sid, status="CLOSING", exit_reason="RR_SLIPPAGE_ABORT")
            self.close_position(pos, spec, "MSC|rr-abort")
            return ExecResult(ABORTED, f"RR_SLIPPAGE_ABORT realized R:R {rr:.2f}", ticket, fill, pos["lots"], rc,
                              [("RR_SLIPPAGE", "BLOCK", f"realized R:R {rr:.2f} after slippage")])

        log.info("EXECUTED %s %s %.2f lots @ %.5f SL %.5f TP %.5f (R:R %.2f) ticket %s", sid, sig.direction,
                 pos["lots"], fill, sl, tp, rr, ticket)
        return ExecResult(FILLED_STATUS, f"filled @ {fill}", ticket, fill, pos["lots"], rc,
                          [("RR_SLIPPAGE", "PASS", f"realized R:R {rr:.2f}"), ("EXECUTION", "PASS", f"ticket {ticket}")])
