"""Live trading orchestrator (01 §3 data flow, 01 §4 threading model).

One asyncio loop; blocking MT5 calls run in a 2-worker ThreadPoolExecutor behind the global
MT5 lock (multi-call operations additionally hold ``conn.op_lock``). Loops: candle poll (5 s),
watchdog + maintenance (15 s), position sync + safety sweep (30 s), positions/status push (5 s),
news feed (``news.poll_minutes``).

Trading only starts once the bot is connected, warmed up AND has reconciled the broker's open
positions with the journal (crash recovery). The magic number is fixed for the process lifetime.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from app.api.ws import WSHub
from app.config import BotConfig, ConfigManager, config_warnings
from app.core.pipeline import SymbolPipeline, merge_candle_events
from app.db.journal import Journal, JournalError, iso
from app.models import EXECUTED, REJECTED, Evaluation, StepResult
from app.mt5.connector import MT5Connector, MT5Error
from app.mt5.connector import RET_DONE, RET_DONE_PARTIAL, RET_NO_MONEY, RET_PLACED
from app.mt5.execution import FILLED_STATUS, ExecutionEngine
from app.mt5.monitor import PositionMonitor
from app.pip_engine import PipEngineError, is_v1_tradeable
from app.risk.daily import DailyLossTracker, day_key
from app.risk.manager import RiskContext, RiskManager
from app.risk.news import NewsCalendar, NewsEvent
from app.symbols import SymbolSpec
from app.timeframes import TF_SECONDS, WARMUP_BARS, event_sort_key

log = logging.getLogger("bot")
STALE = "stale feed"


def day_start_epoch(now: float, boundary: str, offset: int) -> int:
    shift = offset if boundary == "server" else 0
    return int((now + shift) // 86400 * 86400 - shift)


def _epoch(iso_ts: str) -> int:
    return int(datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


class TradingBot:
    def __init__(self, cm: ConfigManager, journal: Journal, hub: WSHub):
        self.cm = cm
        self.journal = journal
        self.hub = hub
        cfg = cm.config
        self.magic = cfg.mt5.magic_number  # fixed until restart (changing it live would orphan positions)
        self.conn = MT5Connector(cfg.mt5)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mt5")
        self.pipelines: dict[str, SymbolPipeline] = {}
        self.specs: dict[str, SymbolSpec] = {}
        self.risk = RiskManager(cfg)
        self.daily = DailyLossTracker(cfg.risk.max_daily_loss_pct)
        self.news = NewsCalendar()
        self.enabled = journal.kv_get("bot_enabled", "1") == "1"
        self.halted = False
        self.halt_reason: str | None = None
        self.execution = ExecutionEngine(self.conn, journal, lambda: self.cfg, self.magic,
                                         can_trade=lambda: self.enabled and not self.halted)
        self.monitor = PositionMonitor(self.conn, journal, self.magic, self.execution, spec_for=self._spec_sync)
        self.warmed_up = False
        self.reconciled = False
        self.rebuild_required = False
        self.restart_required = False
        self.paused: dict[str, str] = {}  # symbol → reason (spec error / stale feed)
        self.no_money_day: dict[str, str] = {}
        self.positions_view: list[dict[str, Any]] = []
        self.account_view: dict[str, Any] | None = None
        self.started_at = time.time()
        self.last_cycle: float | None = None
        self.last_offset_check = 0.0
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()  # serialises candle processing/orders, rebuilds and panic
        self._running = False
        stored = journal.kv_get("server_offset")
        if stored is not None and self.conn.offset_override is None:
            self.conn.server_offset = int(stored)
        cm.on_change(self._on_config_change)

    @property
    def cfg(self) -> BotConfig:
        return self.cm.config

    async def _blocking(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *args)

    def _spec_sync(self, symbol: str) -> SymbolSpec | None:
        try:
            return self.specs.get(symbol) or self.conn.symbol_spec(symbol)
        except (MT5Error, PipEngineError):
            return None

    # ── lifecycle ──────────────────────────────────────────────────────────────────────
    async def start(self) -> None:
        self._running = True
        self.hub.bind(asyncio.get_running_loop())
        self._load_news_from_db()
        if not self.conn.available:
            log.error(self.conn.message)
        for coro in (self._watchdog_loop(), self._poll_loop(), self._sync_loop(), self._push_loop(),
                     self._news_loop()):
            self._tasks.append(asyncio.create_task(coro))
        log.info("MSC bot started (enabled=%s, symbols=%s, exec TF=%s, magic=%s)", self.enabled, self.cfg.symbols,
                 self.cfg.exec_tf, self.magic)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        loop = asyncio.get_running_loop()
        # let an order already in flight finish before the terminal session is closed
        await loop.run_in_executor(None, lambda: self.pool.shutdown(wait=True))
        await loop.run_in_executor(None, self.conn.shutdown)

    def halt(self, reason: str) -> None:
        if not self.halted:
            log.critical("TRADING HALTED: %s — new entries blocked; open positions keep server-side SL/TP", reason)
        self.halted, self.halt_reason = True, reason

    # ── connection, warm-up, reconcile ─────────────────────────────────────────────────
    async def _ensure_connected(self) -> bool:
        if not self.conn.available:
            return False
        if self.conn.connected and await self._blocking(self.conn.ping):
            return True
        was = self.conn.connected
        delay = 1
        while self._running:
            if await self._blocking(self.conn.connect, 1):
                if was:
                    log.warning("MT5 reconnected")
                await self._maintenance()
                return True
            self.hub.publish("status", self.status())
            log.warning("MT5 DISCONNECTED — retrying in %ss", delay)
            await asyncio.sleep(delay)
            delay = min(60, delay * 2)
        return False

    async def _maintenance(self) -> None:
        """Offset check (hourly), warm-up and startup reconcile — each retried until it succeeds."""
        if time.time() - self.last_offset_check > 3600 or not self.warmed_up:
            prev = self.conn.server_offset
            off = await self._blocking(self.conn.detect_server_offset, self.cfg.symbols)
            self.last_offset_check = time.time()
            if off is not None:
                self.journal.kv_set("server_offset", str(off))
                if off != prev and self.warmed_up:
                    log.warning("broker UTC offset changed %+.2fh → %+.2fh — rebuilding structure state",
                                prev / 3600, off / 3600)
                    await self.rebuild()
        if not self.warmed_up:
            await self.rebuild()
        if not self.reconciled:
            try:
                await self._blocking(self.monitor.reconcile_startup)
                self._restore_daily()
                self.reconciled = True
            except MT5Error as exc:
                log.warning("startup reconcile failed (%s) — retrying", exc)

    async def rebuild(self, symbols: list[str] | None = None) -> list[str]:
        """(Re)create pipelines and warm them up from MT5 history (also used after config changes)."""
        if not self.conn.connected:
            raise MT5Error("MT5 not connected — cannot rebuild structure state")
        targets = symbols or list(self.cfg.symbols)
        async with self._lock:
            done = []
            for sym in targets:
                try:
                    pipe, spec = await self._blocking(self._warmup_symbol, sym)
                    self.pipelines[sym], self.specs[sym] = pipe, spec
                    self.paused.pop(sym, None)
                    done.append(sym)
                except (PipEngineError, MT5Error) as exc:
                    self.paused[sym] = str(exc)
                    log.error("%s paused: %s", sym, exc)
            for sym in list(self.pipelines):
                if sym not in self.cfg.symbols:
                    del self.pipelines[sym]
            if symbols is None:
                self.rebuild_required = False
            self.warmed_up = True
        log.info("structure state rebuilt for %s", done)
        self.hub.publish("status", self.status())
        return done

    def _warmup_symbol(self, sym: str) -> tuple[SymbolPipeline, SymbolSpec]:
        if not is_v1_tradeable(sym):
            raise PipEngineError(f"{sym} is outside v1 scope (forex only)")
        spec = self.conn.symbol_spec(sym)
        pipe = SymbolPipeline(sym, self.cfg, spec.pip_size)
        data = {}
        for tf in pipe.tfs:
            bars = self.conn.rates(sym, tf, WARMUP_BARS[tf] + 1)
            data[tf] = bars[:-1]  # last bar is still forming
            if len(data[tf]) < 50:
                log.warning("%s %s: only %d bars of history available", sym, tf, len(data[tf]))
        t0 = time.time()
        for tf, c in merge_candle_events(data, sym):
            pipe.on_candle_closed(tf, c)  # warm-up: evaluations are discarded
        tr = pipe.trends()
        log.info("%s warmed up in %.1fs: 1W=%+d 1D=%+d 4H=%+d → %s; zones %s", sym, time.time() - t0, tr["1W"],
                 tr["1D"], tr["4H"], pipe.last_alignment,
                 {tf: sum(z.valid for z in zs) for tf, zs in pipe.zones.items()})
        return pipe, spec

    # ── loops ──────────────────────────────────────────────────────────────────────────
    async def _watchdog_loop(self) -> None:
        while self._running:
            try:
                if self.conn.available:
                    was = self.conn.connected
                    if await self._ensure_connected():
                        await self._maintenance()
                    if was != self.conn.connected:
                        self.hub.publish("status", self.status())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("watchdog error")
            await asyncio.sleep(15)

    def _trading_ready(self) -> bool:
        return self.conn.connected and self.warmed_up and self.reconciled

    async def _poll_loop(self) -> None:
        while self._running:
            await asyncio.sleep(5)
            if not self._trading_ready():
                continue
            try:
                async with self._lock:
                    for sym, pipe in list(self.pipelines.items()):
                        if sym not in self.cfg.symbols:
                            continue  # removed from config — ignored until the next rebuild
                        if sym in self.paused and self.paused[sym] != STALE:
                            continue
                        bars, stale = await self._blocking(self._fetch_new_bars, sym, pipe)
                        self._set_stale(sym, stale)
                        for tf, candle in bars:
                            await self._handle_step(pipe.on_candle_closed(tf, candle))
                self.last_cycle = time.time()
            except MT5Error as exc:
                log.warning("poll: %s", exc)
            except JournalError as exc:
                self.halt(f"DB write failure: {exc}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("unhandled exception in trading loop")
                self.halt(f"unhandled exception: {type(exc).__name__}: {exc}")

    def _set_stale(self, sym: str, stale: bool | None) -> None:
        if stale is True and self.paused.get(sym) != STALE:
            log.warning("%s stale feed (last %s bar older than 2×TF while ticks arrive) — paused", sym,
                        self.cfg.exec_tf)
            self.paused[sym] = STALE
        elif stale is False and self.paused.get(sym) == STALE:
            log.info("%s feed fresh again — resumed", sym)
            self.paused.pop(sym, None)

    def _fetch_new_bars(self, sym: str, pipe: SymbolPipeline) -> tuple[list[tuple[str, Any]], bool | None]:
        """Closed candles newer than the last processed one, for every TF, in real-time order, plus
        a stale-feed verdict (None = cannot tell, e.g. market closed). A bar counts as closed once a
        newer bar exists (the last returned bar is still forming)."""
        now = time.time()
        out = []
        stale: bool | None = None
        for tf in pipe.tfs:
            last = pipe.last_time(tf)
            need = 3 if last is None else min(5000, int((now - last) / TF_SECONDS[tf]) + 3)
            bars = self.conn.rates(sym, tf, max(3, need))
            for c in bars[:-1]:
                if last is None or c.time > last:
                    out.append((tf, c))
            if tf == pipe.exec_tf and bars:
                tick = self.conn.tick(sym)
                if now - tick["time_utc"] < 120:  # only meaningful while the market is trading
                    stale = (now - (bars[-1].time + TF_SECONDS[tf])) > 2 * TF_SECONDS[tf]
        out.sort(key=lambda e: event_sort_key(e[1].time, e[0], sym))
        return out, stale

    async def _sync_loop(self) -> None:
        while self._running:
            await asyncio.sleep(30)
            if not (self.conn.connected and self.reconciled):
                continue
            try:
                views, closed = await self._blocking(self.monitor.sync)
                self.positions_view = views
                for tr in closed:
                    self.hub.publish("trade", tr)
                if self.monitor.critical:
                    self.halt("; ".join(self.monitor.critical))
                    self.monitor.critical.clear()
                await self._update_daily()
            except MT5Error as exc:
                log.warning("sync: %s", exc)
            except JournalError as exc:
                self.halt(f"DB write failure: {exc}")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("position sync error")

    async def _push_loop(self) -> None:
        last_positions = None
        while self._running:
            await asyncio.sleep(5)
            try:
                if self.conn.connected:
                    self.account_view = await self._blocking(self.conn.account, 4.0)
                    positions = await self._blocking(self.conn.positions, self.magic)
                    self.positions_view = self.monitor.position_views(positions)
                    snap = json.dumps(self.positions_view, default=str)
                    if snap != last_positions:
                        self.hub.publish("positions", {"positions": self.positions_view})
                        last_positions = snap
                self.hub.publish("status", self.status())
            except MT5Error:
                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("status push error")

    async def _news_loop(self) -> None:
        while self._running:
            if self.cfg.news.enabled:
                await self.refresh_news()
            await asyncio.sleep(self.cfg.news.poll_minutes * 60)

    async def refresh_news(self) -> int:
        try:
            events = await self._blocking(self.news.fetch, self.cfg.news.feed_url)
            self.journal.upsert_news([(e.iso(), e.currency, e.impact, e.title, e.source) for e in events])
            self.news.prune(int(time.time()) - 14 * 86400)
            return len(events)
        except Exception:
            return 0

    def _load_news_from_db(self) -> None:
        rows = self.journal.news_since(iso(time.time() - 2 * 86400))
        self.news.add([NewsEvent(_epoch(r["ts_utc"]), r["currency"], r["impact"], r["title"], r["source"] or "db")
                       for r in rows])

    # ── daily loss tracking ────────────────────────────────────────────────────────────
    def _day(self, now: float) -> str:
        return day_key(now, self.cfg.risk.day_boundary, self.conn.server_offset)

    def _restore_daily(self) -> None:
        raw = self.journal.kv_get("daily_state")
        if raw:
            st = json.loads(raw)
            if st.get("day") == self._day(time.time()):
                self.daily.restore(st["day"], st["start_balance"], st.get("cutoff_hit", False))

    def _daily_update(self, acc: dict[str, Any]) -> bool:
        """Single entry point for the daily-loss tracker (sync loop and signal handling)."""
        prev_day, prev_start, was_hit = self.daily.day, self.daily.start_balance, self.daily.cutoff_hit
        hit = self.daily.update(self._day(time.time()), acc["balance"], acc["equity"])
        if self.daily.new_day_started and prev_day:
            self._finalize_day(prev_day, prev_start, acc["balance"])
        if hit and not was_hit:
            st = self.daily.state()
            log.warning("DAILY LOSS CUTOFF HIT: day P/L %.2f (%.2f%%) ≤ −%.2f%% — new entries halted until the "
                        "next day boundary", st.pnl, st.pnl_pct, st.limit_pct)
        self.journal.kv_set("daily_state", json.dumps({"day": self.daily.day, "start_balance": self.daily.start_balance,
                                                       "cutoff_hit": self.daily.cutoff_hit}))
        st = self.daily.state()
        self.journal.upsert_daily(st.day, start_balance=st.start_balance, end_balance=acc["balance"], pnl=st.pnl,
                                  cutoff_hit=int(st.cutoff_hit))
        return hit

    async def _update_daily(self) -> None:
        acc = await self._blocking(self.conn.account, 0.0)
        if acc:
            self.account_view = acc
            self._daily_update(acc)

    def _finalize_day(self, day: str, start_balance: float, end_balance: float) -> None:
        start = day_start_epoch(time.time() - 86400, self.cfg.risk.day_boundary, self.conn.server_offset)
        trades = self.journal.trades_closed_between(iso(start), iso(start + 86400))
        wins = sum(1 for t in trades if (t.get("pnl_money") or 0) > 0)
        self.journal.upsert_daily(day, start_balance=start_balance, end_balance=end_balance,
                                  pnl=end_balance - start_balance, trades=len(trades), wins=wins)
        log.info("day %s closed: %d trades, %d wins, P/L %.2f", day, len(trades), wins, end_balance - start_balance)

    # ── signal handling ────────────────────────────────────────────────────────────────
    async def _handle_step(self, res: StepResult) -> None:
        for ev in res.structure_events:
            lvl = logging.DEBUG if ev.type in ("LIQUIDITY_SWEEP", "SNAKE_NO_PIVOT") else logging.INFO
            log.log(lvl, "%s %s %s @ %.5f %s", res.symbol, ev.tf, ev.type, ev.price, ev.detail)
        for pe in res.pattern_events:
            log.info("%s %s %s %s @ %.5f — %s", res.symbol, res.tf, pe.type, pe.direction, pe.level, pe.detail)
        if res.skip_reason:
            log.debug("%s %s skip: %s", res.symbol, res.tf, res.skip_reason)
        ev = res.evaluation
        if ev is None:
            return
        decision, reason = REJECTED, ev.reason
        if ev.signal is not None:
            decision, reason = await self._process_signal(ev)
        row = {"ts": iso(ev.signal_time), "symbol": ev.symbol, "timeframe": ev.timeframe, "direction": ev.direction,
               "pillar1": ev.pillars.p1, "pillar2": ev.pillars.p2, "pillar3": ev.pillars.p3, "pillar4": ev.pillars.p4,
               "ema_ok": ev.pillars.ema_ok, "pillar_count": ev.pillar_count, "candle_signal": ev.candle_signal,
               "pattern": ev.pattern, "decision": decision, "reason": reason, "is_backtest": 0,
               "signal_id": ev.signal_id, "details": ev.pillars.details}
        row_id = self.journal.insert_signal(row)
        log.info("checklist %s %s %s pillars=%d/%s → %s%s", ev.symbol, ev.direction, ev.signal_id, ev.pillar_count,
                 self.cfg.confluence.min_pillars, decision, f" ({reason})" if reason else "")
        self.hub.publish("signal", self.journal.get_signal(row_id))

    async def _process_signal(self, ev: Evaluation) -> tuple[str, str | None]:
        sig = ev.signal
        assert sig is not None
        now = time.time()
        cfg = self.cfg
        try:
            spec = await self._blocking(self.conn.symbol_spec, sig.symbol)  # fresh tick value
            self.specs[sig.symbol] = spec
            acc = await self._blocking(self.conn.account, 0.0)
            positions = await self._blocking(self.conn.positions, self.magic)
        except (MT5Error, PipEngineError) as exc:
            self.journal.insert_risk_events(iso(now), sig.symbol, sig.signal_id, [("EXECUTION", "BLOCK", str(exc))])
            return REJECTED, f"EXECUTION: {exc}"
        if not acc:
            return REJECTED, "EXECUTION: account info unavailable"
        cut = self._daily_update(acc)
        st = self.daily.state()
        news_blocked, news_reason = self.news.check(sig.symbol, now, cfg.news)
        day0 = day_start_epoch(now, cfg.risk.day_boundary, self.conn.server_offset)
        ctx = RiskContext(enabled=self.enabled and not self.halted, balance=acc["balance"], equity=acc["equity"],
                          open_symbols=[p["symbol"] for p in positions],
                          trades_today=self.journal.count_trades_since(iso(day0)), daily_cutoff_hit=cut,
                          daily_detail=f"day P/L {st.pnl:.2f} ({st.pnl_pct:.2f}%) vs −{st.limit_pct}%",
                          news_blocked=news_blocked, news_reason=news_reason)
        decision = self.risk.evaluate(sig, ctx, spec)
        if decision.approved and self.no_money_day.get(sig.symbol) == self._day(now):
            decision.approved, decision.reason = False, "MARGIN: insufficient margin earlier today"
        self.journal.insert_risk_events(iso(now), sig.symbol, sig.signal_id,
                                        [(g.gate, g.result, g.details) for g in decision.gates])
        if not decision.approved:
            log.info("signal %s blocked by risk: %s", sig.signal_id, decision.reason)
            return REJECTED, decision.reason
        res = await self._blocking(self.execution.execute, sig, decision, spec, time.time())
        if res.gate_events:
            self.journal.insert_risk_events(iso(), sig.symbol, sig.signal_id, res.gate_events)
        if res.retcode == RET_NO_MONEY:  # block symbol until next day (04 §7)
            self.no_money_day[sig.symbol] = self._day(now)
        trade = self.journal.get_trade(sig.signal_id)
        if trade:
            self.hub.publish("trade", trade)
        if res.status == FILLED_STATUS:
            return EXECUTED, None
        return REJECTED, f"{res.status}: {res.message}"

    # ── controls ───────────────────────────────────────────────────────────────────────
    def set_enabled(self, value: bool) -> None:
        self.enabled = value
        self.journal.kv_set("bot_enabled", "1" if value else "0")
        log.warning("bot %s via UI (new entries only)", "ENABLED" if value else "DISABLED")
        self.hub.publish("status", self.status())

    async def panic(self) -> dict[str, Any]:
        """Disable, wait for any order in flight, then market-close every bot position (bot
        magic only), re-scanning until none remain (max 3 passes)."""
        self.set_enabled(False)
        closed: list[int] = []
        if not self.conn.connected:
            return {"closed": closed, "failed": ["MT5 disconnected"], "enabled": False}
        remaining: list[dict[str, Any]] = []
        async with self._lock:
            for _ in range(3):
                try:
                    remaining = await self._blocking(self.conn.positions, self.magic)
                except MT5Error as exc:
                    log.critical("PANIC: cannot list positions: %s", exc)
                    break
                if not remaining:
                    break
                for p in remaining:
                    try:
                        tr = self.journal.trade_by_ticket(p["ticket"])
                        if tr:
                            self.journal.update_trade(tr["signal_id"], status="CLOSING", exit_reason="PANIC")
                        spec = self.specs.get(p["symbol"]) or await self._blocking(self.conn.symbol_spec, p["symbol"])
                        r = await self._blocking(self.execution.close_position, p, spec, "MSC|panic")
                        if int(r.get("retcode", -1)) in (RET_DONE, RET_DONE_PARTIAL, RET_PLACED):
                            closed.append(p["ticket"])
                    except Exception as exc:  # keep closing the others
                        log.critical("PANIC: closing %s failed: %s", p["ticket"], exc)
            try:
                remaining = await self._blocking(self.conn.positions, self.magic)
            except MT5Error:
                pass
        failed = [p["ticket"] for p in remaining]
        log.critical("PANIC CLOSE ALL: closed=%s still_open=%s — bot disabled", closed, failed)
        return {"closed": closed, "failed": failed, "enabled": False}

    def _on_config_change(self, old: BotConfig, new: BotConfig) -> None:
        self.risk.cfg = new
        self.daily.max_daily_loss_pct = new.risk.max_daily_loss_pct
        for p in self.pipelines.values():
            p.cfg = new  # hot params; structural params need a rebuild
        o, n = old.model_dump(), new.model_dump()
        if any(o[k] != n[k] for k in ("symbols", "timeframes", "structure", "aoi", "patterns", "candles")) or \
                o["confluence"]["ema_period"] != n["confluence"]["ema_period"]:
            self.rebuild_required = True
        if o["mt5"] != n["mt5"] or o["server"] != n["server"]:
            self.restart_required = True  # magic number / credentials only change after a restart
        self.journal.insert_config_history(self.cm.masked_dict(), "ui")
        log.info("config updated (rebuild_required=%s, restart_required=%s)", self.rebuild_required,
                 self.restart_required)

    # ── read models ────────────────────────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        cfg = self.cfg
        now = time.time()
        st = self.daily.state()
        acc = self.account_view
        if acc and st.day is None:
            st.start_balance, st.pnl = acc["balance"], acc["equity"] - acc["balance"]
        paused = dict(self.paused)
        warnings = list(config_warnings(cfg))
        news_warning = self.news.warning(now, cfg.news)
        if news_warning:
            warnings.append(news_warning)
        if self.halted:
            warnings.append(f"TRADING HALTED: {self.halt_reason}")
        if st.cutoff_hit:
            warnings.append("Daily loss cutoff hit — new entries halted until the next day boundary")
        if self.rebuild_required:
            warnings.append("Structure parameters changed — rebuild required")
        if self.restart_required:
            warnings.append("MT5/server settings changed — restart the bot to apply")
        if self.conn.connected and not self.reconciled:
            warnings.append("Reconciling open positions with the journal — trading not started yet")
        for sym, why in paused.items():
            warnings.append(f"{sym} paused: {why}")
        if self.conn.available and self.conn.terminal and not self.conn.terminal.get("trade_allowed", True):
            warnings.append("MT5 'Algo Trading' is disabled in the terminal")
        symbols = []
        for sym in cfg.symbols:
            pipe = self.pipelines.get(sym)
            last = pipe.last_time(pipe.exec_tf) if pipe else None
            symbols.append({
                "symbol": sym, "paused": sym in paused, "reason": paused.get(sym),
                "trend": pipe.trends() if pipe else {"1W": 0, "1D": 0, "4H": 0},
                "alignment": pipe.last_alignment if pipe else "NEUTRAL_FILTER",
                "last_candle_utc": iso(last) if last else None,
            })
        day0 = day_start_epoch(now, cfg.risk.day_boundary, self.conn.server_offset)
        return {
            "server_time_utc": iso(now),
            "mt5": {"connected": self.conn.connected, "available": self.conn.available, "message": self.conn.message,
                    "server": (acc or {}).get("server"), "login": (acc or {}).get("login"),
                    "trade_allowed": bool(self.conn.terminal.get("trade_allowed")) if self.conn.terminal else False,
                    "server_utc_offset_sec": self.conn.server_offset},
            "bot": {"enabled": self.enabled, "halted": self.halted, "halt_reason": self.halt_reason,
                    "warmed_up": self.warmed_up, "started_at": iso(self.started_at),
                    "last_cycle_utc": iso(self.last_cycle) if self.last_cycle else None,
                    "rebuild_required": self.rebuild_required},
            "daily": {"day": st.day or self._day(now), "start_balance": st.start_balance, "pnl": st.pnl,
                      "pnl_pct": st.pnl_pct, "limit_pct": st.limit_pct, "limit_money": st.limit_money,
                      "used_fraction": st.used_fraction, "cutoff_hit": st.cutoff_hit,
                      "trades_today": self.journal.count_trades_since(iso(day0))},
            "positions": {"open": len(self.positions_view), "max": cfg.risk.max_concurrent_trades},
            "account": None if not acc else {k: acc[k] for k in ("balance", "equity", "margin", "free_margin", "currency")},
            "news": {"enabled": cfg.news.enabled, "feed_ok": self.news.feed_ok(now, cfg.news.poll_minutes),
                     "last_fetch_utc": iso(self.news.last_fetch_ok) if self.news.last_fetch_ok else None,
                     "warning": news_warning, "blackouts": self.news.blackouts(cfg.symbols, now, cfg.news)},
            "symbols": symbols,
            "warnings": warnings,
        }

    def overlays(self, symbol: str, tf: str) -> dict[str, Any]:
        pipe = self.pipelines.get(symbol)
        if pipe is None or tf not in pipe.tfs:
            return {"structure": None, "zones": [], "swings": [], "events": [], "ema": [], "positions": [], "trades": []}
        out = pipe.overlays(tf)
        out["positions"] = [{"direction": p["direction"], "entry_price": p["entry_price"], "sl": p["sl"], "tp": p["tp"],
                             "open_time": _epoch(p["open_time_utc"])}
                            for p in self.positions_view if p["symbol"] == symbol]
        trades = []
        for t in self.journal.all_trades({"symbol": symbol, "is_backtest": 0}):
            for kind, tkey, pkey in (("entry", "entry_time", "entry_price"), ("exit", "exit_time", "exit_price")):
                if t.get(tkey) and t.get(pkey):
                    d = {"time": _epoch(t[tkey]), "price": t[pkey], "direction": t["direction"], "kind": kind}
                    if kind == "exit":
                        d["exit_reason"] = t.get("exit_reason")
                    trades.append(d)
        out["trades"] = trades
        return out

    def spec_dict(self, symbol: str) -> dict[str, Any] | None:
        s = self.specs.get(symbol)
        return asdict(s) if s else None
