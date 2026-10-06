import { useState, type ReactNode } from 'react'

export function Card({ title, children, className = '', actions }: {
  title?: ReactNode
  children: ReactNode
  className?: string
  actions?: ReactNode
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h3>{title}</h3>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  )
}

export type Tone = 'ok' | 'bad' | 'warn' | 'info' | 'muted'

export function Badge({ tone = 'muted', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  )
}

export function PassFail({ ok, label, title }: { ok: boolean | null | undefined; label: string; title?: string }) {
  const tone: Tone = ok === null || ok === undefined ? 'muted' : ok ? 'ok' : 'bad'
  return (
    <Badge tone={tone} title={title}>
      {label} {ok === null || ok === undefined ? '·' : ok ? '✓' : '✗'}
    </Badge>
  )
}

export function ErrorBox({ error, onRetry }: { error: string | null | undefined; onRetry?: () => void }) {
  if (!error) return null
  return (
    <div className="error-box">
      <span>{error}</span>
      {onRetry && (
        <button className="btn btn-small" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

export function Spinner() {
  return <span className="spinner" aria-label="loading" />
}

export function Tabs<T extends string>({ tabs, value, onChange }: {
  tabs: { key: T; label: ReactNode }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={value === t.key}
          className={`tab ${value === t.key ? 'active' : ''}`}
          onClick={() => onChange(t.key)}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}

export function Pagination({ page, pageSize, total, onPage }: {
  page: number
  pageSize: number
  total: number
  onPage: (p: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return (
    <div className="pagination">
      <button className="btn btn-small" disabled={page <= 1} onClick={() => onPage(1)}>
        «
      </button>
      <button className="btn btn-small" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        ‹ Prev
      </button>
      <span className="muted">
        Page {page} / {pages} · {total} rows
      </span>
      <button className="btn btn-small" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Next ›
      </button>
      <button className="btn btn-small" disabled={page >= pages} onClick={() => onPage(pages)}>
        »
      </button>
    </div>
  )
}

export function Collapsible({ title, children, defaultOpen = false }: {
  title: ReactNode
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="collapsible">
      <button className="collapsible-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="caret">{open ? '▾' : '▸'}</span> {title}
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  )
}

export function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>
}

export function Stat({ label, value, tone, sub }: { label: string; value: ReactNode; tone?: Tone; sub?: ReactNode }) {
  return (
    <div className={`stat ${tone ? `stat-${tone}` : ''}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub !== undefined && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

export function ProgressBar({ fraction, tone }: { fraction: number | null | undefined; tone?: Tone }) {
  const f = fraction === null || fraction === undefined || !Number.isFinite(fraction) ? 0 : fraction
  const pct = Math.max(0, Math.min(1, f)) * 100
  const auto: Tone = f >= 1 ? 'bad' : f >= 0.66 ? 'warn' : 'ok'
  return (
    <div className="progress">
      <div className={`progress-fill tone-${tone ?? auto}`} style={{ width: `${pct}%` }} />
    </div>
  )
}
