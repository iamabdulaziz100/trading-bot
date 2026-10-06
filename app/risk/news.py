"""News blackout (03 §4, 01 §6).

* Source: public economic-calendar JSON (ForexFactory weekly mirror) polled every
  ``news.poll_minutes``; manual CSV upload fallback (``date,time_utc,currency,impact,title``).
* Rule: no NEW entries from ``block_before_min`` before to ``block_after_min`` after an event
  of a configured impact level affecting EITHER currency of the symbol.
* Feed failure: ``allow`` (default, persistent warning) or ``block_all``.
"""
from __future__ import annotations

import csv
import io
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import NewsConfig
from app.pip_engine import split_fx

log = logging.getLogger("news")

DEFAULT_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
IMPACT_MAP = {"high": "high", "medium": "medium", "low": "low", "holiday": "holiday", "non-economic": "low"}


@dataclass(frozen=True)
class NewsEvent:
    ts_utc: int  # epoch seconds
    currency: str
    impact: str  # high | medium | low | holiday
    title: str
    source: str = "feed"

    def iso(self) -> str:
        return datetime.fromtimestamp(self.ts_utc, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ff_json(data: list[dict]) -> list[NewsEvent]:
    out: list[NewsEvent] = []
    for row in data:
        try:
            dt = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            impact = IMPACT_MAP.get(str(row.get("impact", "")).strip().lower(), "low")
            cur = str(row.get("country") or row.get("currency") or "").strip().upper()
            if len(cur) != 3:
                continue
            out.append(NewsEvent(int(dt.timestamp()), cur, impact, str(row.get("title", "")).strip(), "feed"))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def parse_csv(text: str) -> list[NewsEvent]:
    """CSV columns: date,time_utc,currency,impact,title (header optional).
    date: YYYY-MM-DD (or YYYY.MM.DD), time_utc: HH:MM[:SS]."""
    out: list[NewsEvent] = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row or row[0].strip().lower() in ("date", "#date") or row[0].startswith("#"):
            continue
        if len(row) < 5:
            raise ValueError(f"bad CSV row (need 5 columns): {row}")
        d, t, cur, imp, title = (c.strip() for c in row[:5])
        d = d.replace(".", "-").replace("/", "-")
        t = t if t.count(":") == 2 else t + ":00"
        dt = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        out.append(NewsEvent(int(dt.timestamp()), cur.upper(), IMPACT_MAP.get(imp.lower(), imp.lower()),
                             title, "csv"))
    return out


class NewsCalendar:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: dict[tuple[int, str, str], NewsEvent] = {}
        self.last_fetch_ok: float | None = None
        self.last_attempt: float | None = None
        self.last_error: str | None = None
        self.source: str = DEFAULT_FEED_URL

    # ── data ───────────────────────────────────────────────────────────────────────────
    def add(self, events: list[NewsEvent]) -> int:
        with self._lock:
            n = 0
            for e in events:
                k = (e.ts_utc, e.currency, e.title)
                if k not in self._events:
                    n += 1
                self._events[k] = e
            return n

    def events(self, start: int | None = None, end: int | None = None) -> list[NewsEvent]:
        with self._lock:
            evs = [e for e in self._events.values()
                   if (start is None or e.ts_utc >= start) and (end is None or e.ts_utc <= end)]
        return sorted(evs, key=lambda e: (e.ts_utc, e.currency))

    def prune(self, older_than: int) -> None:
        with self._lock:
            for k in [k for k, e in self._events.items() if e.ts_utc < older_than]:
                del self._events[k]

    def fetch(self, url: str = "", timeout: float = 20.0) -> list[NewsEvent]:
        import httpx

        url = url or DEFAULT_FEED_URL
        self.source = url
        self.last_attempt = time.time()
        try:
            r = httpx.get(url, timeout=timeout, headers={"User-Agent": "msc-bot/1.0"}, follow_redirects=True)
            r.raise_for_status()
            events = parse_ff_json(r.json())
            if not events:
                raise ValueError("feed returned no events")
            self.add(events)
            self.last_fetch_ok = time.time()
            self.last_error = None
            log.info("news feed: %d events fetched", len(events))
            return events
        except Exception as exc:  # network, JSON, rate limit …
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.error("news feed fetch failed: %s", self.last_error)
            raise

    # ── rules ──────────────────────────────────────────────────────────────────────────
    def feed_ok(self, now: float, poll_minutes: int) -> bool:
        if self.last_fetch_ok is None:
            return False
        return now - self.last_fetch_ok <= max(2 * poll_minutes * 60 + 300, 3 * 3600)

    def blackout_event(self, symbol: str, now: float, cfg: NewsConfig) -> NewsEvent | None:
        fx = split_fx(symbol)
        if not fx:
            return None
        before, after = cfg.block_before_min * 60, cfg.block_after_min * 60
        levels = set(cfg.impact_levels)
        for e in self.events(int(now - after), int(now + before)):
            if e.currency in fx and e.impact in levels and e.ts_utc - before <= now <= e.ts_utc + after:
                return e
        return None

    def check(self, symbol: str, now: float, cfg: NewsConfig) -> tuple[bool, str]:
        """(blocked, reason) for a NEW entry on ``symbol`` at ``now``."""
        if not cfg.enabled:
            return False, "news filter disabled"
        ev = self.blackout_event(symbol, now, cfg)
        if ev:
            return True, f"{ev.impact} {ev.currency} '{ev.title}' at {ev.iso()}"
        if not self.feed_ok(now, cfg.poll_minutes):
            if cfg.feed_failure_mode == "block_all":
                return True, "news feed down (feed_failure_mode=block_all)"
            return False, "news feed down (feed_failure_mode=allow)"
        return False, "no event in window"

    def blackouts(self, symbols: list[str], now: float, cfg: NewsConfig) -> list[dict]:
        out = []
        if not cfg.enabled:
            return out
        after = cfg.block_after_min * 60
        for sym in symbols:
            ev = self.blackout_event(sym, now, cfg)
            if ev:
                until = ev.ts_utc + after
                out.append({"symbol": sym, "currency": ev.currency, "title": ev.title,
                            "event_time_utc": ev.iso(),
                            "blocked_until_utc": datetime.fromtimestamp(until, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "seconds_remaining": max(0, int(until - now))})
        return out

    def warning(self, now: float, cfg: NewsConfig) -> str | None:
        if not cfg.enabled or self.feed_ok(now, cfg.poll_minutes):
            return None
        mode = cfg.feed_failure_mode
        detail = f" ({self.last_error})" if self.last_error else ""
        if mode == "block_all":
            return f"News feed DOWN — all new entries blocked (feed_failure_mode=block_all){detail}"
        return f"News feed DOWN — trading without news blackout (feed_failure_mode=allow){detail}"
