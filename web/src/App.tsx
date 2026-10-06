import { useState, type ReactNode } from 'react'
import { HashRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { api, errorMessage } from './api'
import { Badge } from './components/ui'
import BacktestsPage from './pages/Backtests'
import ChartsPage from './pages/Charts'
import DashboardPage from './pages/Dashboard'
import JournalPage from './pages/Journal'
import LogsPage from './pages/Logs'
import SettingsPage from './pages/Settings'
import { StatusProvider, useStatus } from './status'
import { useWsConnected } from './ws'

const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/charts', label: 'Charts' },
  { to: '/journal', label: 'Journal' },
  { to: '/backtests', label: 'Backtests' },
  { to: '/settings', label: 'Settings' },
  { to: '/logs', label: 'Logs' },
]

function TopBar() {
  const { status, offline } = useStatus()
  const wsLive = useWsConnected()
  const mt5 = status?.mt5
  const bot = status?.bot
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">MSC</span>
        <span className="brand-name">Market Structure &amp; Confluence Bot</span>
      </div>
      <nav className="nav">
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
            {n.label}
          </NavLink>
        ))}
      </nav>
      <div className="topbar-pills">
        {offline ? (
          <Badge tone="bad">BACKEND OFFLINE</Badge>
        ) : (
          <>
            <Badge tone={mt5?.connected ? 'ok' : 'bad'} title={mt5?.message ?? undefined}>
              MT5 {mt5?.connected ? 'ON' : 'OFF'}
            </Badge>
            <Badge tone={bot?.halted ? 'bad' : bot?.enabled ? 'ok' : 'warn'}>
              BOT {bot?.halted ? 'HALTED' : bot?.enabled ? 'ENABLED' : 'DISABLED'}
            </Badge>
          </>
        )}
        <Badge tone={wsLive ? 'info' : 'muted'} title="WebSocket live updates">
          {wsLive ? 'LIVE' : 'WS…'}
        </Badge>
      </div>
    </header>
  )
}

function Banners() {
  const { status, offline, error, refresh } = useStatus()
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildMsg, setRebuildMsg] = useState<string | null>(null)

  if (offline || (!status && error)) {
    return (
      <div className="banners">
        <div className="banner banner-bad">
          <strong>Backend offline.</strong> Cannot reach the bot at this address ({error ?? 'no response'}). Retrying…
        </div>
      </div>
    )
  }
  if (!status) return null

  const items: { tone: string; node: ReactNode; key: string }[] = []
  if (!status.mt5?.connected) {
    items.push({
      key: 'mt5',
      tone: 'bad',
      node: (
        <>
          <strong>MT5 DISCONNECTED</strong>
          {status.mt5?.available === false ? ' — MetaTrader5 package not available on this host.' : ''}
          {status.mt5?.message ? ` (${status.mt5.message})` : ''} New entries are paused.
        </>
      ),
    })
  }
  if (status.bot?.halted) {
    items.push({
      key: 'halt',
      tone: 'bad',
      node: (
        <>
          <strong>TRADING HALTED</strong> — {status.bot.halt_reason ?? 'safety halt'}. Open positions remain protected by SL/TP.
        </>
      ),
    })
  }
  if (status.daily?.cutoff_hit) {
    items.push({
      key: 'cutoff',
      tone: 'warn',
      node: (
        <>
          <strong>Daily loss cutoff hit</strong> — new entries halted until the next day boundary.
        </>
      ),
    })
  }
  if (status.news?.warning) {
    items.push({ key: 'news', tone: 'warn', node: <><strong>News feed:</strong> {status.news.warning}</> })
  }
  for (const [i, w] of (status.warnings ?? []).entries()) {
    if (w === status.news?.warning) continue
    items.push({ key: `w${i}`, tone: 'warn', node: w })
  }
  if (status.bot?.rebuild_required) {
    items.push({
      key: 'rebuild',
      tone: 'info',
      node: (
        <>
          Structure parameters changed — rebuild required.{' '}
          <button
            className="btn btn-small"
            disabled={rebuilding}
            onClick={async () => {
              setRebuilding(true)
              setRebuildMsg(null)
              try {
                await api.rebuild()
                setRebuildMsg('Rebuild started')
                await refresh()
              } catch (e) {
                setRebuildMsg(errorMessage(e))
              } finally {
                setRebuilding(false)
              }
            }}
          >
            {rebuilding ? 'Rebuilding…' : 'Rebuild now'}
          </button>
          {rebuildMsg && <span className="muted"> {rebuildMsg}</span>}
        </>
      ),
    })
  }
  if (!items.length) return null
  return (
    <div className="banners">
      {items.map((b) => (
        <div key={b.key} className={`banner banner-${b.tone}`}>
          {b.node}
        </div>
      ))}
    </div>
  )
}

export default function App() {
  return (
    <HashRouter>
      <StatusProvider>
        <div className="app">
          <TopBar />
          <Banners />
          <main className="main">
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/charts" element={<ChartsPage />} />
              <Route path="/journal" element={<JournalPage />} />
              <Route path="/backtests" element={<BacktestsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/logs" element={<LogsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
        </div>
      </StatusProvider>
    </HashRouter>
  )
}
