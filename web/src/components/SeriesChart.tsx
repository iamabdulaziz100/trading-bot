// Small time-series chart (line or area) on lightweight-charts. Used for the
// dashboard equity sparkline and the backtest equity / drawdown charts.
import { useEffect, useRef } from 'react'
import {
  AreaSeries,
  ColorType,
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'

export interface Point {
  time: number
  value: number
}

/** lightweight-charts requires strictly increasing unique times. */
export function dedupeSorted(points: Point[]): Point[] {
  const sorted = [...points].filter((p) => Number.isFinite(p.time) && Number.isFinite(p.value)).sort((a, b) => a.time - b.time)
  const out: Point[] = []
  for (const p of sorted) {
    if (out.length && out[out.length - 1].time === p.time) out[out.length - 1] = p
    else out.push(p)
  }
  return out
}

export function SeriesChart({
  points,
  height = 240,
  kind = 'line',
  color = '#3b82f6',
  minimal = false,
  valueFormatter,
}: {
  points: Point[]
  height?: number
  kind?: 'line' | 'area'
  color?: string
  minimal?: boolean
  valueFormatter?: (v: number) => string
}) {
  const el = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Line'> | ISeriesApi<'Area'> | null>(null)

  useEffect(() => {
    if (!el.current) return
    const chart = createChart(el.current, {
      height,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#9ca3af',
        attributionLogo: false,
      },
      grid: {
        vertLines: { visible: !minimal, color: '#1f2937' },
        horzLines: { visible: !minimal, color: '#1f2937' },
      },
      rightPriceScale: { visible: !minimal, borderColor: '#374151' },
      timeScale: { visible: !minimal, borderColor: '#374151', timeVisible: true },
      handleScroll: !minimal,
      handleScale: !minimal,
      crosshair: { vertLine: { visible: !minimal }, horzLine: { visible: !minimal } },
      localization: valueFormatter ? { priceFormatter: valueFormatter } : undefined,
    })
    const series =
      kind === 'area'
        ? chart.addSeries(AreaSeries, {
            lineColor: color,
            topColor: `${color}55`,
            bottomColor: `${color}05`,
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: !minimal,
          })
        : chart.addSeries(LineSeries, {
            color,
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: !minimal,
          })
    chartRef.current = chart
    seriesRef.current = series
    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [height, kind, color, minimal, valueFormatter])

  useEffect(() => {
    const s = seriesRef.current
    if (!s) return
    const data = dedupeSorted(points).map((p) => ({ time: p.time as UTCTimestamp, value: p.value }))
    s.setData(data)
    chartRef.current?.timeScale().fitContent()
  }, [points, height, kind, color, minimal, valueFormatter])

  return <div ref={el} className="series-chart" style={{ height }} />
}
