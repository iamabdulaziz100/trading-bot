// Global bot status: REST poll (5 s fallback) + WebSocket `status` pushes.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, ApiError, errorMessage } from './api'
import { useInterval } from './hooks'
import type { Status } from './types'
import { useWsChannel, useWsReconnect } from './ws'

interface StatusCtx {
  status: Status | null
  /** Date.now() when `status` was received; used to tick countdowns locally. */
  receivedAt: number
  offline: boolean
  error: string | null
  refresh: () => Promise<void>
  setStatus: (s: Status) => void
}

const Ctx = createContext<StatusCtx>({
  status: null,
  receivedAt: 0,
  offline: false,
  error: null,
  refresh: async () => {},
  setStatus: () => {},
})

export function StatusProvider({ children }: { children: ReactNode }) {
  const [status, setStatusState] = useState<Status | null>(null)
  const [receivedAt, setReceivedAt] = useState(0)
  const [offline, setOffline] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inflight = useRef(false)

  const setStatus = useCallback((s: Status) => {
    setStatusState(s)
    setReceivedAt(Date.now())
    setOffline(false)
    setError(null)
  }, [])

  const refresh = useCallback(async () => {
    if (inflight.current) return
    inflight.current = true
    try {
      setStatus(await api.status())
    } catch (e) {
      setOffline(e instanceof ApiError && e.offline)
      setError(errorMessage(e))
    } finally {
      inflight.current = false
    }
  }, [setStatus])

  useEffect(() => {
    void refresh()
  }, [refresh])
  useInterval(() => void refresh(), 5000)
  useWsChannel('status', (s) => setStatus(s))
  useWsReconnect(() => void refresh())

  return (
    <Ctx.Provider value={{ status, receivedAt, offline, error, refresh, setStatus }}>{children}</Ctx.Provider>
  )
}

export function useStatus(): StatusCtx {
  return useContext(Ctx)
}
