"""Logging: rotating file (``logs/bot.log``), in-memory ring buffer for ``GET /api/logs``,
WebSocket push hook (INFO+) and WARNING+ mirrored into the ``journal_events`` table."""
from __future__ import annotations

import logging
import logging.handlers
import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def _rec_dict(record: logging.LogRecord, formatter: logging.Formatter | None = None) -> dict[str, Any]:
    msg = record.getMessage()
    if record.exc_info and formatter is not None:
        msg = msg + "\n" + formatter.formatException(record.exc_info)
    ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{int(record.msecs):03d}Z"
    return {"ts": ts, "level": record.levelname, "module": record.name, "message": msg}


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 5000):
        super().__init__()
        self.buffer: deque[dict[str, Any]] = deque(maxlen=capacity)
        self.subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._fmt = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            d = _rec_dict(record, self._fmt)
            self.buffer.append(d)
            if record.levelno >= logging.INFO:
                for fn in list(self.subscribers):
                    try:
                        fn(d)
                    except Exception:
                        pass
        except Exception:  # never let logging break the bot
            self.handleError(record)

    def query(self, level: str = "DEBUG", q: str = "", tail: int = 500) -> list[dict[str, Any]]:
        lv = LEVELS.get(level.upper(), 10)
        ql = q.lower()
        rows = [r for r in list(self.buffer)
                if LEVELS.get(r["level"], 0) >= lv and (not ql or ql in r["message"].lower() or ql in r["module"].lower())]
        return rows[-tail:] if tail else rows


class JournalDBHandler(logging.Handler):
    """Mirror WARNING+ records into journal_events."""

    def __init__(self, journal: Any):
        super().__init__(level=logging.WARNING)
        self.journal = journal
        self._local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._local, "busy", False):
            return
        self._local.busy = True
        try:
            d = _rec_dict(record)
            self.journal.insert_journal_event(d["ts"], d["level"], d["module"], d["message"])
        except Exception:
            pass
        finally:
            self._local.busy = False


_ring: RingBufferHandler | None = None


def ring() -> RingBufferHandler:
    global _ring
    if _ring is None:
        _ring = RingBufferHandler()
    return _ring


def setup_logging(log_dir: Path, level: str = "INFO", journal: Any = None) -> RingBufferHandler:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_msc", False):
            root.removeHandler(h)
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)-12s %(message)s")
    fmt.converter = __import__("time").gmtime  # UTC timestamps in the file

    fh = logging.handlers.RotatingFileHandler(log_dir / "bot.log", maxBytes=5_000_000, backupCount=5,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(LEVELS.get(level.upper(), 20))
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(LEVELS.get(level.upper(), 20))
    rb = ring()
    rb.setLevel(logging.DEBUG)
    handlers: list[logging.Handler] = [fh, ch, rb]
    if journal is not None:
        handlers.append(JournalDBHandler(journal))
    for h in handlers:
        h._msc = True  # type: ignore[attr-defined]
        root.addHandler(h)
    for noisy in ("uvicorn.access", "httpx", "httpcore", "asyncio", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return rb
