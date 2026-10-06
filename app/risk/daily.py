"""Daily loss cutoff (03 §3) — shared by live trading and the backtester."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.models import EPS


def day_key(now_epoch: float, boundary: str, server_offset_sec: int = 0) -> str:
    """Trading day label. ``server`` = broker server midnight, ``utc`` = UTC midnight."""
    shift = server_offset_sec if boundary == "server" else 0
    return datetime.fromtimestamp(now_epoch + shift, tz=timezone.utc).strftime("%Y-%m-%d")


@dataclass
class DailyState:
    day: str | None
    start_balance: float
    pnl: float
    pnl_pct: float
    limit_pct: float
    limit_money: float
    used_fraction: float
    cutoff_hit: bool


class DailyLossTracker:
    """Snapshot balance at the day boundary; day P/L = equity − day_start_balance; when it is
    ≤ −max_daily_loss_pct % of the start balance, new entries halt until the next boundary."""

    def __init__(self, max_daily_loss_pct: float):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.day: str | None = None
        self.start_balance = 0.0
        self.pnl = 0.0
        self.cutoff_hit = False
        self.new_day_started = False

    def restore(self, day: str, start_balance: float, cutoff_hit: bool) -> None:
        self.day, self.start_balance, self.cutoff_hit = day, start_balance, cutoff_hit

    def update(self, key: str, balance: float, equity: float) -> bool:
        """Returns True if the cutoff is (or already was) hit for the current day."""
        self.new_day_started = False
        if key != self.day:
            self.day, self.start_balance, self.cutoff_hit = key, balance, False
            self.new_day_started = True
        self.pnl = equity - self.start_balance
        limit = self.max_daily_loss_pct / 100.0 * self.start_balance
        if self.start_balance > 0 and self.pnl <= -limit + EPS:
            self.cutoff_hit = True
        return self.cutoff_hit

    def state(self) -> DailyState:
        limit_money = self.max_daily_loss_pct / 100.0 * self.start_balance
        used = (-self.pnl / limit_money) if (limit_money > 0 and self.pnl < 0) else 0.0
        pct = (self.pnl / self.start_balance * 100.0) if self.start_balance else 0.0
        return DailyState(self.day, self.start_balance, self.pnl, pct, self.max_daily_loss_pct,
                          limit_money, used, self.cutoff_hit)
