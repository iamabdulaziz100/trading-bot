import { Fragment, useState, type ReactNode } from 'react'
import { api, type Query } from '../api'
import { Badge, Card, Empty, ErrorBox, Json, Pagination, PassFail, Spinner, Tabs } from '../components/ui'
import { fmtMoney, fmtNum, fmtPrice, fmtTime, pnlClass } from '../format'
import { useAsync } from '../hooks'
import { useStatus } from '../status'
import type { RiskEvent, Signal, Trade } from '../types'
import { useWsChannel, useWsReconnect } from '../ws'

type Tab = 'signals' | 'trades' | 'risk'

const GATES = ['SIZING', 'DAILY_LOSS', 'NEWS', 'CONCURRENCY', 'SYMBOL_DUP', 'RR_CAP', 'RR_SLIPPAGE', 'ENABLED', 'MAX_TRADES_DAY', 'EXECUTION']

function useSymbols(): string[] {
  const s = useAsync(() => api.symbols(), [])
  return s.data?.symbols ?? []
}

interface BaseFilters {
  from: string
  to: string
  symbol: string
}

function FilterBar({ f, set, symbols, children }: {
  f: BaseFilters
  set: (patch: Partial<BaseFilters>) => void
  symbols: string[]
  children?: ReactNode
}) {
  return (
    <div className="toolbar filters">
      <label>
        From <input type="date" value={f.from} onChange={(e) => set({ from: e.target.value })} />
      </label>
      <label>
        To <input type="date" value={f.to} onChange={(e) => set({ to: e.target.value })} />
      </label>
      <label>
        Symbol{' '}
        <select value={f.symbol} onChange={(e) => set({ symbol: e.target.value })}>
          <option value="">all</option>
          {symbols.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </label>
      {children}
    </div>
  )
}

// ── Signals ────────────────────────────────────────────────────────────────
export function SignalsTable({ items, symbolDigits }: { items: Signal[]; symbolDigits?: number | null }) {
  const [open, setOpen] = useState<number | null>(null)
  if (!items.length) return <Empty>No evaluations.</Empty>
  return (
    <div className="table-wrap">
      <table className="table compact">
        <thead>
          <tr>
            <th>Time</th>
            <th>Symbol</th>
            <th>TF</th>
            <th>Dir</th>
            <th>Pillars</th>
            <th>EMA</th>
            <th className="num">#</th>
            <th>Candle</th>
            <th>Pattern</th>
            <th>Decision</th>
            <th>Reason</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((s) => {
            const d = s.details ?? {}
            const title = (k: string) => (typeof d[k] === 'string' ? (d[k] as string) : undefined)
            return (
              <Fragment key={s.id}>
                <tr>
                  <td className="nowrap">{fmtTime(s.ts)}</td>
                  <td>{s.symbol}</td>
                  <td>{s.timeframe}</td>
                  <td>{s.direction ? <Badge tone={s.direction === 'BUY' ? 'ok' : 'bad'}>{s.direction}</Badge> : '—'}</td>
                  <td className="nowrap">
                    <PassFail ok={s.pillar1} label="P1" title={title('pillar1') ?? 'Trend alignment'} />
                    <PassFail ok={s.pillar2} label="P2" title={title('pillar2') ?? 'Valid AOI'} />
                    <PassFail ok={s.pillar3} label="P3" title={title('pillar3') ?? 'Structural pattern'} />
                    <PassFail ok={s.pillar4} label="P4" title={title('pillar4') ?? 'Candle trigger'} />
                  </td>
                  <td>
                    <PassFail ok={s.ema_ok} label="EMA" />
                  </td>
                  <td className="num">{s.pillar_count}</td>
                  <td>{s.candle_signal ?? '—'}</td>
                  <td>{s.pattern ?? '—'}</td>
                  <td>
                    <Badge tone={s.decision === 'EXECUTED' ? 'ok' : s.decision === 'REJECTED' ? 'warn' : 'info'}>{s.decision}</Badge>
                  </td>
                  <td className="small">{s.reason ?? ''}</td>
                  <td>
                    <button className="btn btn-small" onClick={() => setOpen(open === s.id ? null : s.id)}>
                      {open === s.id ? 'Hide' : 'Details'}
                    </button>
                  </td>
                </tr>
                {open === s.id && (
                  <tr className="detail-row">
                    <td colSpan={12}>
                      <div className="detail-grid">
                        {(['pillar1', 'pillar2', 'pillar3', 'pillar4'] as const).map((k, i) => (
                          <div key={k}>
                            <PassFail ok={s[k]} label={`P${i + 1}`} /> <span className="small">{title(k) ?? '—'}</span>
                          </div>
                        ))}
                        {'entry' in d && (
                          <div className="small mono">
                            entry {fmtPrice(d.entry as number, s.symbol, symbolDigits)} · SL {fmtPrice(d.sl as number, s.symbol, symbolDigits)} · TP{' '}
                            {fmtPrice(d.tp as number, s.symbol, symbolDigits)} · RR {fmtNum(d.rr as number, 2)}
                          </div>
                        )}
                        {s.signal_id && <div className="small mono">signal_id {s.signal_id}</div>}
                      </div>
                      <Json value={s.details} />
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function SignalsTab({ symbols }: { symbols: string[] }) {
  const [f, setF] = useState({ from: '', to: '', symbol: '', decision: '', is_backtest: '0' })
  const [page, setPage] = useState(1)
  const pageSize = 50
  const q: Query = { ...f, page, page_size: pageSize }
  const data = useAsync(() => api.signals(q), [JSON.stringify(q)])
  useWsChannel('signal', () => {
    if (page === 1) void data.reload()
  })
  useWsReconnect(() => void data.reload())
  const set = (patch: Partial<typeof f>) => {
    setF({ ...f, ...patch })
    setPage(1)
  }
  return (
    <>
      <FilterBar f={f} set={set} symbols={symbols}>
        <label>
          Decision{' '}
          <select value={f.decision} onChange={(e) => set({ decision: e.target.value })}>
            <option value="">all</option>
            <option>EXECUTED</option>
            <option>REJECTED</option>
          </select>
        </label>
        <label>
          Source{' '}
          <select value={f.is_backtest} onChange={(e) => set({ is_backtest: e.target.value })}>
            <option value="0">live</option>
            <option value="1">backtest</option>
          </select>
        </label>
        {data.loading && <Spinner />}
      </FilterBar>
      <ErrorBox error={data.error} onRetry={() => void data.reload()} />
      <SignalsTable items={data.data?.items ?? []} />
      {data.data && <Pagination page={page} pageSize={pageSize} total={data.data.total} onPage={setPage} />}
    </>
  )
}

// ── Trades ─────────────────────────────────────────────────────────────────
export function TradesTable({ items, currency }: { items: Trade[]; currency?: string | null }) {
  if (!items.length) return <Empty>No trades.</Empty>
  return (
    <div className="table-wrap">
      <table className="table compact">
        <thead>
          <tr>
            <th>Entry time</th>
            <th>Symbol</th>
            <th>Dir</th>
            <th>Status</th>
            <th className="num">Lots</th>
            <th className="num">Entry</th>
            <th className="num">SL</th>
            <th className="num">TP</th>
            <th>Exit time</th>
            <th className="num">Exit</th>
            <th>Reason</th>
            <th className="num">P/L</th>
            <th className="num">R</th>
            <th className="num">Pillars</th>
            <th>Pattern</th>
            <th>Candle</th>
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <tr key={`${t.id}-${t.signal_id}`}>
              <td className="nowrap">{fmtTime(t.entry_time)}</td>
              <td>{t.symbol}</td>
              <td>
                <Badge tone={t.direction === 'BUY' ? 'ok' : 'bad'}>{t.direction}</Badge>
              </td>
              <td>
                <Badge tone={t.status === 'OPEN' ? 'info' : t.status === 'CLOSED' ? 'muted' : 'warn'}>{t.status}</Badge>
              </td>
              <td className="num">{fmtNum(t.lots, 2)}</td>
              <td className="num">{fmtPrice(t.entry_price, t.symbol)}</td>
              <td className="num">{fmtPrice(t.sl, t.symbol)}</td>
              <td className="num">{fmtPrice(t.tp, t.symbol)}</td>
              <td className="nowrap">{fmtTime(t.exit_time)}</td>
              <td className="num">{fmtPrice(t.exit_price, t.symbol)}</td>
              <td>{t.exit_reason ?? '—'}</td>
              <td className={`num ${pnlClass(t.pnl_money)}`}>{fmtMoney(t.pnl_money, currency, true)}</td>
              <td className={`num ${pnlClass(t.r_multiple)}`}>{fmtNum(t.r_multiple, 2, true)}</td>
              <td className="num">{t.pillars ?? '—'}</td>
              <td>{t.pattern_type ?? '—'}</td>
              <td>{t.candle_signal ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TradesTab({ symbols }: { symbols: string[] }) {
  const { status } = useStatus()
  const [f, setF] = useState({ from: '', to: '', symbol: '', result: '', is_backtest: '0' })
  const [page, setPage] = useState(1)
  const pageSize = 50
  const q: Query = { ...f, page, page_size: pageSize }
  const data = useAsync(() => api.trades(q), [JSON.stringify(q)])
  useWsChannel('trade', () => void data.reload())
  useWsReconnect(() => void data.reload())
  const set = (patch: Partial<typeof f>) => {
    setF({ ...f, ...patch })
    setPage(1)
  }
  return (
    <>
      <FilterBar f={f} set={set} symbols={symbols}>
        <label>
          Result{' '}
          <select value={f.result} onChange={(e) => set({ result: e.target.value })}>
            <option value="">all</option>
            <option value="win">win</option>
            <option value="loss">loss</option>
            <option value="open">open</option>
          </select>
        </label>
        <label>
          Source{' '}
          <select value={f.is_backtest} onChange={(e) => set({ is_backtest: e.target.value })}>
            <option value="0">live</option>
            <option value="1">backtest</option>
          </select>
        </label>
        <a className="btn btn-small" href={api.tradesCsvUrl(f)} download>
          Export CSV
        </a>
        {data.loading && <Spinner />}
      </FilterBar>
      <ErrorBox error={data.error} onRetry={() => void data.reload()} />
      <TradesTable items={data.data?.items ?? []} currency={status?.account?.currency} />
      {data.data && <Pagination page={page} pageSize={pageSize} total={data.data.total} onPage={setPage} />}
    </>
  )
}

// ── Risk events ────────────────────────────────────────────────────────────
function RiskTab({ symbols }: { symbols: string[] }) {
  const [f, setF] = useState({ from: '', to: '', symbol: '', gate: '' })
  const [page, setPage] = useState(1)
  const pageSize = 100
  const q: Query = { ...f, page, page_size: pageSize }
  const data = useAsync(() => api.riskEvents(q), [JSON.stringify(q)])
  useWsChannel('signal', () => {
    if (page === 1) void data.reload()
  })
  const set = (patch: Partial<typeof f>) => {
    setF({ ...f, ...patch })
    setPage(1)
  }
  const items: RiskEvent[] = data.data?.items ?? []
  return (
    <>
      <FilterBar f={f} set={set} symbols={symbols}>
        <label>
          Gate{' '}
          <select value={f.gate} onChange={(e) => set({ gate: e.target.value })}>
            <option value="">all</option>
            {GATES.map((g) => (
              <option key={g}>{g}</option>
            ))}
          </select>
        </label>
        {data.loading && <Spinner />}
      </FilterBar>
      <ErrorBox error={data.error} onRetry={() => void data.reload()} />
      {items.length ? (
        <div className="table-wrap">
          <table className="table compact">
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Signal</th>
                <th>Gate</th>
                <th>Result</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id}>
                  <td className="nowrap">{fmtTime(r.ts)}</td>
                  <td>{r.symbol ?? '—'}</td>
                  <td className="mono small">{r.signal_id ?? '—'}</td>
                  <td>{r.gate}</td>
                  <td>
                    <Badge tone={r.result === 'PASS' ? 'ok' : 'bad'}>{r.result}</Badge>
                  </td>
                  <td className="small mono wrap">
                    {typeof r.details === 'string' ? r.details : r.details ? JSON.stringify(r.details) : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No risk events.</Empty>
      )}
      {data.data && <Pagination page={page} pageSize={pageSize} total={data.data.total} onPage={setPage} />}
    </>
  )
}

export default function JournalPage() {
  const [tab, setTab] = useState<Tab>('signals')
  const symbols = useSymbols()
  return (
    <div className="page">
      <Card
        title="Journal"
        actions={
          <Tabs<Tab>
            value={tab}
            onChange={setTab}
            tabs={[
              { key: 'signals', label: 'Signals' },
              { key: 'trades', label: 'Trades' },
              { key: 'risk', label: 'Risk events' },
            ]}
          />
        }
      >
        {tab === 'signals' && <SignalsTab symbols={symbols} />}
        {tab === 'trades' && <TradesTab symbols={symbols} />}
        {tab === 'risk' && <RiskTab symbols={symbols} />}
      </Card>
    </div>
  )
}
