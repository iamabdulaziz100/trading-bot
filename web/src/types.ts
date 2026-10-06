// Shared types mirroring docs/API.md. Many fields are nullable because the
// backend reports partial state while MT5 is disconnected.

export type Direction = 'BUY' | 'SELL'
export type Trend = 1 | 0 | -1
export type Alignment = 'LONG' | 'SHORT' | 'NEUTRAL_FILTER'

export interface ApiErrorBody {
  error: { code: string; message: string; details?: unknown }
}

// ── Status ────────────────────────────────────────────────────────────────
export interface Mt5Status {
  connected: boolean
  available: boolean
  message: string | null
  server: string | null
  login: number | null
  trade_allowed: boolean | null
  server_utc_offset_sec: number | null
}

export interface BotStatus {
  enabled: boolean
  halted: boolean
  halt_reason: string | null
  warmed_up: boolean
  started_at: string | null
  last_cycle_utc: string | null
  rebuild_required: boolean
}

export interface DailyStatus {
  day: string | null
  start_balance: number | null
  pnl: number | null
  pnl_pct: number | null
  limit_pct: number | null
  limit_money: number | null
  used_fraction: number | null
  cutoff_hit: boolean
  trades_today: number | null
}

export interface AccountSummary {
  balance: number
  equity: number
  margin: number
  free_margin: number
  currency: string
}

export interface Blackout {
  symbol: string
  currency: string
  title: string
  event_time_utc: string
  blocked_until_utc: string
  seconds_remaining: number
}

export interface NewsStatus {
  enabled: boolean
  feed_ok: boolean
  last_fetch_utc: string | null
  warning: string | null
  blackouts: Blackout[]
}

export interface SymbolStatus {
  symbol: string
  paused: boolean
  reason: string | null
  trend: Record<string, Trend>
  alignment: Alignment
  last_candle_utc: string | null
}

export interface Status {
  server_time_utc: string
  mt5: Mt5Status
  bot: BotStatus
  daily: DailyStatus
  positions: { open: number; max: number }
  account: AccountSummary | null
  news: NewsStatus
  symbols: SymbolStatus[]
  warnings: string[]
}

export interface Account {
  balance: number
  equity: number
  margin: number
  free_margin: number
  margin_level: number | null
  currency: string
  leverage: number | null
  login: number | null
  server: string | null
  name: string | null
}

export interface Position {
  ticket: number
  signal_id: string | null
  symbol: string
  direction: Direction
  lots: number
  entry_price: number
  sl: number | null
  tp: number | null
  current_price: number | null
  profit: number | null
  risk_money: number | null
  floating_r: number | null
  pillars: number | null
  open_time_utc: string | null
}

// ── Journal ───────────────────────────────────────────────────────────────
export type TradeStatus = 'PENDING' | 'OPEN' | 'CLOSED' | 'FAILED' | 'ABORTED'

export interface Trade {
  id: number
  signal_id: string | null
  symbol: string
  direction: Direction
  status: TradeStatus | string
  lots: number | null
  entry_time: string | null
  entry_price: number | null
  sl: number | null
  tp: number | null
  exit_time: string | null
  exit_price: number | null
  exit_reason: string | null
  pnl_money: number | null
  r_multiple: number | null
  risk_money: number | null
  pillars: number | null
  pattern_type: string | null
  candle_signal: string | null
  ticket: number | null
  is_backtest: boolean
  created_at: string | null
}

export interface Signal {
  id: number
  ts: string
  symbol: string
  timeframe: string
  direction: Direction | null
  pillar1: boolean
  pillar2: boolean
  pillar3: boolean
  pillar4: boolean
  ema_ok: boolean | null
  pillar_count: number
  candle_signal: string | null
  pattern: string | null
  decision: string
  reason: string | null
  signal_id: string | null
  details: Record<string, unknown> | null
  is_backtest: boolean
}

export interface RiskEvent {
  id: number
  ts: string
  symbol: string | null
  signal_id: string | null
  gate: string
  result: 'PASS' | 'BLOCK' | string
  details: string | Record<string, unknown> | null
}

export interface Paged<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

// ── Charts ────────────────────────────────────────────────────────────────
export interface SymbolsInfo {
  symbols: string[]
  timeframes: string[]
  execution_tf: string
}

export interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
}

export interface CandlesResponse {
  symbol: string
  tf: string
  pip_size: number | null
  digits: number | null
  candles: Candle[]
}

export interface Zone {
  tf: string
  z_min: number
  z_max: number
  touches: number
  valid: boolean
  confined: boolean
  side: 'support' | 'resistance' | string
}

export interface Swing {
  time: number
  price: number
  kind: 'HIGH' | 'LOW'
  label: 'HH' | 'HL' | 'LH' | 'LL' | string | null
}

export interface StructureEvent {
  time: number
  type: string
  price: number | null
}

export interface Overlays {
  structure: {
    state: string
    active_HH: number | null
    active_HL: number | null
    active_LH: number | null
    active_LL: number | null
  } | null
  zones: Zone[]
  swings: Swing[]
  events: StructureEvent[]
  ema: { time: number; value: number }[]
  positions: { direction: Direction; entry_price: number; sl: number | null; tp: number | null; open_time: number | null }[]
  trades: { time: number; price: number; direction: Direction; kind: 'entry' | 'exit'; exit_reason?: string | null }[]
}

// ── Config ────────────────────────────────────────────────────────────────
export type FieldType = 'int' | 'float' | 'bool' | 'str' | 'enum' | 'list' | 'dict'

export interface SchemaField {
  path: string
  type: FieldType | string
  default: unknown
  description: string | null
  spec_ref: string | null
  min: number | null
  max: number | null
  warn_min: number | null
  warn_max: number | null
  options: unknown[] | null
  restart_required: boolean
  rebuild_required: boolean
}

export interface SchemaSection {
  key: string
  title: string
  fields: SchemaField[]
}

export interface ConfigSchema {
  sections: SchemaSection[]
}

export type ConfigObject = Record<string, unknown>

export interface ConfigPutResponse {
  ok: boolean
  config: ConfigObject
  rebuild_required: boolean
  restart_required: boolean
  warnings: string[]
}

export interface ValidationDetail {
  path: string
  msg: string
}

export interface SymbolSpec {
  pip_size: number
  digits: number
  pip_value_per_lot: number | 'auto'
  contract_size: number
  spread_pips: number
  commission_per_lot: number
  min_lot: number
  max_lot: number
  lot_step: number
  [extra: string]: unknown
}

export interface SymbolSpecs {
  specs: Record<string, SymbolSpec>
}

// ── News ──────────────────────────────────────────────────────────────────
export interface NewsEvent {
  id: number
  ts_utc: string
  currency: string
  impact: string
  title: string
  source: string | null
}

export interface NewsEventsResponse {
  events: NewsEvent[]
  status: { feed_ok: boolean; last_fetch_utc: string | null; source: string | null; warning: string | null }
}

// ── Backtests ─────────────────────────────────────────────────────────────
export interface HistoryFile {
  symbol: string
  tf: string
  rows: number
  from: string | null
  to: string | null
}

export interface BacktestRequest {
  symbols: string[]
  date_from: string
  date_to: string
  initial_balance: number | null
  spread_pips: number | null
  slippage_pips: number | null
  commission_per_lot: number | null
  risk_per_trade_pct: number | null
}

export interface BacktestMetrics {
  trades: number
  wins: number
  losses: number
  win_rate: number | null
  net_pnl: number | null
  net_pnl_pct: number | null
  gross_profit: number | null
  gross_loss: number | null
  profit_factor: number | null
  expectancy_r: number | null
  avg_rr_realized: number | null
  max_drawdown_pct: number | null
  max_drawdown_money: number | null
  max_drawdown_duration_days: number | null
  sharpe_daily: number | null
  start_balance: number | null
  final_balance: number | null
  pillars_distribution: Record<string, number> | null
  rejected_signals: number | null
}

export type BacktestStatus = 'queued' | 'running' | 'done' | 'error'

export interface BacktestSummary {
  id: number
  ts: string
  status: BacktestStatus | string
  progress: number | null
  params: Record<string, unknown> | null
  metrics: BacktestMetrics | null
}

export interface BacktestBreakdownRow {
  trades: number
  win_rate: number | null
  net_pnl: number | null
  sum_r: number | null
}

export interface BacktestDetail extends BacktestSummary {
  error: string | null
  config_hash: string | null
  config_snapshot: Record<string, unknown> | null
  equity: { time: number; equity: number }[]
  drawdown: { time: number; dd_pct: number }[]
  per_symbol: (BacktestBreakdownRow & { symbol: string })[]
  per_month: (BacktestBreakdownRow & { month: string })[]
  trades: Trade[]
  rejected: Signal[]
  notes: string[]
}

// ── Logs ──────────────────────────────────────────────────────────────────
export interface LogLine {
  ts: string
  level: string
  module: string | null
  message: string
}

// ── WebSocket ─────────────────────────────────────────────────────────────
export type WsMessage =
  | { channel: 'status'; payload: Status }
  | { channel: 'positions'; payload: { positions: Position[] } }
  | { channel: 'signal'; payload: Signal }
  | { channel: 'trade'; payload: Trade }
  | { channel: 'log'; payload: LogLine }
  | { channel: 'backtest'; payload: { id: number; status: string; progress: number | null; metrics?: BacktestMetrics | null } }
