// Formatting helpers: JPY-aware pips/prices, money, timestamps.

export function isJpy(symbol: string | null | undefined): boolean {
  return !!symbol && symbol.toUpperCase().includes('JPY')
}

/** Forex pip size: 0.01 for JPY-quoted pairs, otherwise 0.0001 (spec 02 §B). */
export function pipSize(symbol: string | null | undefined): number {
  return isJpy(symbol) ? 0.01 : 0.0001
}

/** Price digits: explicit `digits` from the backend when known, else 3 (JPY) / 5. */
export function priceDigits(symbol: string | null | undefined, digits?: number | null): number {
  if (digits !== null && digits !== undefined && Number.isFinite(digits)) return digits
  return isJpy(symbol) ? 3 : 5
}

export function fmtPrice(v: number | null | undefined, symbol?: string | null, digits?: number | null): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return v.toFixed(priceDigits(symbol, digits))
}

export function toPips(distance: number | null | undefined, symbol: string | null | undefined): number | null {
  if (distance === null || distance === undefined || !Number.isFinite(distance)) return null
  return distance / pipSize(symbol)
}

export function fmtPips(distance: number | null | undefined, symbol: string | null | undefined): string {
  const p = toPips(distance, symbol)
  return p === null ? '—' : `${p.toFixed(1)} pips`
}

export function fmtMoney(v: number | null | undefined, currency?: string | null, signed = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  let s: string
  try {
    s = new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: currency || 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(Math.abs(v))
  } catch {
    s = `${Math.abs(v).toFixed(2)} ${currency ?? ''}`.trim()
  }
  if (v < 0) return `−${s}`
  return signed && v > 0 ? `+${s}` : s
}

export function fmtNum(v: number | null | undefined, digits = 2, signed = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const s = v.toFixed(digits)
  return signed && v > 0 ? `+${s}` : s
}

export function fmtPct(v: number | null | undefined, digits = 2, signed = false): string {
  const s = fmtNum(v, digits, signed)
  return s === '—' ? s : `${s}%`
}

/** Fraction (0..1) as percent. */
export function fmtRatio(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return `${(v * 100).toFixed(digits)}%`
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toISOString().replace('T', ' ').slice(0, 19) + 'Z'
}

export function fmtEpoch(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return '—'
  return fmtTime(new Date(sec * 1000).toISOString())
}

export function fmtDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`
}

export function pnlClass(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v) || v === 0) return ''
  return v > 0 ? 'pos' : 'neg'
}

export function todayUtc(): string {
  return new Date().toISOString().slice(0, 10)
}

export function daysAgoUtc(n: number): string {
  return new Date(Date.now() - n * 86400_000).toISOString().slice(0, 10)
}
