import { useEffect, useMemo, useState } from 'react'
import { api, errorMessage } from '../api'
import { SeriesChart } from '../components/SeriesChart'
import { Badge, Card, Collapsible, Empty, ErrorBox, Json, ProgressBar, Spinner, Stat } from '../components/ui'
import { daysAgoUtc, fmtMoney, fmtNum, fmtPct, fmtRatio, fmtTime, pnlClass, todayUtc } from '../format'
import { useAsync, useInterval } from '../hooks'
import type { BacktestDetail, BacktestMetrics, BacktestRequest, BacktestSummary } from '../types'
import { useWsChannel, useWsReconnect } from '../ws'
import { SignalsTable, TradesTable } from './Journal'

const TFS = ['1W', '1D', '4H', '1H', '30M', '15M']
// module-level so SeriesChart doesn't rebuild the chart on every render
const pctFormatter = (v: number) => `${v.toFixed(2)}%`

function numOrNull(s: string): number | null {
  if (s.trim() === '') return null
  const n = Number(s)
  return Number.isFinite(n) ? n : null
}

function statusTone(s: string): 'ok' | 'bad' | 'info' | 'muted' {
  return s === 'done' ? 'ok' : s === 'error' ? 'bad' : s === 'running' ? 'info' : 'muted'
}

function MetricCards({ m }: { m: BacktestMetrics }) {
  const pd = m.pillars_distribution ?? {}
  return (
    <div className="metric-grid">
      <Stat label="Trades" value={m.trades} sub={`${m.wins}W / ${m.losses}L`} />
      <Stat label="Win rate" value={fmtRatio(m.win_rate)} />
      <Stat
        label="Net P/L"
        value={<span className={pnlClass(m.net_pnl)}>{fmtMoney(m.net_pnl, 'USD', true)}</span>}
        sub={fmtPct(m.net_pnl_pct, 2, true)}
      />
      <Stat label="Profit factor" value={fmtNum(m.profit_factor, 2)} />
      <Stat label="Expectancy" value={`${fmtNum(m.expectancy_r, 2, true)} R`} />
      <Stat label="Avg realized R:R" value={fmtNum(m.avg_rr_realized, 2)} />
      <Stat
        label="Max drawdown"
        value={fmtPct(m.max_drawdown_pct, 2)}
        sub={`${fmtMoney(m.max_drawdown_money, 'USD')} · ${fmtNum(m.max_drawdown_duration_days, 1)} d`}
        tone={(m.max_drawdown_pct ?? 0) > 20 ? 'bad' : undefined}
      />
      <Stat label="Sharpe (daily)" value={fmtNum(m.sharpe_daily, 2)} />
      <Stat label="Balance" value={fmtMoney(m.final_balance, 'USD')} sub={`start ${fmtMoney(m.start_balance, 'USD')}`} />
      <Stat
        label="Pillars (taken)"
        value={Object.keys(pd).length ? Object.entries(pd).map(([k, v]) => `${k}p: ${v}`).join(' · ') : '—'}
      />
      <Stat label="Rejected signals" value={m.rejected_signals ?? '—'} />
    </div>
  )
}

function Results({ d }: { d: BacktestDetail }) {
  const equity = useMemo(() => d.equity.map((p) => ({ time: p.time, value: p.equity })), [d.equity])
  const dd = useMemo(() => d.drawdown.map((p) => ({ time: p.time, value: -Math.abs(p.dd_pct) })), [d.drawdown])
  return (
    <>
      {d.metrics && <MetricCards m={d.metrics} />}
      {d.notes?.length > 0 && (
        <div className="notes">
          {d.notes.map((n, i) => (
            <div key={i} className="banner banner-info small">
              {n}
            </div>
          ))}
        </div>
      )}
      <div className="grid-2">
        <Card title="Equity curve">
          {equity.length > 1 ? <SeriesChart points={equity} height={260} color="#3b82f6" /> : <Empty>No closed trades.</Empty>}
        </Card>
        <Card title="Drawdown (%)">
          {dd.length > 1 ? (
            <SeriesChart points={dd} height={260} kind="area" color="#ef4444" valueFormatter={pctFormatter} />
          ) : (
            <Empty>No drawdown data.</Empty>
          )}
        </Card>
      </div>
      <div className="grid-2">
        <Card title="Per symbol">
          {d.per_symbol?.length ? (
            <table className="table compact">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th className="num">Trades</th>
                  <th className="num">Win rate</th>
                  <th className="num">Net P/L</th>
                  <th className="num">Σ R</th>
                </tr>
              </thead>
              <tbody>
                {d.per_symbol.map((r) => (
                  <tr key={r.symbol}>
                    <td>{r.symbol}</td>
                    <td className="num">{r.trades}</td>
                    <td className="num">{fmtRatio(r.win_rate)}</td>
                    <td className={`num ${pnlClass(r.net_pnl)}`}>{fmtMoney(r.net_pnl, 'USD', true)}</td>
                    <td className={`num ${pnlClass(r.sum_r)}`}>{fmtNum(r.sum_r, 2, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>—</Empty>
          )}
        </Card>
        <Card title="Per month">
          {d.per_month?.length ? (
            <div className="table-wrap scroll-y">
              <table className="table compact">
                <thead>
                  <tr>
                    <th>Month</th>
                    <th className="num">Trades</th>
                    <th className="num">Win rate</th>
                    <th className="num">Net P/L</th>
                    <th className="num">Σ R</th>
                  </tr>
                </thead>
                <tbody>
                  {d.per_month.map((r) => (
                    <tr key={r.month}>
                      <td>{r.month}</td>
                      <td className="num">{r.trades}</td>
                      <td className="num">{fmtRatio(r.win_rate)}</td>
                      <td className={`num ${pnlClass(r.net_pnl)}`}>{fmtMoney(r.net_pnl, 'USD', true)}</td>
                      <td className={`num ${pnlClass(r.sum_r)}`}>{fmtNum(r.sum_r, 2, true)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>—</Empty>
          )}
        </Card>
      </div>
      <Card
        title={`Trades (${d.trades?.length ?? 0})`}
        actions={
          <a className="btn btn-small" href={api.backtestCsvUrl(d.id)} download>
            Download CSV
          </a>
        }
      >
        <TradesTable items={d.trades ?? []} currency="USD" />
      </Card>
      <Card title="Rejected checklist evaluations">
        <Collapsible title={`Show ${d.rejected?.length ?? 0} rejected signals`}>
          <SignalsTable items={d.rejected ?? []} />
        </Collapsible>
      </Card>
      <Card title="Reproducibility">
        <p className="small">
          Config hash: <span className="mono">{d.config_hash ?? '—'}</span>
        </p>
        <Collapsible title="Run parameters">
          <Json value={d.params} />
        </Collapsible>
        <Collapsible title="Config snapshot">
          <Json value={d.config_snapshot} />
        </Collapsible>
      </Card>
    </>
  )
}

function BacktestForm({ onStarted }: { onStarted: (id: number) => void }) {
  const symbols = useAsync(() => api.symbols(), [])
  const [selected, setSelected] = useState<string[]>([])
  const [dateFrom, setDateFrom] = useState(daysAgoUtc(730))
  const [dateTo, setDateTo] = useState(todayUtc())
  const [balance, setBalance] = useState('10000')
  const [spread, setSpread] = useState('')
  const [slippage, setSlippage] = useState('')
  const [commission, setCommission] = useState('')
  const [risk, setRisk] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (symbols.data && !selected.length) setSelected(symbols.data.symbols)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols.data])

  const toggle = (s: string) => setSelected((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]))

  const run = async () => {
    setBusy(true)
    setErr(null)
    try {
      const req: BacktestRequest = {
        symbols: selected,
        date_from: dateFrom,
        date_to: dateTo,
        initial_balance: numOrNull(balance),
        spread_pips: numOrNull(spread),
        slippage_pips: numOrNull(slippage),
        commission_per_lot: numOrNull(commission),
        risk_per_trade_pct: numOrNull(risk),
      }
      const r = await api.runBacktest(req)
      onStarted(r.id)
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="New backtest">
      <div className="form-grid">
        <div className="form-row full">
          <span className="form-label">Symbols</span>
          <div className="checks">
            {(symbols.data?.symbols ?? []).map((s) => (
              <label key={s} className="check">
                <input type="checkbox" checked={selected.includes(s)} onChange={() => toggle(s)} /> {s}
              </label>
            ))}
          </div>
        </div>
        <label className="form-row">
          <span className="form-label">From</span>
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </label>
        <label className="form-row">
          <span className="form-label">To</span>
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </label>
        <label className="form-row">
          <span className="form-label">Initial balance</span>
          <input type="number" min={0} step={100} value={balance} onChange={(e) => setBalance(e.target.value)} />
        </label>
        <label className="form-row">
          <span className="form-label">Spread (pips)</span>
          <input type="number" min={0} step={0.1} placeholder="per-symbol default" value={spread} onChange={(e) => setSpread(e.target.value)} />
        </label>
        <label className="form-row">
          <span className="form-label">Slippage (pips)</span>
          <input type="number" min={0} step={0.1} placeholder="config default" value={slippage} onChange={(e) => setSlippage(e.target.value)} />
        </label>
        <label className="form-row">
          <span className="form-label">Commission / lot</span>
          <input type="number" min={0} step={0.5} placeholder="config default" value={commission} onChange={(e) => setCommission(e.target.value)} />
        </label>
        <label className="form-row">
          <span className="form-label">Risk / trade (%)</span>
          <input type="number" min={0} max={5} step={0.1} placeholder="config default" value={risk} onChange={(e) => setRisk(e.target.value)} />
        </label>
      </div>
      <ErrorBox error={err ?? symbols.error} />
      <button className="btn btn-ok" disabled={busy || !selected.length} onClick={run}>
        {busy ? 'Starting…' : 'Run backtest'}
      </button>
    </Card>
  )
}

function HistoryData() {
  const files = useAsync(() => api.backtestData(), [])
  const symbols = useAsync(() => api.symbols(), [])
  const [file, setFile] = useState<File | null>(null)
  const [symbol, setSymbol] = useState('')
  const [tf, setTf] = useState('1H')
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const upload = async () => {
    if (!file || !symbol) return
    setBusy(true)
    setMsg(null)
    try {
      const r = await api.backtestUpload(file, symbol.toUpperCase(), tf)
      setMsg(`Uploaded ${r.rows} rows`)
      await files.reload()
    } catch (e) {
      setMsg(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Historical data (data/history)">
      <ErrorBox error={files.error} onRetry={() => void files.reload()} />
      {files.data?.files?.length ? (
        <div className="table-wrap scroll-y">
          <table className="table compact">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>TF</th>
                <th className="num">Rows</th>
                <th>From</th>
                <th>To</th>
              </tr>
            </thead>
            <tbody>
              {files.data.files.map((f) => (
                <tr key={`${f.symbol}-${f.tf}`}>
                  <td>{f.symbol}</td>
                  <td>{f.tf}</td>
                  <td className="num">{f.rows}</td>
                  <td>{fmtTime(f.from).slice(0, 10)}</td>
                  <td>{fmtTime(f.to).slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No history CSV files yet. Upload below or export from MT5.</Empty>
      )}
      <div className="toolbar upload">
        <input type="file" accept=".csv,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <input list="bt-symbols" placeholder="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
        <datalist id="bt-symbols">
          {(symbols.data?.symbols ?? []).map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <select value={tf} onChange={(e) => setTf(e.target.value)}>
          {TFS.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <button className="btn btn-small" disabled={busy || !file || !symbol} onClick={upload}>
          {busy ? 'Uploading…' : 'Upload CSV'}
        </button>
      </div>
      <p className="muted small">CSV columns: time_utc,open,high,low,close[,volume] (MT5 export format also accepted).</p>
      {msg && <p className="small">{msg}</p>}
    </Card>
  )
}

export default function BacktestsPage() {
  const list = useAsync(() => api.backtests(), [])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const detail = useAsync<BacktestDetail | null>(
    () => (selectedId === null ? Promise.resolve(null) : api.backtest(selectedId)),
    [selectedId],
  )
  const [live, setLive] = useState<Record<number, { status: string; progress: number | null }>>({})

  useWsChannel('backtest', (p) => {
    setLive((cur) => ({ ...cur, [p.id]: { status: p.status, progress: p.progress } }))
    if (p.status === 'done' || p.status === 'error') {
      void list.reload()
      if (p.id === selectedId) void detail.reload()
    }
  })
  useWsReconnect(() => {
    void list.reload()
    if (selectedId !== null) void detail.reload()
  })

  const d = detail.data
  const running = !!d && (d.status === 'queued' || d.status === 'running')
  // polling fallback while the selected run is in progress
  useInterval(
    () => {
      void detail.reload()
      void list.reload()
    },
    running ? 2000 : null,
  )

  const items: BacktestSummary[] = list.data?.items ?? []
  const liveOf = (b: BacktestSummary) => live[b.id] ?? { status: b.status, progress: b.progress }
  const selLive = d ? live[d.id] : undefined
  const progress = selLive && running ? selLive.progress : d?.progress

  return (
    <div className="page">
      <div className="grid-2">
        <BacktestForm
          onStarted={(id) => {
            setSelectedId(id)
            void list.reload()
          }}
        />
        <HistoryData />
      </div>

      <Card title="Runs" actions={list.loading ? <Spinner /> : undefined}>
        <ErrorBox error={list.error} onRetry={() => void list.reload()} />
        {items.length ? (
          <div className="table-wrap scroll-y">
            <table className="table compact clickable">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Started</th>
                  <th>Status</th>
                  <th>Symbols</th>
                  <th>Range</th>
                  <th className="num">Trades</th>
                  <th className="num">Win rate</th>
                  <th className="num">Net P/L</th>
                  <th className="num">Max DD</th>
                </tr>
              </thead>
              <tbody>
                {items.map((b) => {
                  const lv = liveOf(b)
                  const p = (b.params ?? {}) as Record<string, unknown>
                  return (
                    <tr key={b.id} className={b.id === selectedId ? 'selected' : ''} onClick={() => setSelectedId(b.id)}>
                      <td>{b.id}</td>
                      <td className="nowrap">{fmtTime(b.ts)}</td>
                      <td>
                        <Badge tone={statusTone(lv.status)}>
                          {lv.status}
                          {lv.status === 'running' && lv.progress !== null ? ` ${Math.round((lv.progress ?? 0) * 100)}%` : ''}
                        </Badge>
                      </td>
                      <td className="small">{Array.isArray(p.symbols) ? (p.symbols as string[]).join(', ') : '—'}</td>
                      <td className="small nowrap">
                        {String(p.date_from ?? '')} → {String(p.date_to ?? '')}
                      </td>
                      <td className="num">{b.metrics?.trades ?? '—'}</td>
                      <td className="num">{fmtRatio(b.metrics?.win_rate)}</td>
                      <td className={`num ${pnlClass(b.metrics?.net_pnl)}`}>{fmtMoney(b.metrics?.net_pnl, 'USD', true)}</td>
                      <td className="num">{fmtPct(b.metrics?.max_drawdown_pct, 1)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No backtests yet.</Empty>
        )}
      </Card>

      {selectedId !== null && (
        <Card title={`Backtest #${selectedId}`} actions={detail.loading ? <Spinner /> : undefined}>
          <ErrorBox error={detail.error} onRetry={() => void detail.reload()} />
          {d && (
            <>
              <div className="toolbar">
                <Badge tone={statusTone(d.status)}>{d.status}</Badge>
                <span className="muted small">started {fmtTime(d.ts)}</span>
              </div>
              {running && (
                <div className="bt-progress">
                  <ProgressBar fraction={progress ?? 0} tone="info" />
                  <span className="muted small">{Math.round((progress ?? 0) * 100)}%</span>
                </div>
              )}
              {d.status === 'error' && <ErrorBox error={d.error ?? 'Backtest failed'} />}
              {d.status === 'done' && <Results d={d} />}
            </>
          )}
        </Card>
      )}
    </div>
  )
}
