import { useMemo, useState } from 'react'
import { api, errorMessage } from '../api'
import { SeriesChart, type Point } from '../components/SeriesChart'
import { Badge, Card, Empty, ErrorBox, ProgressBar, Stat } from '../components/ui'
import {
  daysAgoUtc,
  fmtDuration,
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtPrice,
  fmtTime,
  pnlClass,
  todayUtc,
} from '../format'
import { useAsync, useInterval, useNow } from '../hooks'
import { useStatus } from '../status'
import type { Position, Status, Trade, Trend } from '../types'
import { useWsChannel, useWsReconnect } from '../ws'

function trendArrow(t: Trend | undefined): { txt: string; tone: 'ok' | 'bad' | 'muted' } {
  if (t === 1) return { txt: '▲', tone: 'ok' }
  if (t === -1) return { txt: '▼', tone: 'bad' }
  return { txt: '•', tone: 'muted' }
}

function BotControls({ status }: { status: Status | null }) {
  const { refresh } = useStatus()
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [panicStage, setPanicStage] = useState<0 | 1 | 2>(0)
  const enabled = !!status?.bot?.enabled

  const toggle = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const r = enabled ? await api.disable() : await api.enable()
      setMsg(r.enabled ? 'Bot enabled' : 'Bot disabled')
      await refresh()
    } catch (e) {
      setMsg(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const panic = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const r = await api.panic()
      setMsg(
        `Panic: closed ${r.closed.length} position(s)` +
          (r.failed?.length ? `, FAILED ${r.failed.length}: ${JSON.stringify(r.failed)}` : '') +
          `. Bot ${r.enabled ? 'enabled' : 'disabled'}.`,
      )
      await refresh()
    } catch (e) {
      setMsg(errorMessage(e))
    } finally {
      setBusy(false)
      setPanicStage(0)
    }
  }

  return (
    <Card title="Controls">
      <div className="controls">
        <button className={`btn btn-xl ${enabled ? 'btn-warn' : 'btn-ok'}`} disabled={busy || !status} onClick={toggle}>
          {enabled ? 'Disable bot' : 'Enable bot'}
        </button>
        {panicStage === 0 && (
          <button className="btn btn-xl btn-bad" disabled={busy || !status} onClick={() => setPanicStage(1)}>
            Panic close all
          </button>
        )}
      </div>
      {panicStage > 0 && (
        <div className="panic-box">
          {panicStage === 1 ? (
            <>
              <p>
                <strong>Close ALL bot positions at market and disable the bot?</strong> Manual (non-bot) positions are not
                touched.
              </p>
              <div className="controls">
                <button className="btn btn-bad" onClick={() => setPanicStage(2)}>
                  Yes, continue (1/2)
                </button>
                <button className="btn" onClick={() => setPanicStage(0)}>
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <>
              <p>
                <strong>FINAL CONFIRMATION.</strong> This sends market close orders for every bot position right now.
              </p>
              <div className="controls">
                <button className="btn btn-bad btn-xl" disabled={busy} onClick={panic}>
                  {busy ? 'Closing…' : 'CLOSE ALL NOW (2/2)'}
                </button>
                <button className="btn" onClick={() => setPanicStage(0)}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      )}
      {msg && <p className="muted small">{msg}</p>}
    </Card>
  )
}

function PositionsTable({ positions, currency }: { positions: Position[]; currency?: string | null }) {
  if (!positions.length) return <Empty>No open bot positions.</Empty>
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Dir</th>
            <th className="num">Entry</th>
            <th className="num">SL</th>
            <th className="num">TP</th>
            <th className="num">Lots</th>
            <th className="num">Floating P/L</th>
            <th className="num">R</th>
            <th className="num">Risk</th>
            <th className="num">Pillars</th>
            <th>Opened</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.ticket}>
              <td>{p.symbol}</td>
              <td>
                <Badge tone={p.direction === 'BUY' ? 'ok' : 'bad'}>{p.direction}</Badge>
              </td>
              <td className="num">{fmtPrice(p.entry_price, p.symbol)}</td>
              <td className="num">{fmtPrice(p.sl, p.symbol)}</td>
              <td className="num">{fmtPrice(p.tp, p.symbol)}</td>
              <td className="num">{fmtNum(p.lots, 2)}</td>
              <td className={`num ${pnlClass(p.profit)}`}>{fmtMoney(p.profit, currency, true)}</td>
              <td className={`num ${pnlClass(p.floating_r)}`}>{fmtNum(p.floating_r, 2, true)}</td>
              <td className="num">{fmtMoney(p.risk_money, currency)}</td>
              <td className="num">{p.pillars ?? '—'}</td>
              <td>{fmtTime(p.open_time_utc)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function DashboardPage() {
  const { status, receivedAt } = useStatus()
  const now = useNow(1000)
  const currency = status?.account?.currency ?? 'USD'

  // ── positions: REST + WS push ─────────────────────────────────────────────
  const positions = useAsync(() => api.positions(), [])
  useWsChannel('positions', (p) => positions.setData(p))
  useInterval(() => void positions.reload(), 10000)

  // ── trades of the last 30 days (sparkline + today's closed trades) ────────
  const trades = useAsync(
    () => api.trades({ from: daysAgoUtc(31), is_backtest: 0, page: 1, page_size: 500 }),
    [],
  )
  useWsChannel('trade', () => void trades.reload())
  useWsReconnect(() => {
    void positions.reload()
    void trades.reload()
  })
  useInterval(() => void trades.reload(), 60000)

  const today = status?.daily?.day ?? todayUtc()
  const closed = useMemo(
    () =>
      (trades.data?.items ?? [])
        .filter((t) => t.status === 'CLOSED' && t.exit_time)
        .sort((a, b) => (a.exit_time ?? '').localeCompare(b.exit_time ?? '')),
    [trades.data],
  )
  const closedToday: Trade[] = useMemo(
    () => closed.filter((t) => (t.exit_time ?? '').slice(0, 10) === today).reverse(),
    [closed, today],
  )
  const sparkline: Point[] = useMemo(() => {
    if (!closed.length) return []
    const total = closed.reduce((s, t) => s + (t.pnl_money ?? 0), 0)
    const balance = status?.account?.balance
    let eq = balance !== undefined && balance !== null ? balance - total : 0
    const pts: Point[] = [{ time: Math.floor(Date.parse(closed[0].exit_time!) / 1000) - 1, value: eq }]
    for (const t of closed) {
      eq += t.pnl_money ?? 0
      pts.push({ time: Math.floor(Date.parse(t.exit_time!) / 1000), value: eq })
    }
    return pts
  }, [closed, status?.account?.balance])

  // server-clock skew so countdowns follow the bot's clock, not the browser's
  const skew = status ? Date.parse(status.server_time_utc) - receivedAt : 0
  const serverNow = now + (Number.isFinite(skew) ? skew : 0)

  const mt5 = status?.mt5
  const bot = status?.bot
  const daily = status?.daily
  const pos = status?.positions
  const blackouts = status?.news?.blackouts ?? []

  return (
    <div className="page">
      <div className="grid-status">
        <div className={`status-tile ${mt5?.connected ? 'tile-ok' : 'tile-bad'}`}>
          <div className="tile-label">MT5</div>
          <div className="tile-value">{status ? (mt5?.connected ? 'CONNECTED' : 'DISCONNECTED') : '…'}</div>
          <div className="tile-sub">
            {mt5?.server ?? '—'} {mt5?.login ? `· #${mt5.login}` : ''}
            {mt5?.connected && mt5.trade_allowed === false ? ' · algo trading NOT allowed' : ''}
          </div>
        </div>
        <div className={`status-tile ${bot?.halted ? 'tile-bad' : bot?.enabled ? 'tile-ok' : 'tile-warn'}`}>
          <div className="tile-label">Bot</div>
          <div className="tile-value">{status ? (bot?.halted ? 'HALTED' : bot?.enabled ? 'ENABLED' : 'DISABLED') : '…'}</div>
          <div className="tile-sub">
            {bot?.halted ? bot.halt_reason : bot?.warmed_up === false ? 'warming up…' : `last cycle ${fmtTime(bot?.last_cycle_utc)}`}
          </div>
        </div>
        <div className={`status-tile ${daily?.cutoff_hit ? 'tile-bad' : (daily?.used_fraction ?? 0) >= 0.66 ? 'tile-warn' : 'tile-ok'}`}>
          <div className="tile-label">Today P/L vs daily loss limit</div>
          <div className={`tile-value ${pnlClass(daily?.pnl)}`}>
            {fmtMoney(daily?.pnl, currency, true)} <span className="tile-small">({fmtPct(daily?.pnl_pct, 2, true)})</span>
          </div>
          <ProgressBar fraction={daily?.used_fraction} />
          <div className="tile-sub">
            limit −{fmtPct(daily?.limit_pct, 1)} = {fmtMoney(daily?.limit_money, currency)}
            {daily?.cutoff_hit ? ' · CUTOFF HIT' : ''} · trades today {daily?.trades_today ?? '—'}
          </div>
        </div>
        <div className={`status-tile ${pos && pos.open >= pos.max ? 'tile-warn' : 'tile-ok'}`}>
          <div className="tile-label">Open positions</div>
          <div className="tile-value">
            {pos ? `${pos.open} / ${pos.max}` : '—'}
          </div>
          <ProgressBar fraction={pos && pos.max ? pos.open / pos.max : 0} tone="info" />
        </div>
        <div className={`status-tile ${blackouts.length ? 'tile-warn' : 'tile-ok'}`}>
          <div className="tile-label">News blackouts</div>
          <div className="tile-value">{blackouts.length ? `${blackouts.length} active` : 'none'}</div>
          <div className="tile-sub">
            {status?.news?.enabled === false ? 'news filter disabled' : status?.news?.feed_ok ? 'feed ok' : 'feed DOWN'}
          </div>
        </div>
      </div>

      <div className="grid-2">
        <Card title="Account">
          {status?.account ? (
            <div className="stats-row">
              <Stat label="Balance" value={fmtMoney(status.account.balance, currency)} />
              <Stat label="Equity" value={fmtMoney(status.account.equity, currency)} />
              <Stat label="Margin" value={fmtMoney(status.account.margin, currency)} />
              <Stat label="Free margin" value={fmtMoney(status.account.free_margin, currency)} />
              <Stat label="Day start" value={fmtMoney(daily?.start_balance, currency)} />
            </div>
          ) : (
            <Empty>Account unavailable (MT5 disconnected).</Empty>
          )}
        </Card>
        <BotControls status={status} />
      </div>

      {blackouts.length > 0 && (
        <Card title="Active news blackouts">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Currency</th>
                  <th>Event</th>
                  <th>Event time</th>
                  <th>Blocked until</th>
                  <th className="num">Countdown</th>
                </tr>
              </thead>
              <tbody>
                {blackouts.map((b, i) => {
                  const until = Date.parse(b.blocked_until_utc)
                  const remaining = Number.isFinite(until)
                    ? (until - serverNow) / 1000
                    : b.seconds_remaining - (now - receivedAt) / 1000
                  return (
                    <tr key={`${b.symbol}-${b.title}-${i}`}>
                      <td>{b.symbol}</td>
                      <td>{b.currency}</td>
                      <td>{b.title}</td>
                      <td>{fmtTime(b.event_time_utc)}</td>
                      <td>{fmtTime(b.blocked_until_utc)}</td>
                      <td className="num mono big-countdown">{fmtDuration(remaining)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card title="Symbols — trend (1W / 1D / 4H) &amp; alignment">
        {status?.symbols?.length ? (
          <div className="symbol-chips">
            {status.symbols.map((s) => (
              <div key={s.symbol} className={`symbol-chip align-${s.alignment}`} title={s.reason ?? undefined}>
                <div className="symbol-chip-head">
                  <strong>{s.symbol}</strong>
                  {s.paused && <Badge tone="warn">PAUSED</Badge>}
                </div>
                <div className="symbol-chip-trend">
                  {Object.entries(s.trend ?? {}).map(([tf, t]) => {
                    const a = trendArrow(t)
                    return (
                      <span key={tf} className={`trend trend-${a.tone}`}>
                        {tf} {a.txt}
                      </span>
                    )
                  })}
                </div>
                <Badge tone={s.alignment === 'LONG' ? 'ok' : s.alignment === 'SHORT' ? 'bad' : 'muted'}>
                  {s.alignment === 'NEUTRAL_FILTER' ? 'NEUTRAL' : s.alignment}
                </Badge>
                {s.paused && s.reason && <div className="muted small">{s.reason}</div>}
              </div>
            ))}
          </div>
        ) : (
          <Empty>No symbol state yet.</Empty>
        )}
      </Card>

      <Card title={`Open positions (${positions.data?.positions.length ?? 0})`}>
        <ErrorBox error={positions.error} onRetry={() => void positions.reload()} />
        <PositionsTable positions={positions.data?.positions ?? []} currency={currency} />
      </Card>

      <div className="grid-2">
        <Card title={`Closed today (${closedToday.length})`}>
          <ErrorBox error={trades.error} onRetry={() => void trades.reload()} />
          {closedToday.length ? (
            <div className="table-wrap">
              <table className="table compact">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Dir</th>
                    <th>Exit</th>
                    <th>Reason</th>
                    <th className="num">P/L</th>
                    <th className="num">R</th>
                  </tr>
                </thead>
                <tbody>
                  {closedToday.map((t) => (
                    <tr key={t.id}>
                      <td>{t.symbol}</td>
                      <td>{t.direction}</td>
                      <td>{fmtTime(t.exit_time).slice(11)}</td>
                      <td>{t.exit_reason ?? '—'}</td>
                      <td className={`num ${pnlClass(t.pnl_money)}`}>{fmtMoney(t.pnl_money, currency, true)}</td>
                      <td className={`num ${pnlClass(t.r_multiple)}`}>{fmtNum(t.r_multiple, 2, true)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>No trades closed today.</Empty>
          )}
        </Card>
        <Card title="Equity — last 30 days (closed trades)">
          {sparkline.length > 1 ? (
            <SeriesChart points={sparkline} height={180} kind="area" color="#22c55e" />
          ) : (
            <Empty>Not enough closed trades for a curve yet.</Empty>
          )}
        </Card>
      </div>
    </div>
  )
}
