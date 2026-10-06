"""Canonical data types (02 §A)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

BUY = "BUY"
SELL = "SELL"

BULLISH = "BULLISH"
BEARISH = "BEARISH"
UNDEFINED = "UNDEFINED"

HIGH = "HIGH"
LOW = "LOW"

# Pattern types (02 §H)
BREAK_RETEST = "BREAK_RETEST"
HS_NECKLINE_BREAK = "HS_NECKLINE_BREAK"
INV_HS_NECKLINE_BREAK = "INV_HS_NECKLINE_BREAK"

# Candle signal types (02 §I)
BULL_ENGULF = "BULL_ENGULF"
BEAR_ENGULF = "BEAR_ENGULF"
MORNING_STAR = "MORNING_STAR"
EVENING_STAR = "EVENING_STAR"
HAMMER = "HAMMER"
SHOOTING_STAR = "SHOOTING_STAR"
ALL_TRIGGERS = (BULL_ENGULF, BEAR_ENGULF, MORNING_STAR, EVENING_STAR, HAMMER, SHOOTING_STAR)

# Structure events
BOS_BULLISH = "BOS_BULLISH"
BOS_BEARISH = "BOS_BEARISH"
INIT_BULLISH = "INIT_BULLISH"
INIT_BEARISH = "INIT_BEARISH"
LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"
SNAKE_NO_PIVOT = "SNAKE_NO_PIVOT"

# Alignment results (02 §F)
LONG = "LONG"
SHORT = "SHORT"
NEUTRAL_FILTER = "NEUTRAL_FILTER"

# Evaluation decisions / reasons
EXECUTED = "EXECUTED"
REJECTED = "REJECTED"
SIGNAL = "SIGNAL"  # strategy says trade; risk/execution decide EXECUTED vs REJECTED
LOW_CONFLUENCE = "LOW_CONFLUENCE"
NO_TRIGGER = "NO_TRIGGER"
RR_CAP_REJECT = "RR_CAP_REJECT"
INVALID_SL = "INVALID_SL"

EPS = 1e-9  # float guard for exact-threshold comparisons (prices and ratios)


def direction_sign(direction: str) -> int:
    return 1 if direction == BUY else -1


@dataclass(slots=True)
class Candle:
    time: int  # epoch seconds UTC of candle OPEN
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body_high(self) -> float:
        return max(self.open, self.close)

    @property
    def body_low(self) -> float:
        return min(self.open, self.close)


@dataclass(slots=True)
class Pivot:
    index: int
    time: int
    price: float
    kind: str  # HIGH | LOW
    confirmed: bool = True
    confirmed_index: int = -1  # bar at which the pivot was confirmed (index + N)
    label: str = ""  # HH | LH | HL | LL (relative to previous same-kind pivot)


@dataclass(slots=True)
class StructureEvent:
    type: str
    bar: int
    time: int
    price: float
    tf: str = ""
    detail: str = ""


@dataclass(slots=True)
class StructureSnapshot:
    state: str
    active_HH: float | None
    active_HL: float | None
    active_LH: float | None
    active_LL: float | None
    last_break_bar: int | None

    @property
    def trend(self) -> int:
        return 1 if self.state == BULLISH else -1 if self.state == BEARISH else 0


@dataclass(slots=True)
class Zone:
    """Area of interest (02 §G)."""

    tf: str
    z_min: float
    z_max: float
    touches: int
    valid: bool
    members: tuple[float, ...] = ()

    @property
    def center(self) -> float:
        return (self.z_min + self.z_max) / 2.0

    @property
    def width(self) -> float:
        return self.z_max - self.z_min

    def key(self) -> str:
        return f"{self.tf}:{self.z_min:.6f}:{self.z_max:.6f}"


@dataclass(slots=True)
class PatternEvent:
    type: str
    direction: str
    level: float
    bar: int
    time: int
    detail: str = ""


@dataclass(slots=True)
class CandleSignal:
    type: str
    direction: str
    sl_ref: float  # invalidation wick: formation min low (BUY) / max high (SELL)


@dataclass(slots=True)
class PillarResult:
    p1: bool
    p2: bool
    p3: bool
    p4: bool
    ema_ok: bool | None
    details: dict[str, Any] = field(default_factory=dict)

    def count(self, ema_counts_as_confluence: bool = False) -> int:
        n = int(self.p1) + int(self.p2) + int(self.p3) + int(self.p4)
        if ema_counts_as_confluence and self.ema_ok:
            n += 1
        return n


@dataclass(slots=True)
class TradeSignal:
    signal_id: str
    symbol: str
    timeframe: str
    direction: str
    entry: float
    sl: float
    tp: float
    rr: float
    pillars: int
    reasons: list[str]
    bar_time: int  # trigger candle open time
    signal_time: int  # trigger candle close time (signal generation instant)
    zone_tf: str
    z_min: float
    z_max: float
    candle_signal: str
    pattern_type: str | None
    ema_ok: bool | None
    pip_size: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Evaluation:
    """One confluence checklist evaluation at an AOI (journaled pass or fail)."""

    symbol: str
    timeframe: str
    bar_time: int
    signal_time: int
    direction: str
    pillars: PillarResult
    pillar_count: int
    candle_signal: str | None
    pattern: str | None
    decision: str  # SIGNAL | REJECTED
    reason: str | None
    signal_id: str
    signal: TradeSignal | None = None


@dataclass(slots=True)
class StepResult:
    """Everything that happened when one closed candle was fed to a symbol pipeline."""

    symbol: str
    tf: str
    bar: int
    candle: Candle
    new_pivots: list[Pivot] = field(default_factory=list)
    structure_events: list[StructureEvent] = field(default_factory=list)
    pattern_events: list[PatternEvent] = field(default_factory=list)
    skip_reason: str | None = None
    evaluation: Evaluation | None = None
