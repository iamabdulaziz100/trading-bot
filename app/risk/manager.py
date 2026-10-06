"""Risk manager — gates a TradeSignal into an approved OrderPlan (03, 01 §3 step 5).

Gate order: ENABLED → NEWS → DAILY_LOSS → MAX_TRADES_DAY → CONCURRENCY → SYMBOL_DUP → SIZING.
Every evaluated gate is returned for the risk-decision journal (03 §7); evaluation stops at the
first BLOCK. Used unchanged by live trading and the backtester.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import BotConfig
from app.models import TradeSignal
from app.risk.sizing import compute_lots, risk_pct_for
from app.symbols import SymbolSpec

PASS, BLOCK = "PASS", "BLOCK"


@dataclass(slots=True)
class GateResult:
    gate: str
    result: str
    details: str


@dataclass
class RiskContext:
    enabled: bool
    balance: float
    equity: float
    open_symbols: list[str]  # symbols with an open bot position
    trades_today: int
    daily_cutoff_hit: bool
    daily_detail: str = ""
    news_blocked: bool = False
    news_reason: str = ""


@dataclass
class RiskDecision:
    approved: bool
    lots: float = 0.0
    risk_money: float = 0.0
    risk_pct: float = 0.0
    gates: list[GateResult] = field(default_factory=list)
    reason: str | None = None


class RiskManager:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg

    def evaluate(self, sig: TradeSignal, ctx: RiskContext, spec: SymbolSpec | None) -> RiskDecision:
        r, t = self.cfg.risk, self.cfg.trade
        gates: list[GateResult] = []

        def block(gate: str, detail: str) -> RiskDecision:
            gates.append(GateResult(gate, BLOCK, detail))
            return RiskDecision(False, gates=gates, reason=f"{gate}: {detail}")

        if not ctx.enabled:
            return block("ENABLED", "bot disabled")
        gates.append(GateResult("ENABLED", PASS, "bot enabled"))

        if ctx.news_blocked:
            return block("NEWS", ctx.news_reason)
        gates.append(GateResult("NEWS", PASS, ctx.news_reason or "no event in window"))

        if ctx.daily_cutoff_hit:
            return block("DAILY_LOSS", ctx.daily_detail or "daily loss cutoff hit")
        gates.append(GateResult("DAILY_LOSS", PASS, ctx.daily_detail or "within limit"))

        if r.max_trades_per_day > 0:
            if ctx.trades_today >= r.max_trades_per_day:
                return block("MAX_TRADES_DAY", f"{ctx.trades_today} ≥ {r.max_trades_per_day}")
            gates.append(GateResult("MAX_TRADES_DAY", PASS, f"{ctx.trades_today} < {r.max_trades_per_day}"))

        if len(ctx.open_symbols) >= r.max_concurrent_trades:
            return block("CONCURRENCY", f"{len(ctx.open_symbols)} open ≥ cap {r.max_concurrent_trades}")
        gates.append(GateResult("CONCURRENCY", PASS, f"{len(ctx.open_symbols)} open < {r.max_concurrent_trades}"))

        if t.one_trade_per_symbol and sig.symbol in ctx.open_symbols:
            return block("SYMBOL_DUP", f"position already open on {sig.symbol}")
        gates.append(GateResult("SYMBOL_DUP", PASS, "no open position on symbol"))

        pct = risk_pct_for(sig.pillars, r)
        sz = compute_lots(ctx.balance, pct, sig.entry, sig.sl, spec)
        if not sz.ok:
            return block("SIZING", f"{sz.reason}: {sz.detail}")
        gates.append(GateResult("SIZING", PASS, sz.detail))
        return RiskDecision(True, sz.lots, sz.risk_money, pct, gates, None)
