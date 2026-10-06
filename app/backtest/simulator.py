"""Event-driven backtester (05).

Replays historical candles through the EXACT same strategy pipeline (``SymbolPipeline``) and
risk code (``RiskManager``, ``DailyLossTracker``, sizing, R:R checks) as live trading. Only the
data source (CSV) and the executor (simulated fills below) differ.

Replay clock: candle-close events of all symbols/TFs are merged by close time (higher TF first
at equal times), so an HTF candle is only "revealed" once it is fully closed → no lookahead.

Execution simulation (05 §3, prices in the data are BID):
* signal on close of candle t → entry at the open of the next execution-TF candle;
  BUY fills at open + spread + slippage (ask), SELL at open − slippage (bid).
* pre-trade checks identical to live: signal TTL, price within entry_zone_tolerance of the AOI.
* SL/TP checked on every candle from the fill candle on, using high/low (shorts on ask = bid +
  spread). Both inside one candle → SL first (pessimistic). Opening beyond SL/TP → fill at open.
* Slippage is adverse on entries and SL exits; commission per lot round-turn at close.
* Simplified margin: lots × contract × base-ccy USD value / leverage must fit in free margin.
"""
from __future__ import annotations

import logging
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.backtest.metrics import compute_metrics
from app.config import BotConfig
from app.core.pipeline import SymbolPipeline
from app.core.trade_params import entry_still_valid, rr_slippage_abort
from app.models import BUY, Candle, Evaluation, TradeSignal
from app.risk.daily import DailyLossTracker, day_key
from app.risk.manager import RiskContext, RiskDecision, RiskManager
from app.risk.news import NewsCalendar
from app.symbols import BTSymbolSpec
from app.timeframes import event_sort_key

log = logging.getLogger("backtest")


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_epoch(s: str, end: bool = False) -> int:
    dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) + (86400 - 1 if end else 0)


@dataclass
class BTParams:
    symbols: list[str]
    date_from: str
    date_to: str
    initial_balance: float | None = None
    spread_pips: float | None = None
    slippage_pips: float | None = None
    commission_per_lot: float | None = None
    risk_per_trade_pct: float | None = None


@dataclass
class SimPosition:
    signal: TradeSignal
    lots: float
    risk_money: float
    entry_ts: int
    entry_price: float
    rr_realized: float
    spec: BTSymbolSpec
    spread: float
    slip: float
    commission: float
    fill_bar_time: int
    pillars: int


@dataclass
class _Pending:
    signal: TradeSignal
    decision: RiskDecision
    evaluation: Evaluation


@dataclass
class BTState:
    balance: float
    positions: dict[str, SimPosition] = field(default_factory=dict)
    pending: dict[str, _Pending] = field(default_factory=dict)
    last_close: dict[str, float] = field(default_factory=dict)
    trades: list[dict[str, Any]] = field(default_factory=list)
    rejected: deque = field(default_factory=lambda: deque(maxlen=2000))
    rejected_count: int = 0
    entries_by_day: Counter = field(default_factory=Counter)
    risk_events: list[tuple] = field(default_factory=list)


class BacktestEngine:
    def __init__(self, cfg: BotConfig, params: BTParams, data: dict[str, dict[str, list[Candle]]],
                 specs: dict[str, BTSymbolSpec], news: NewsCalendar | None = None, notes: list[str] | None = None):
        self.cfg = cfg
        self.params = params
        self.data = data
        self.specs = specs
        self.news = news
        self.notes = list(notes or [])
        b = cfg.backtest
        self.initial_balance = params.initial_balance or b.initial_balance
        self.slippage_pips = b.slippage_pips if params.slippage_pips is None else params.slippage_pips
        self.commission = b.commission_per_lot if params.commission_per_lot is None else params.commission_per_lot
        self.leverage = b.leverage
        self.start_ts = date_epoch(params.date_from)
        self.end_ts = date_epoch(params.date_to, end=True)
        self.signal_seq = 0

    # ── helpers ────────────────────────────────────────────────────────────────────────
    def _spread_pips(self, sym: str) -> float:
        if self.params.spread_pips is not None:
            return self.params.spread_pips
        sp = self.specs[sym].spread_pips
        return self.cfg.backtest.spread_pips_default if sp is None else sp

    def _commission(self, sym: str) -> float:
        if self.params.commission_per_lot is not None:
            return self.params.commission_per_lot
        c = self.specs[sym].commission_per_lot
        return self.commission if c is None else c

    def _floating(self, st: BTState) -> float:
        total = 0.0
        for sym, p in st.positions.items():
            px = st.last_close.get(sym)
            if px is None:
                continue
            exit_px = px if p.signal.direction == BUY else px + p.spread
            total += self._gross_pnl(p, exit_px)
        return total

    def _gross_pnl(self, p: SimPosition, exit_price: float) -> float:
        diff = (exit_price - p.entry_price) if p.signal.direction == BUY else (p.entry_price - exit_price)
        return diff / p.spec.pip_size * p.spec.pip_value(exit_price) * p.lots

    def _used_margin(self, st: BTState) -> float:
        return sum(p.lots * p.spec.contract_size * p.spec.base_value_usd(p.entry_price) / self.leverage
                   for p in st.positions.values())

    def _signal_row(self, ev: Evaluation, decision: str, reason: str | None) -> dict[str, Any]:
        self.signal_seq += 1
        p = ev.pillars
        return {"id": self.signal_seq, "ts": _iso(ev.signal_time), "symbol": ev.symbol, "timeframe": ev.timeframe,
                "direction": ev.direction, "pillar1": p.p1, "pillar2": p.p2, "pillar3": p.p3, "pillar4": p.p4,
                "ema_ok": p.ema_ok, "pillar_count": ev.pillar_count, "candle_signal": ev.candle_signal,
                "pattern": ev.pattern, "decision": decision, "reason": reason, "signal_id": ev.signal_id,
                "details": p.details, "is_backtest": True}

    def _reject(self, st: BTState, ev: Evaluation, reason: str | None) -> None:
        st.rejected_count += 1
        st.rejected.append(self._signal_row(ev, "REJECTED", reason))

    def _close(self, st: BTState, sym: str, exit_ts: int, exit_price: float, reason: str) -> None:
        p = st.positions.pop(sym)
        pnl = self._gross_pnl(p, exit_price) - p.commission * p.lots
        st.balance += pnl
        s = p.signal
        st.trades.append({
            "id": len(st.trades) + 1, "signal_id": s.signal_id, "symbol": sym, "direction": s.direction,
            "status": "CLOSED", "lots": p.lots, "entry_time": _iso(p.entry_ts), "entry_price": round(p.entry_price, 6),
            "sl": s.sl, "tp": s.tp, "exit_time": _iso(exit_ts), "exit_price": round(exit_price, 6),
            "exit_reason": reason, "pnl_money": round(pnl, 2), "r_multiple": round(pnl / p.risk_money, 4)
            if p.risk_money else None, "risk_money": round(p.risk_money, 2), "pillars": p.pillars,
            "pattern_type": s.pattern_type, "candle_signal": s.candle_signal, "ticket": None, "is_backtest": True,
            "created_at": _iso(s.signal_time), "timeframe": s.timeframe, "planned_entry": s.entry,
            "rr_planned": round(s.rr, 4), "exit_ts": exit_ts, "rr_realized": round(p.rr_realized, 4)})

    # ── execution simulation ───────────────────────────────────────────────────────────
    def _on_exec_candle(self, st: BTState, sym: str, c: Candle) -> None:
        pend = st.pending.pop(sym, None)
        if pend is not None:
            self._fill(st, sym, c, pend)
        p = st.positions.get(sym)
        if p is None:
            return
        just_filled = p.fill_bar_time == c.time
        sl, tp, slip, spread = p.signal.sl, p.signal.tp, p.slip, p.spread
        if p.signal.direction == BUY:
            if not just_filled and c.open <= sl:
                return self._close(st, sym, c.time, c.open - slip, "SL")  # gap through SL
            if not just_filled and c.open >= tp:
                return self._close(st, sym, c.time, c.open, "TP")  # gap through TP
            if c.low <= sl:  # SL first when both are inside the candle (pessimistic)
                return self._close(st, sym, c.time, sl - slip, "SL")
            if c.high >= tp:
                return self._close(st, sym, c.time, tp, "TP")
        else:
            a_open, a_high, a_low = c.open + spread, c.high + spread, c.low + spread
            if not just_filled and a_open >= sl:
                return self._close(st, sym, c.time, a_open + slip, "SL")
            if not just_filled and a_open <= tp:
                return self._close(st, sym, c.time, a_open, "TP")
            if a_high >= sl:
                return self._close(st, sym, c.time, sl + slip, "SL")
            if a_low <= tp:
                return self._close(st, sym, c.time, tp, "TP")
        return None

    def _fill(self, st: BTState, sym: str, c: Candle, pend: _Pending) -> None:
        cfg, sig, dec = self.cfg, pend.signal, pend.decision
        spec = self.specs[sym]
        pip = spec.pip_size
        spread, slip = self._spread_pips(sym) * pip, self.slippage_pips * pip
        age = c.time - sig.signal_time
        if age > cfg.trade.signal_ttl_seconds:
            st.risk_events.append((c.time, sym, sig.signal_id, "EXECUTION", "BLOCK", f"SIGNAL_TTL age {age}s"))
            return self._reject(st, pend.evaluation, f"STALE: SIGNAL_TTL (next bar {age}s after signal)")
        fill = c.open + spread + slip if sig.direction == BUY else c.open - slip
        if not entry_still_valid(sig.direction, fill, sig.z_min, sig.z_max, pip, cfg.trade.entry_zone_tolerance_pips):
            st.risk_events.append((c.time, sym, sig.signal_id, "EXECUTION", "BLOCK", "price left AOI tolerance"))
            return self._reject(st, pend.evaluation, "STALE: PRICE_LEFT_ZONE")
        equity = st.balance + self._floating(st)
        required = dec.lots * spec.contract_size * spec.base_value_usd(fill) / self.leverage
        if required > equity - self._used_margin(st):
            st.risk_events.append((c.time, sym, sig.signal_id, "MARGIN", "BLOCK", f"required {required:.2f}"))
            return self._reject(st, pend.evaluation, f"MARGIN: required {required:.2f} > free margin")
        abort, rr = rr_slippage_abort(sig.direction, fill, sig.sl, sig.tp, cfg.trade.min_rr, cfg.trade.rr_slippage_tolerance)
        pos = SimPosition(sig, dec.lots, dec.risk_money, c.time, fill, rr, spec, spread, slip, self._commission(sym),
                          c.time, sig.pillars)
        st.positions[sym] = pos
        st.entries_by_day[day_key(c.time, "utc")] += 1
        if abort:
            st.risk_events.append((c.time, sym, sig.signal_id, "RR_SLIPPAGE", "BLOCK", f"realized R:R {rr:.2f}"))
            exit_px = c.open - slip if sig.direction == BUY else c.open + spread + slip
            self._close(st, sym, c.time, exit_px, "RR_SLIPPAGE_ABORT")
        return None

    # ── main loop ──────────────────────────────────────────────────────────────────────
    def run(self, progress: Callable[[float], None] | None = None) -> dict[str, Any]:
        cfg = self.cfg
        exec_tf = cfg.exec_tf
        pipes = {s: SymbolPipeline(s, cfg, self.specs[s].pip_size) for s in self.data}
        events = []
        for sym, tfs in self.data.items():
            for tf, candles in tfs.items():
                for c in candles:
                    events.append((event_sort_key(c.time, tf, sym), sym, tf, c))
        events.sort(key=lambda e: e[0])
        risk = RiskManager(cfg)
        daily = DailyLossTracker(cfg.risk.max_daily_loss_pct)
        st = BTState(balance=self.initial_balance)
        apply_news = cfg.backtest.apply_news_filter and self.news is not None
        total = len(events) or 1
        last_progress = 0.0
        last_t = self.start_ts

        for i, (key, sym, tf, c) in enumerate(events):
            close_t = key[0]
            if close_t > self.end_ts:
                break
            last_t = close_t
            if tf == exec_tf:
                self._on_exec_candle(st, sym, c)
                st.last_close[sym] = c.close
                if close_t >= self.start_ts:
                    daily.update(day_key(close_t, "utc"), st.balance, st.balance + self._floating(st))
            res = pipes[sym].on_candle_closed(tf, c)
            ev = res.evaluation
            if ev is not None and close_t >= self.start_ts:
                if ev.signal is None:
                    self._reject(st, ev, ev.reason)
                else:
                    sig = ev.signal
                    spec = self.specs[sym].to_symbol_spec(sig.entry)
                    equity = st.balance + self._floating(st)
                    dkey = day_key(close_t, "utc")
                    cut = daily.update(dkey, st.balance, equity)
                    ds = daily.state()
                    nb, nr = False, "news filter not applied in backtest"
                    if apply_news:
                        nev = self.news.blackout_event(sym, close_t, cfg.news)  # type: ignore[union-attr]
                        nb, nr = (nev is not None), (f"{nev.currency} {nev.title}" if nev else "no event in window")
                    ctx = RiskContext(enabled=True, balance=st.balance, equity=equity,
                                      open_symbols=list(st.positions) + list(st.pending),
                                      trades_today=st.entries_by_day[dkey], daily_cutoff_hit=cut,
                                      daily_detail=f"day P/L {ds.pnl:.2f} ({ds.pnl_pct:.2f}%)",
                                      news_blocked=nb, news_reason=nr)
                    decision = risk.evaluate(sig, ctx, spec)
                    for g in decision.gates:
                        st.risk_events.append((close_t, sym, sig.signal_id, g.gate, g.result, g.details))
                    if decision.approved:
                        st.pending[sym] = _Pending(sig, decision, ev)
                    else:
                        self._reject(st, ev, decision.reason)
            if progress and (i / total - last_progress) >= 0.01:
                last_progress = i / total
                progress(last_progress)

        for sym in list(st.positions):
            px = st.last_close[sym]
            p = st.positions[sym]
            exit_px = px if p.signal.direction == BUY else px + p.spread
            self._close(st, sym, last_t, exit_px, "END_OF_TEST")
        if progress:
            progress(1.0)

        out = compute_metrics(st.trades, self.initial_balance, self.start_ts, self.end_ts, st.rejected_count)
        if not cfg.backtest.apply_news_filter:
            self.notes.append("News filter not applied (no historical calendar bundled — 05 §4)")
        elif self.news is None:
            self.notes.append("News filter requested but no calendar data available")
        if cfg.risk.day_boundary == "server":
            self.notes.append("Daily-loss day boundary evaluated in UTC (CSV data carries no broker offset)")
        out.update({
            "trades": [{k: v for k, v in t.items() if k != "exit_ts"} for t in st.trades],
            "rejected": list(st.rejected),
            "risk_events": [{"ts": _iso(t), "symbol": s, "signal_id": sid, "gate": g, "result": r, "details": d}
                            for t, s, sid, g, r, d in st.risk_events[-5000:]],
            "notes": self.notes,
        })
        return out
