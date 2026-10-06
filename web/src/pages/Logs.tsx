import { useEffect, useMemo, useRef, useState } from 'react'
import { api, errorMessage } from '../api'
import { Card, ErrorBox, Spinner } from '../components/ui'
import type { LogLine } from '../types'
import { useWsChannel, useWsReconnect } from '../ws'

const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
const MAX_LINES = 5000

function levelRank(l: string): number {
  const u = l.toUpperCase()
  const i = LEVELS.indexOf(u === 'WARN' ? 'WARNING' : u === 'FATAL' ? 'CRITICAL' : u)
  return i < 0 ? 1 : i
}

const key = (l: LogLine) => `${l.ts}|${l.level}|${l.module}|${l.message}`

export default function LogsPage() {
  const [lines, setLines] = useState<LogLine[]>([])
  const [level, setLevel] = useState('INFO')
  const [q, setQ] = useState('')
  const [paused, setPaused] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const pausedBuffer = useRef<LogLine[]>([])

  const load = async () => {
    setLoading(true)
    try {
      const r = await api.logs({ level: 'DEBUG', tail: 500 })
      // merge with anything that arrived over WS meanwhile, de-duplicated
      setLines((cur) => {
        const seen = new Set<string>()
        const merged: LogLine[] = []
        for (const l of [...r.lines, ...cur]) {
          const k = key(l)
          if (seen.has(k)) continue
          seen.add(k)
          merged.push(l)
        }
        merged.sort((a, b) => a.ts.localeCompare(b.ts))
        return merged.slice(-MAX_LINES)
      })
      setError(null)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])
  useWsReconnect(() => void load())
  useWsChannel('log', (l) => {
    if (paused) {
      pausedBuffer.current.push(l)
      if (pausedBuffer.current.length > MAX_LINES) pausedBuffer.current.shift()
      return
    }
    setLines((cur) => {
      const next = cur.length >= MAX_LINES ? cur.slice(cur.length - MAX_LINES + 1) : cur.slice()
      next.push(l)
      return next
    })
  })

  const resume = () => {
    const buf = pausedBuffer.current
    pausedBuffer.current = []
    setLines((cur) => [...cur, ...buf].slice(-MAX_LINES))
    setPaused(false)
  }

  const filtered = useMemo(() => {
    const min = levelRank(level)
    const needle = q.trim().toLowerCase()
    return lines.filter(
      (l) =>
        levelRank(l.level) >= min &&
        (!needle || l.message.toLowerCase().includes(needle) || (l.module ?? '').toLowerCase().includes(needle)),
    )
  }, [lines, level, q])

  useEffect(() => {
    if (paused || !box.current) return
    box.current.scrollTop = box.current.scrollHeight
  }, [filtered, paused])

  return (
    <div className="page">
      <Card
        title="Logs"
        actions={
          <div className="toolbar">
            <select value={level} onChange={(e) => setLevel(e.target.value)}>
              {LEVELS.map((l) => (
                <option key={l}>{l}</option>
              ))}
            </select>
            <input type="search" placeholder="search…" value={q} onChange={(e) => setQ(e.target.value)} />
            {paused ? (
              <button className="btn btn-small btn-ok" onClick={resume}>
                Resume ({pausedBuffer.current.length})
              </button>
            ) : (
              <button className="btn btn-small" onClick={() => setPaused(true)}>
                Pause
              </button>
            )}
            <button className="btn btn-small" onClick={() => void load()}>
              {loading ? <Spinner /> : 'Reload'}
            </button>
            <button className="btn btn-small" onClick={() => setLines([])}>
              Clear view
            </button>
            <a className="btn btn-small" href={api.logsDownloadUrl()} download>
              Download log file
            </a>
          </div>
        }
      >
        <ErrorBox error={error} onRetry={() => void load()} />
        <div className="muted small">
          {filtered.length} of {lines.length} lines {paused ? '· paused' : '· live'}
        </div>
        <div ref={box} className="logbox mono">
          {filtered.map((l, i) => (
            <div key={i} className={`logline lvl-${l.level.toUpperCase()}`}>
              <span className="log-ts">{l.ts.replace('T', ' ').replace('Z', '')}</span>{' '}
              <span className="log-lvl">{l.level.toUpperCase().padEnd(8)}</span>{' '}
              {l.module && <span className="log-mod">[{l.module}]</span>} <span className="log-msg">{l.message}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
