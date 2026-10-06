import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { api } from '../api'
import { Badge, Card, ErrorBox, Spinner } from '../components/ui'
import { fmtPips, fmtPrice, priceDigits } from '../format'
import { useAsync, useInterval } from '../hooks'
import type { Candle, CandlesResponse, Overlays, Zone } from '../types'
import { useWsChannel, useWsReconnect } from '../ws'

const COLORS = {
  up: '#22c55e',
  down: '#ef4444',
  support: '#22c55e',
  resistance: '#ef4444',
  neutral: '#60a5fa',
  ema: '#f59e0b',
  structure: '#a78bfa',
  entry: '#60a5fa',
  sl: '#ef4444',
  tp: '#22c55e',
  close: '#93c5fd',
}

function withAlpha(hex: string, alpha: number): string {
  const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255)
    .toString(16)
    .padStart(2, '0')
  return `${hex}${a}`
}

/** Snap an arbitrary timestamp to the open time of the bar containing it. */
function snapper(candles: Candle[]) {
  const times = candles.map((c) => c.time)
  return (t: number): number | null => {
    if (!times.length || t < times[0]) return null
    let lo = 0
    let hi = times.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (times[mid] <= t) lo = mid
      else hi = mid - 1
    }
    return times[lo]
  }
}

interface Toggles {
  zones: boolean
  swings: boolean
  events: boolean
  ema: boolean
  structure: boolean
  trades: boolean
}

const LIMITS = [300, 500, 1000, 2000]

export default function ChartsPage() {
  const symbols = useAsync(() => api.symbols(), [])
  const [symbol, setSymbol] = useState<string>('')
  const [tf, setTf] = useState<string>('')
  const [limit, setLimit] = useState(500)
  const [bodiesOnly, setBodiesOnly] = useState(false)
  const [toggles, setToggles] = useState<Toggles>({
    zones: true,
    swings: true,
    events: true,
    ema: true,
    structure: true,
    trades: true,
  })

  useEffect(() => {
    const s = symbols.data
    if (!s) return
    if (!symbol && s.symbols.length) setSymbol(s.symbols[0])
    if (!tf) setTf(s.execution_tf || s.timeframes[s.timeframes.length - 1] || '1H')
  }, [symbols.data, symbol, tf])

  const timeframes = useMemo(() => {
    const s = symbols.data
    if (!s) return []
    const set = new Set([...s.timeframes, s.execution_tf])
    return [...set]
  }, [symbols.data])

  const ready = !!symbol && !!tf
  const candles = useAsync<CandlesResponse | null>(
    () => (ready ? api.candles(symbol, tf, limit) : Promise.resolve(null)),
    [symbol, tf, limit, ready],
  )
  const overlays = useAsync<Overlays | null>(
    () => (ready ? api.overlays(symbol, tf) : Promise.resolve(null)),
    [symbol, tf, ready],
  )
  const refresh = () => {
    if (!ready) return
    void candles.reload()
    void overlays.reload()
  }
  useInterval(refresh, 30000)
  useWsChannel('signal', (s) => {
    if (s.symbol === symbol) refresh()
  })
  useWsChannel('trade', (t) => {
    if (t.symbol === symbol) refresh()
  })
  useWsReconnect(refresh)

  // ── chart plumbing ───────────────────────────────────────────────────────
  const el = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const mainRef = useRef<ISeriesApi<'Candlestick'> | ISeriesApi<'Line'> | null>(null)
  const emaRef = useRef<ISeriesApi<'Line'> | null>(null)
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const linesRef = useRef<IPriceLine[]>([])
  const fittedKey = useRef<string>('')

  useEffect(() => {
    if (!el.current) return
    const chart = createChart(el.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#0b1220' },
        textColor: '#9ca3af',
        attributionLogo: false,
      },
      grid: { vertLines: { color: '#152033' }, horzLines: { color: '#152033' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#334155' },
      timeScale: { borderColor: '#334155', timeVisible: true, secondsVisible: false },
    })
    chartRef.current = chart
    emaRef.current = chart.addSeries(LineSeries, {
      color: COLORS.ema,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      title: 'EMA50',
    })
    return () => {
      chart.remove()
      chartRef.current = null
      mainRef.current = null
      emaRef.current = null
      markersRef.current = null
      linesRef.current = []
    }
  }, [])

  // (re)create the main series when switching candles <-> bodies-only line
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    if (mainRef.current) {
      markersRef.current?.detach()
      chart.removeSeries(mainRef.current)
    }
    linesRef.current = []
    mainRef.current = bodiesOnly
      ? chart.addSeries(LineSeries, { color: COLORS.close, lineWidth: 2, priceLineVisible: false })
      : chart.addSeries(CandlestickSeries, {
          upColor: COLORS.up,
          downColor: COLORS.down,
          borderUpColor: COLORS.up,
          borderDownColor: COLORS.down,
          wickUpColor: COLORS.up,
          wickDownColor: COLORS.down,
          priceLineVisible: true,
        })
    markersRef.current = createSeriesMarkers(mainRef.current as ISeriesApi<'Candlestick'>, [])
  }, [bodiesOnly])

  // push data + overlays
  useEffect(() => {
    const chart = chartRef.current
    const main = mainRef.current
    const ema = emaRef.current
    if (!chart || !main || !ema) return
    const cs = candles.data?.candles ?? []
    const digits = priceDigits(symbol, candles.data?.digits)
    const minMove = Number((1 / 10 ** digits).toFixed(digits))
    main.applyOptions({ priceFormat: { type: 'price', precision: digits, minMove } })
    ema.applyOptions({ priceFormat: { type: 'price', precision: digits, minMove } })

    const sorted = [...cs].sort((a, b) => a.time - b.time)
    if (bodiesOnly) {
      ;(main as ISeriesApi<'Line'>).setData(sorted.map((c) => ({ time: c.time as UTCTimestamp, value: c.close })))
    } else {
      ;(main as ISeriesApi<'Candlestick'>).setData(
        sorted.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })),
      )
    }

    const ov = overlays.data
    const firstTime = sorted.length ? sorted[0].time : 0
    // EMA
    const emaPts = toggles.ema && ov ? [...ov.ema].filter((p) => p.time >= firstTime).sort((a, b) => a.time - b.time) : []
    ema.setData(emaPts.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })))

    // price lines: zones, structure, positions
    for (const l of linesRef.current) {
      try {
        main.removePriceLine(l)
      } catch {
        /* series replaced */
      }
    }
    linesRef.current = []
    const addLine = (price: number | null | undefined, color: string, title: string, style: LineStyle, width: 1 | 2 = 1, axis = true) => {
      if (price === null || price === undefined || !Number.isFinite(price)) return
      linesRef.current.push(
        main.createPriceLine({ price, color, title, lineStyle: style, lineWidth: width, axisLabelVisible: axis }),
      )
    }
    if (ov && toggles.zones) {
      for (const z of ov.zones) {
        const base = z.side === 'support' ? COLORS.support : z.side === 'resistance' ? COLORS.resistance : COLORS.neutral
        const color = withAlpha(base, z.valid ? 0.9 : 0.35)
        const style = !z.valid ? LineStyle.Dotted : z.confined ? LineStyle.Solid : LineStyle.Dashed
        const title = `${z.tf} AOI ×${z.touches}${z.valid ? '' : ' (invalid)'}`
        addLine(z.z_max, color, title, style, z.valid ? 2 : 1, false)
        addLine(z.z_min, color, '', style, z.valid ? 2 : 1, false)
      }
    }
    if (ov?.structure && toggles.structure) {
      const st = ov.structure
      addLine(st.active_HH, COLORS.structure, 'HH', LineStyle.LargeDashed)
      addLine(st.active_HL, COLORS.structure, 'HL', LineStyle.LargeDashed)
      addLine(st.active_LH, COLORS.structure, 'LH', LineStyle.LargeDashed)
      addLine(st.active_LL, COLORS.structure, 'LL', LineStyle.LargeDashed)
    }
    if (ov && toggles.trades) {
      for (const p of ov.positions) {
        addLine(p.entry_price, COLORS.entry, `${p.direction} entry`, LineStyle.Solid, 2)
        addLine(p.sl, COLORS.sl, 'SL', LineStyle.Dashed, 2)
        addLine(p.tp, COLORS.tp, 'TP', LineStyle.Dashed, 2)
      }
    }

    // markers (must sit on existing bars and be time-sorted)
    const snap = snapper(sorted)
    const markers: SeriesMarker<Time>[] = []
    if (ov && toggles.swings) {
      for (const s of ov.swings) {
        const t = snap(s.time)
        if (t === null) continue
        const high = s.kind === 'HIGH'
        markers.push({
          time: t as UTCTimestamp,
          position: high ? 'aboveBar' : 'belowBar',
          shape: 'circle',
          size: 0.5,
          color: high ? '#fca5a5' : '#86efac',
          text: s.label ?? (high ? 'H' : 'L'),
        })
      }
    }
    if (ov && toggles.events) {
      for (const e of ov.events) {
        const t = snap(e.time)
        if (t === null) continue
        const bull = e.type.includes('BULL')
        const sweep = e.type === 'LIQUIDITY_SWEEP'
        markers.push({
          time: t as UTCTimestamp,
          position: sweep ? 'inBar' : bull ? 'belowBar' : 'aboveBar',
          shape: sweep ? 'square' : bull ? 'arrowUp' : 'arrowDown',
          color: sweep ? '#facc15' : bull ? '#4ade80' : '#f87171',
          text: sweep ? 'sweep' : e.type.startsWith('INIT') ? (bull ? 'INIT▲' : 'INIT▼') : bull ? 'BOS▲' : 'BOS▼',
        })
      }
    }
    if (ov && toggles.trades) {
      for (const tr of ov.trades) {
        const t = snap(tr.time)
        if (t === null) continue
        const buy = tr.direction === 'BUY'
        if (tr.kind === 'entry') {
          markers.push({
            time: t as UTCTimestamp,
            position: buy ? 'belowBar' : 'aboveBar',
            shape: buy ? 'arrowUp' : 'arrowDown',
            color: '#60a5fa',
            text: `${tr.direction} @ ${fmtPrice(tr.price, symbol, digits)}`,
          })
        } else {
          markers.push({
            time: t as UTCTimestamp,
            position: buy ? 'aboveBar' : 'belowBar',
            shape: 'square',
            color: tr.exit_reason === 'TP' ? '#22c55e' : tr.exit_reason === 'SL' ? '#ef4444' : '#e5e7eb',
            text: `exit ${tr.exit_reason ?? ''}`.trim(),
          })
        }
      }
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number))
    markersRef.current?.setMarkers(markers)

    const key = `${symbol}|${tf}|${limit}|${bodiesOnly}`
    if (sorted.length && fittedKey.current !== key) {
      chart.timeScale().fitContent()
      fittedKey.current = key
    }
  }, [candles.data, overlays.data, toggles, bodiesOnly, symbol, tf, limit])

  const st = overlays.data?.structure
  const lastCandle = candles.data?.candles?.length ? candles.data.candles[candles.data.candles.length - 1] : null
  const digits = candles.data?.digits

  const toggle = (k: keyof Toggles) => setToggles((t) => ({ ...t, [k]: !t[k] }))
  const zonesSorted: Zone[] = useMemo(
    () => [...(overlays.data?.zones ?? [])].sort((a, b) => b.z_max - a.z_max),
    [overlays.data],
  )

  return (
    <div className="page">
      <Card
        title="Chart"
        actions={
          <div className="toolbar">
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              {(symbols.data?.symbols ?? []).map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
            <div className="seg">
              {timeframes.map((t) => (
                <button key={t} className={`seg-btn ${t === tf ? 'active' : ''}`} onClick={() => setTf(t)}>
                  {t}
                </button>
              ))}
            </div>
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} title="bars">
              {LIMITS.map((l) => (
                <option key={l} value={l}>
                  {l} bars
                </option>
              ))}
            </select>
            <label className="check">
              <input type="checkbox" checked={bodiesOnly} onChange={() => setBodiesOnly(!bodiesOnly)} /> Bodies only
            </label>
            <button className="btn btn-small" onClick={refresh}>
              {candles.loading || overlays.loading ? <Spinner /> : 'Refresh'}
            </button>
          </div>
        }
      >
        <div className="toolbar toggles">
          {(Object.keys(toggles) as (keyof Toggles)[]).map((k) => (
            <label key={k} className="check">
              <input type="checkbox" checked={toggles[k]} onChange={() => toggle(k)} /> {k}
            </label>
          ))}
          {st && (
            <span className="structure-info">
              <Badge tone={st.state === 'BULLISH' ? 'ok' : st.state === 'BEARISH' ? 'bad' : 'muted'}>{st.state}</Badge>{' '}
              {st.state === 'BULLISH' && (
                <span className="muted small">
                  HH {fmtPrice(st.active_HH, symbol, digits)} · HL {fmtPrice(st.active_HL, symbol, digits)}
                </span>
              )}
              {st.state === 'BEARISH' && (
                <span className="muted small">
                  LH {fmtPrice(st.active_LH, symbol, digits)} · LL {fmtPrice(st.active_LL, symbol, digits)}
                </span>
              )}
            </span>
          )}
          {lastCandle && (
            <span className="muted small mono">
              O {fmtPrice(lastCandle.open, symbol, digits)} H {fmtPrice(lastCandle.high, symbol, digits)} L{' '}
              {fmtPrice(lastCandle.low, symbol, digits)} C {fmtPrice(lastCandle.close, symbol, digits)}
            </span>
          )}
        </div>
        <ErrorBox error={symbols.error ?? candles.error ?? overlays.error} onRetry={refresh} />
        <div ref={el} className="price-chart" />
        {ready && !candles.loading && !candles.error && !(candles.data?.candles?.length) && (
          <p className="muted">No candles for {symbol} {tf} yet (bot warming up or MT5 disconnected).</p>
        )}
      </Card>

      <Card title={`AOI zones — ${symbol || '…'}`}>
        {zonesSorted.length ? (
          <div className="table-wrap">
            <table className="table compact">
              <thead>
                <tr>
                  <th>TF</th>
                  <th className="num">z_max</th>
                  <th className="num">z_min</th>
                  <th className="num">Width</th>
                  <th className="num">Touches</th>
                  <th>Side</th>
                  <th>Valid</th>
                  <th>Confined</th>
                </tr>
              </thead>
              <tbody>
                {zonesSorted.map((z, i) => (
                  <tr key={`${z.tf}-${z.z_min}-${i}`} className={z.valid ? '' : 'dim'}>
                    <td>{z.tf}</td>
                    <td className="num">{fmtPrice(z.z_max, symbol, digits)}</td>
                    <td className="num">{fmtPrice(z.z_min, symbol, digits)}</td>
                    <td className="num">{fmtPips(z.z_max - z.z_min, symbol)}</td>
                    <td className="num">{z.touches}</td>
                    <td>
                      <Badge tone={z.side === 'support' ? 'ok' : z.side === 'resistance' ? 'bad' : 'info'}>{z.side}</Badge>
                    </td>
                    <td>{z.valid ? '✓' : '✗'}</td>
                    <td>{z.confined ? '✓' : '✗'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">No zones.</p>
        )}
      </Card>
    </div>
  )
}
