// Thin REST client for the MSC Bot backend (docs/API.md). All paths are relative
// so the SPA works both behind the Vite dev proxy and when served by FastAPI.
import type {
  Account,
  BacktestDetail,
  BacktestRequest,
  BacktestSummary,
  CandlesResponse,
  ConfigObject,
  ConfigPutResponse,
  ConfigSchema,
  HistoryFile,
  LogLine,
  NewsEventsResponse,
  Overlays,
  Paged,
  Position,
  RiskEvent,
  Signal,
  Status,
  SymbolSpecs,
  SymbolsInfo,
  Trade,
} from './types'

export class ApiError extends Error {
  status: number
  code: string
  details: unknown

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }

  /** True when the backend itself could not be reached. */
  get offline(): boolean {
    return this.status === 0
  }
}

export type Query = Record<string, string | number | boolean | null | undefined>

export function qs(params: Query = {}): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '') continue
    sp.set(k, typeof v === 'boolean' ? (v ? '1' : '0') : String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

async function request<T>(method: string, path: string, body?: unknown, isForm = false): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: body !== undefined && !isForm ? { 'Content-Type': 'application/json' } : undefined,
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
    })
  } catch (e) {
    throw new ApiError(0, 'OFFLINE', 'Backend unreachable', String(e))
  }
  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  if (!res.ok) {
    const err = (data && typeof data === 'object' && 'error' in data ? (data as { error: unknown }).error : null) as
      | { code?: string; message?: string; details?: unknown }
      | null
    // FastAPI's default 422 body is {"detail": [...]}; normalise it too.
    const detail = data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : undefined
    throw new ApiError(
      res.status,
      err?.code ?? `HTTP_${res.status}`,
      err?.message ?? (typeof detail === 'string' ? detail : res.statusText || `HTTP ${res.status}`),
      err?.details ?? detail,
    )
  }
  return data as T
}

const get = <T>(path: string) => request<T>('GET', path)
const post = <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {})
const put = <T>(path: string, body: unknown) => request<T>('PUT', path, body)
const postForm = <T>(path: string, form: FormData) => request<T>('POST', path, form, true)

export const api = {
  // status & account
  status: () => get<Status>('/status'),
  account: () => get<Account>('/account'),
  positions: () => get<{ positions: Position[] }>('/positions'),

  // journal
  trades: (q: Query) => get<Paged<Trade>>(`/trades${qs(q)}`),
  tradesCsvUrl: (q: Query) => `/api/trades.csv${qs(q)}`,
  signals: (q: Query) => get<Paged<Signal>>(`/signals${qs(q)}`),
  riskEvents: (q: Query) => get<Paged<RiskEvent>>(`/risk_events${qs(q)}`),

  // charts
  symbols: () => get<SymbolsInfo>('/symbols'),
  candles: (symbol: string, tf: string, limit = 500) => get<CandlesResponse>(`/candles${qs({ symbol, tf, limit })}`),
  overlays: (symbol: string, tf: string) => get<Overlays>(`/overlays${qs({ symbol, tf })}`),

  // bot control
  enable: () => post<{ enabled: boolean }>('/bot/enable'),
  disable: () => post<{ enabled: boolean }>('/bot/disable'),
  panic: () => post<{ closed: number[]; failed: unknown[]; enabled: boolean }>('/bot/panic'),
  rebuild: (symbol?: string) => post<{ ok: boolean; symbols: string[] }>(`/structure/rebuild${qs({ symbol })}`),

  // config
  config: () => get<ConfigObject>('/config'),
  configSchema: () => get<ConfigSchema>('/config/schema'),
  putConfig: (cfg: ConfigObject) => put<ConfigPutResponse>('/config', cfg),
  symbolSpecs: () => get<SymbolSpecs>('/symbol_specs'),
  putSymbolSpecs: (specs: SymbolSpecs) => put<SymbolSpecs>('/symbol_specs', specs),

  // news
  newsEvents: (q: Query = {}) => get<NewsEventsResponse>(`/news/events${qs(q)}`),
  newsUpload: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return postForm<{ imported: number }>('/news/upload_csv', fd)
  },
  newsRefresh: () => post<{ ok: boolean; fetched: number }>('/news/refresh'),

  // backtests
  backtestData: () => get<{ files: HistoryFile[] }>('/backtests/data'),
  backtestUpload: (file: File, symbol: string, tf: string) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('symbol', symbol)
    fd.append('tf', tf)
    return postForm<{ ok: boolean; rows: number }>('/backtests/upload', fd)
  },
  backtests: () => get<{ items: BacktestSummary[] }>('/backtests'),
  backtest: (id: number) => get<BacktestDetail>(`/backtests/${id}`),
  runBacktest: (req: BacktestRequest) => post<{ id: number; status: string }>('/backtests', req),
  backtestCsvUrl: (id: number) => `/api/backtests/${id}/trades.csv`,

  // logs
  logs: (q: Query) => get<{ lines: LogLine[] }>(`/logs${qs(q)}`),
  logsDownloadUrl: () => '/api/logs/download',
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.offline ? 'Backend offline' : `${e.code}: ${e.message}`
  if (e instanceof Error) return e.message
  return String(e)
}
