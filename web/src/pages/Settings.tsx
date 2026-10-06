import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, ApiError, errorMessage } from '../api'
import { Badge, Card, Empty, ErrorBox, Spinner, Tabs } from '../components/ui'
import { fmtTime } from '../format'
import { useAsync } from '../hooks'
import { useStatus } from '../status'
import type { ConfigObject, SchemaField, SymbolSpec, ValidationDetail } from '../types'

// ── dotted-path helpers ──────────────────────────────────────────────────────
function getPath(obj: unknown, path: string): unknown {
  let cur: unknown = obj
  for (const k of path.split('.')) {
    if (cur === null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[k]
  }
  return cur
}

function setPath(obj: ConfigObject, path: string, value: unknown): ConfigObject {
  const keys = path.split('.')
  const root: ConfigObject = { ...obj }
  let cur: Record<string, unknown> = root
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i]
    const next = cur[k]
    const copy = next && typeof next === 'object' && !Array.isArray(next) ? { ...(next as Record<string, unknown>) } : {}
    cur[k] = copy
    cur = copy
  }
  cur[keys[keys.length - 1]] = value
  return root
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string') return v === '' ? '""' : v
  return JSON.stringify(v)
}

function isPrimitiveList(v: unknown): v is (string | number)[] {
  return Array.isArray(v) && v.every((x) => typeof x === 'string' || typeof x === 'number')
}

// ── a single field ──────────────────────────────────────────────────────────
function FieldEditor({ field, value, onChange, error }: {
  field: SchemaField
  value: unknown
  onChange: (v: unknown) => void
  error?: string
}) {
  const t = field.type
  const listAsText = t === 'list' && (isPrimitiveList(value) || value === undefined || value === null)
  const numericList = listAsText && Array.isArray(field.default) && field.default.every((x) => typeof x === 'number')
  const initialText = () => {
    if (listAsText) return Array.isArray(value) ? (value as unknown[]).join(', ') : ''
    if (t === 'list' || t === 'dict') return JSON.stringify(value ?? (t === 'list' ? [] : {}), null, 2)
    return ''
  }
  const [text, setText] = useState(initialText)
  const [parseErr, setParseErr] = useState<string | null>(null)

  // keep the text box in sync when the value changes from outside (reload / reset)
  useEffect(() => {
    if (t !== 'list' && t !== 'dict') return
    try {
      const parsed = listAsText
        ? text.split(',').map((s) => s.trim()).filter(Boolean)
        : JSON.parse(text)
      const cmp = numericList ? (parsed as string[]).map(Number) : parsed
      if (!same(cmp, value)) {
        setText(initialText())
        setParseErr(null)
      }
    } catch {
      /* user is mid-edit */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  let input: ReactNode
  if (t === 'bool') {
    input = <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
  } else if (t === 'int' || t === 'float') {
    input = (
      <input
        type="number"
        value={value === null || value === undefined ? '' : String(value)}
        min={field.min ?? undefined}
        max={field.max ?? undefined}
        step={t === 'int' ? 1 : 'any'}
        onChange={(e) => {
          const s = e.target.value
          if (s === '') return onChange(null)
          const n = t === 'int' ? parseInt(s, 10) : parseFloat(s)
          onChange(Number.isFinite(n) ? n : s)
        }}
      />
    )
  } else if (t === 'enum') {
    input = (
      <select value={String(value ?? '')} onChange={(e) => {
        const opt = (field.options ?? []).find((o) => String(o) === e.target.value)
        onChange(opt ?? e.target.value)
      }}>
        {(field.options ?? []).map((o) => (
          <option key={String(o)} value={String(o)}>
            {String(o)}
          </option>
        ))}
      </select>
    )
  } else if (listAsText) {
    input = (
      <input
        type="text"
        value={text}
        placeholder="comma,separated"
        onChange={(e) => {
          setText(e.target.value)
          const parts = e.target.value.split(',').map((s) => s.trim()).filter(Boolean)
          onChange(numericList ? parts.map(Number) : parts)
        }}
      />
    )
  } else if (t === 'list' || t === 'dict') {
    input = (
      <textarea
        rows={Math.min(8, Math.max(2, text.split('\n').length))}
        value={text}
        spellCheck={false}
        onChange={(e) => {
          setText(e.target.value)
          try {
            onChange(JSON.parse(e.target.value))
            setParseErr(null)
          } catch (err) {
            setParseErr(`Invalid JSON: ${(err as Error).message}`)
          }
        }}
      />
    )
  } else {
    const secret = /password|token/i.test(field.path)
    input = (
      <input
        type={secret ? 'password' : 'text'}
        value={value === null || value === undefined ? '' : String(value)}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
      />
    )
  }

  const num = typeof value === 'number' ? value : null
  const warn =
    num !== null &&
    ((field.warn_min !== null && field.warn_min !== undefined && num < field.warn_min) ||
      (field.warn_max !== null && field.warn_max !== undefined && num > field.warn_max))
  const hard =
    num !== null &&
    ((field.min !== null && field.min !== undefined && num < field.min) ||
      (field.max !== null && field.max !== undefined && num > field.max))
  const changed = !same(value, field.default)
  const key = field.path.split('.').slice(1).join('.') || field.path

  return (
    <div className={`field ${error || hard || parseErr ? 'field-error' : warn ? 'field-warn' : ''}`}>
      <div className="field-head">
        <label className="field-name mono" title={field.path}>
          {key}
        </label>
        <span className="field-badges">
          {field.spec_ref && <Badge tone="muted">{field.spec_ref}</Badge>}
          {field.restart_required && <Badge tone="warn">restart required</Badge>}
          {field.rebuild_required && <Badge tone="info">rebuild required</Badge>}
        </span>
      </div>
      <div className="field-input">{input}</div>
      {field.description && <div className="field-desc">{field.description}</div>}
      <div className="field-meta">
        default <span className="mono">{fmtValue(field.default)}</span>
        {(field.min !== null && field.min !== undefined) || (field.max !== null && field.max !== undefined) ? (
          <>
            {' '}· range [{field.min ?? '−∞'}, {field.max ?? '∞'}]
          </>
        ) : null}
        {(field.warn_min !== null && field.warn_min !== undefined) || (field.warn_max !== null && field.warn_max !== undefined) ? (
          <>
            {' '}· spec band [{field.warn_min ?? '−∞'}, {field.warn_max ?? '∞'}]
          </>
        ) : null}
        {changed && <span className="changed"> · modified from default</span>}
      </div>
      {warn && !hard && <div className="field-msg warn">Outside the strategy-spec band — allowed, but double-check.</div>}
      {hard && <div className="field-msg bad">Outside the allowed range.</div>}
      {parseErr && <div className="field-msg bad">{parseErr}</div>}
      {error && <div className="field-msg bad">{error}</div>}
    </div>
  )
}

// ── config editor ───────────────────────────────────────────────────────────
function ConfigEditor() {
  const schema = useAsync(() => api.configSchema(), [])
  const config = useAsync(() => api.config(), [])
  const { refresh } = useStatus()
  const [draft, setDraft] = useState<ConfigObject | null>(null)
  const [section, setSection] = useState<string>('')
  const [saving, setSaving] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [topError, setTopError] = useState<string | null>(null)
  const [result, setResult] = useState<{ warnings: string[]; restart: boolean; rebuild: boolean } | null>(null)
  const [rebuildMsg, setRebuildMsg] = useState<string | null>(null)

  useEffect(() => {
    if (config.data) setDraft(config.data)
  }, [config.data])
  useEffect(() => {
    if (schema.data && !section && schema.data.sections.length) setSection(schema.data.sections[0].key)
  }, [schema.data, section])

  const dirty = useMemo(() => !!draft && !!config.data && !same(draft, config.data), [draft, config.data])
  const sec = schema.data?.sections.find((s) => s.key === section)
  const errorCountBySection = useMemo(() => {
    const out: Record<string, number> = {}
    for (const s of schema.data?.sections ?? []) {
      out[s.key] = s.fields.filter((f) => errors[f.path]).length
    }
    return out
  }, [schema.data, errors])

  const save = async () => {
    if (!draft) return
    setSaving(true)
    setErrors({})
    setTopError(null)
    setResult(null)
    setRebuildMsg(null)
    try {
      const r = await api.putConfig(draft)
      config.setData(r.config)
      setDraft(r.config)
      setResult({ warnings: r.warnings ?? [], restart: r.restart_required, rebuild: r.rebuild_required })
      void refresh()
      if (r.rebuild_required && window.confirm('Structure / AOI / pattern / candle parameters changed. Rebuild structure state now?')) {
        await doRebuild()
      }
    } catch (e) {
      if (e instanceof ApiError && Array.isArray(e.details)) {
        const map: Record<string, string> = {}
        for (const d of e.details as (ValidationDetail | { loc?: unknown[]; msg?: string })[]) {
          const path =
            'path' in d && d.path
              ? String(d.path)
              : 'loc' in d && Array.isArray(d.loc)
                ? d.loc.filter((x) => x !== 'body').join('.')
                : ''
          map[path] = map[path] ? `${map[path]}; ${d.msg}` : String(d.msg)
        }
        setErrors(map)
        const unmatched = Object.keys(map).filter(
          (p) => !(schema.data?.sections ?? []).some((s) => s.fields.some((f) => f.path === p)),
        )
        setTopError(`${e.message}${unmatched.length ? ` — ${unmatched.map((p) => `${p || '(root)'}: ${map[p]}`).join('; ')}` : ''}`)
      } else {
        setTopError(errorMessage(e))
      }
    } finally {
      setSaving(false)
    }
  }

  const doRebuild = async () => {
    try {
      const r = await api.rebuild()
      setRebuildMsg(`Rebuild triggered for ${r.symbols.join(', ') || 'all symbols'}`)
      void refresh()
    } catch (e) {
      setRebuildMsg(errorMessage(e))
    }
  }

  if (schema.error || config.error) {
    return (
      <ErrorBox
        error={schema.error ?? config.error}
        onRetry={() => {
          void schema.reload()
          void config.reload()
        }}
      />
    )
  }
  if (!schema.data || !draft) return <Spinner />

  return (
    <div className="settings">
      <aside className="settings-nav">
        {schema.data.sections.map((s) => (
          <button key={s.key} className={`settings-nav-item ${s.key === section ? 'active' : ''}`} onClick={() => setSection(s.key)}>
            {s.title}
            {errorCountBySection[s.key] > 0 && <Badge tone="bad">{errorCountBySection[s.key]}</Badge>}
          </button>
        ))}
      </aside>
      <div className="settings-body">
        <div className="toolbar sticky">
          <button className="btn btn-ok" disabled={!dirty || saving} onClick={save}>
            {saving ? 'Saving…' : 'Save config'}
          </button>
          <button className="btn" disabled={!dirty || saving} onClick={() => { setDraft(config.data); setErrors({}); setTopError(null) }}>
            Discard changes
          </button>
          {dirty && <Badge tone="warn">unsaved changes</Badge>}
          <button className="btn btn-small" onClick={doRebuild}>
            Rebuild structure state
          </button>
        </div>
        <ErrorBox error={topError} />
        {result && (
          <div className="banner banner-info small">
            Saved.{result.restart ? ' Some changes need a bot restart to take effect.' : ''}
            {result.rebuild ? ' Structure rebuild required.' : ''}
            {result.warnings.map((w, i) => (
              <div key={i}>⚠ {w}</div>
            ))}
          </div>
        )}
        {rebuildMsg && <div className="banner banner-info small">{rebuildMsg}</div>}
        {sec && (
          <div className="fields">
            {sec.fields.map((f) => (
              <FieldEditor
                key={f.path}
                field={f}
                value={getPath(draft, f.path)}
                error={errors[f.path]}
                onChange={(v) => setDraft((d) => (d ? setPath(d, f.path, v) : d))}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── symbol spec table ───────────────────────────────────────────────────────
const SPEC_COLS: { key: keyof SymbolSpec; label: string; step: string }[] = [
  { key: 'pip_size', label: 'Pip size', step: 'any' },
  { key: 'digits', label: 'Digits', step: '1' },
  { key: 'pip_value_per_lot', label: 'Pip value / lot', step: 'any' },
  { key: 'contract_size', label: 'Contract', step: '1' },
  { key: 'spread_pips', label: 'Spread (pips)', step: 'any' },
  { key: 'commission_per_lot', label: 'Commission / lot', step: 'any' },
  { key: 'min_lot', label: 'Min lot', step: 'any' },
  { key: 'max_lot', label: 'Max lot', step: 'any' },
  { key: 'lot_step', label: 'Lot step', step: 'any' },
]

function defaultSpec(symbol: string): SymbolSpec {
  const jpy = symbol.toUpperCase().includes('JPY')
  return {
    pip_size: jpy ? 0.01 : 0.0001,
    digits: jpy ? 3 : 5,
    pip_value_per_lot: 'auto',
    contract_size: 100000,
    spread_pips: 1.0,
    commission_per_lot: 0,
    min_lot: 0.01,
    max_lot: 100,
    lot_step: 0.01,
  }
}

function SymbolSpecsEditor() {
  const data = useAsync(() => api.symbolSpecs(), [])
  const [specs, setSpecs] = useState<Record<string, SymbolSpec> | null>(null)
  const [newSym, setNewSym] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (data.data) setSpecs(data.data.specs)
  }, [data.data])

  const dirty = !!specs && !!data.data && !same(specs, data.data.specs)
  const update = (sym: string, key: keyof SymbolSpec, raw: string) => {
    setSpecs((cur) => {
      if (!cur) return cur
      let v: unknown
      if (key === 'pip_value_per_lot' && raw.trim().toLowerCase() === 'auto') v = 'auto'
      else {
        const n = Number(raw)
        v = raw.trim() === '' ? (key === 'pip_value_per_lot' ? 'auto' : 0) : Number.isFinite(n) ? n : raw
      }
      return { ...cur, [sym]: { ...cur[sym], [key]: v } }
    })
  }

  const save = async () => {
    if (!specs) return
    setBusy(true)
    setMsg(null)
    try {
      const r = await api.putSymbolSpecs({ specs })
      data.setData(r)
      setSpecs(r.specs)
      setMsg('Saved')
    } catch (e) {
      setMsg(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  if (data.error) return <ErrorBox error={data.error} onRetry={() => void data.reload()} />
  if (!specs) return <Spinner />
  const symbols = Object.keys(specs).sort()
  return (
    <>
      <p className="muted small">Backtest symbol specs (config/bt_symbol_specs.yaml). Pip value "auto" is derived when USD is the base or quote currency.</p>
      <div className="table-wrap">
        <table className="table compact edit-table">
          <thead>
            <tr>
              <th>Symbol</th>
              {SPEC_COLS.map((c) => (
                <th key={String(c.key)}>{c.label}</th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {symbols.map((s) => (
              <tr key={s}>
                <td className="mono">{s}</td>
                {SPEC_COLS.map((c) => (
                  <td key={String(c.key)}>
                    <input
                      type={c.key === 'pip_value_per_lot' ? 'text' : 'number'}
                      step={c.step}
                      value={String(specs[s][c.key] ?? '')}
                      onChange={(e) => update(s, c.key, e.target.value)}
                    />
                  </td>
                ))}
                <td>
                  <button
                    className="btn btn-small"
                    onClick={() =>
                      setSpecs((cur) => {
                        if (!cur) return cur
                        const next = { ...cur }
                        delete next[s]
                        return next
                      })
                    }
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="toolbar">
        <input placeholder="Add symbol (e.g. NZDUSD)" value={newSym} onChange={(e) => setNewSym(e.target.value.toUpperCase())} />
        <button
          className="btn btn-small"
          disabled={!newSym || newSym in specs}
          onClick={() => {
            setSpecs({ ...specs, [newSym]: defaultSpec(newSym) })
            setNewSym('')
          }}
        >
          Add
        </button>
        <button className="btn btn-ok" disabled={!dirty || busy} onClick={save}>
          {busy ? 'Saving…' : 'Save specs'}
        </button>
        {dirty && (
          <button className="btn" onClick={() => setSpecs(data.data?.specs ?? null)}>
            Discard
          </button>
        )}
        {msg && <span className="small">{msg}</span>}
      </div>
    </>
  )
}

// ── news ────────────────────────────────────────────────────────────────────
function NewsPanel() {
  const events = useAsync(() => api.newsEvents(), [])
  const [file, setFile] = useState<File | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const upload = async () => {
    if (!file) return
    setBusy(true)
    setMsg(null)
    try {
      const r = await api.newsUpload(file)
      setMsg(`Imported ${r.imported} events`)
      await events.reload()
    } catch (e) {
      setMsg(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  const refreshFeed = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const r = await api.newsRefresh()
      setMsg(`Feed refreshed: ${r.fetched} events`)
      await events.reload()
    } catch (e) {
      setMsg(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const st = events.data?.status
  const now = Date.now()
  const list = (events.data?.events ?? []).slice().sort((a, b) => a.ts_utc.localeCompare(b.ts_utc))
  return (
    <>
      <div className="stats-row">
        <div>
          Feed: {st ? <Badge tone={st.feed_ok ? 'ok' : 'bad'}>{st.feed_ok ? 'OK' : 'DOWN'}</Badge> : '—'}
        </div>
        <div className="small">Last fetch: {fmtTime(st?.last_fetch_utc)}</div>
        <div className="small">Source: {st?.source ?? '—'}</div>
      </div>
      {st?.warning && <div className="banner banner-warn small">{st.warning}</div>}
      <div className="toolbar upload">
        <button className="btn btn-small" disabled={busy} onClick={refreshFeed}>
          Refresh now
        </button>
        <input type="file" accept=".csv,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <button className="btn btn-small" disabled={busy || !file} onClick={upload}>
          Upload CSV
        </button>
        {busy && <Spinner />}
        {msg && <span className="small">{msg}</span>}
      </div>
      <p className="muted small">Manual CSV columns: date,time_utc,currency,impact,title</p>
      <ErrorBox error={events.error} onRetry={() => void events.reload()} />
      {list.length ? (
        <div className="table-wrap scroll-y">
          <table className="table compact">
            <thead>
              <tr>
                <th>Time (UTC)</th>
                <th>Currency</th>
                <th>Impact</th>
                <th>Title</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {list.map((e) => (
                <tr key={e.id} className={Date.parse(e.ts_utc) < now ? 'dim' : ''}>
                  <td className="nowrap">{fmtTime(e.ts_utc)}</td>
                  <td>{e.currency}</td>
                  <td>
                    <Badge tone={e.impact.toLowerCase() === 'high' ? 'bad' : e.impact.toLowerCase() === 'medium' ? 'warn' : 'muted'}>
                      {e.impact}
                    </Badge>
                  </td>
                  <td>{e.title}</td>
                  <td className="small">{e.source ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No calendar events.</Empty>
      )}
    </>
  )
}

type Tab = 'config' | 'specs' | 'news'

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>('config')
  return (
    <div className="page">
      <Card
        title="Settings"
        actions={
          <Tabs<Tab>
            value={tab}
            onChange={setTab}
            tabs={[
              { key: 'config', label: 'Configuration' },
              { key: 'specs', label: 'Symbol specs' },
              { key: 'news', label: 'News calendar' },
            ]}
          />
        }
      >
        {tab === 'config' && <ConfigEditor />}
        {tab === 'specs' && <SymbolSpecsEditor />}
        {tab === 'news' && <NewsPanel />}
      </Card>
    </div>
  )
}
