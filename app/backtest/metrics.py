"""Backtest metrics (05 §5)."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any


def _dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def drawdown_series(points: list[tuple[int, float]]) -> tuple[list[dict], float, float, float]:
    """Returns (series [{time, dd_pct}], max_dd_pct, max_dd_money, max_dd_duration_days)."""
    series, peak, peak_t = [], None, None
    max_pct = max_money = 0.0
    max_dur = 0.0
    in_dd_since: int | None = None
    for t, v in points:
        if peak is None or v >= peak:
            if in_dd_since is not None:
                max_dur = max(max_dur, (t - in_dd_since) / 86400)
                in_dd_since = None
            peak, peak_t = v, t
        dd_money = peak - v
        dd_pct = dd_money / peak * 100 if peak else 0.0
        if dd_money > 0 and in_dd_since is None:
            in_dd_since = peak_t
        max_pct, max_money = max(max_pct, dd_pct), max(max_money, dd_money)
        series.append({"time": t, "dd_pct": -round(dd_pct, 4)})
    if in_dd_since is not None and points:
        max_dur = max(max_dur, (points[-1][0] - in_dd_since) / 86400)
    return series, max_pct, max_money, max_dur


def sharpe_daily(points: list[tuple[int, float]], start_ts: int, end_ts: int) -> float | None:
    """Annualised Sharpe of daily balance returns (weekdays, balance forward-filled)."""
    if len(points) < 2:
        return None
    by_day: dict[str, float] = {}
    for t, v in points:
        by_day[_dt(t).strftime("%Y-%m-%d")] = v
    day, last = start_ts // 86400 * 86400, points[0][1]
    balances = []
    while day <= end_ts:
        key = _dt(day).strftime("%Y-%m-%d")
        if key in by_day:
            last = by_day[key]
        if _dt(day).weekday() < 5:
            balances.append(last)
        day += 86400
    rets = [(b / a - 1) for a, b in zip(balances, balances[1:]) if a]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    return round(mean / sd * math.sqrt(252), 4) if sd > 0 else None


def _group(trades: list[dict], key) -> list[dict]:
    g: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        g[key(t)].append(t)
    out = []
    for k in sorted(g):
        ts = g[k]
        wins = sum(1 for t in ts if t["pnl_money"] > 0)
        out.append({"trades": len(ts), "win_rate": round(wins / len(ts), 4) if ts else 0.0,
                    "net_pnl": round(sum(t["pnl_money"] for t in ts), 2),
                    "sum_r": round(sum(t["r_multiple"] or 0 for t in ts), 4), "_k": k})
    return out


def compute_metrics(trades: list[dict[str, Any]], initial_balance: float, start_ts: int, end_ts: int,
                    rejected_signals: int = 0) -> dict[str, Any]:
    """``trades``: closed trades with pnl_money, r_multiple, exit_ts, symbol, pillars, rr_realized."""
    trades = sorted(trades, key=lambda t: t["exit_ts"])
    n = len(trades)
    wins = [t for t in trades if t["pnl_money"] > 0]
    losses = [t for t in trades if t["pnl_money"] <= 0]
    gp = sum(t["pnl_money"] for t in wins)
    gl = sum(t["pnl_money"] for t in losses)
    net = gp + gl
    points: list[tuple[int, float]] = [(start_ts, initial_balance)]
    bal = initial_balance
    for t in trades:
        bal += t["pnl_money"]
        points.append((t["exit_ts"], bal))
    dd, max_pct, max_money, max_dur = drawdown_series(points)
    rs = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    rr = [t["rr_realized"] for t in trades if t.get("rr_realized") is not None]
    metrics = {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n, 4) if n else 0.0,
        "net_pnl": round(net, 2), "net_pnl_pct": round(net / initial_balance * 100, 4) if initial_balance else 0.0,
        "gross_profit": round(gp, 2), "gross_loss": round(gl, 2),
        "profit_factor": round(gp / abs(gl), 4) if gl < 0 else None,
        "expectancy_r": round(sum(rs) / len(rs), 4) if rs else 0.0,
        "avg_rr_realized": round(sum(rr) / len(rr), 4) if rr else 0.0,
        "max_drawdown_pct": round(max_pct, 4), "max_drawdown_money": round(max_money, 2),
        "max_drawdown_duration_days": round(max_dur, 2),
        "sharpe_daily": sharpe_daily(points, start_ts, end_ts),
        "start_balance": round(initial_balance, 2), "final_balance": round(bal, 2),
        "pillars_distribution": {str(k): v for k, v in sorted(Counter(t.get("pillars") for t in trades).items())
                                 if k is not None},
        "rejected_signals": rejected_signals,
    }
    per_symbol = [{"symbol": g.pop("_k"), **g} for g in _group(trades, lambda t: t["symbol"])]
    per_month = [{"month": g.pop("_k"), **g} for g in _group(trades, lambda t: _dt(t["exit_ts"]).strftime("%Y-%m"))]
    equity = [{"time": t, "equity": round(v, 2)} for t, v in points]
    return {"metrics": metrics, "per_symbol": per_symbol, "per_month": per_month, "equity": equity, "drawdown": dd}
