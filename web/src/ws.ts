// Single shared WebSocket to /ws with exponential reconnect backoff (1s → 30s).
// Components subscribe per channel; on every (re)connect the `open` listeners fire
// so pages can resync their state over REST (spec 06 §3).
import { useEffect, useRef, useSyncExternalStore } from 'react'
import type { WsMessage } from './types'

type Channel = WsMessage['channel']
type PayloadOf<C extends Channel> = Extract<WsMessage, { channel: C }>['payload']
type Handler = (payload: unknown) => void

const MIN_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 30000

class WsClient {
  private handlers = new Map<Channel, Set<Handler>>()
  private openHandlers = new Set<() => void>()
  private stateListeners = new Set<() => void>()
  private backoff = MIN_BACKOFF_MS
  private timer: number | null = null
  private started = false
  connected = false

  start() {
    if (this.started) return
    this.started = true
    this.connect()
  }

  private url(): string {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${proto}//${window.location.host}/ws`
  }

  private setConnected(v: boolean) {
    if (this.connected === v) return
    this.connected = v
    this.stateListeners.forEach((l) => l())
  }

  private connect() {
    if (this.timer !== null) {
      window.clearTimeout(this.timer)
      this.timer = null
    }
    let ws: WebSocket
    try {
      ws = new WebSocket(this.url())
    } catch {
      this.scheduleReconnect()
      return
    }
    ws.onopen = () => {
      this.backoff = MIN_BACKOFF_MS
      this.setConnected(true)
      this.openHandlers.forEach((h) => {
        try {
          h()
        } catch (e) {
          console.error('ws open handler failed', e)
        }
      })
    }
    ws.onmessage = (ev) => {
      let msg: WsMessage
      try {
        msg = JSON.parse(String(ev.data)) as WsMessage
      } catch {
        return
      }
      const hs = this.handlers.get(msg.channel)
      if (!hs) return
      hs.forEach((h) => {
        try {
          h(msg.payload)
        } catch (e) {
          console.error('ws handler failed', e)
        }
      })
    }
    ws.onclose = () => {
      this.setConnected(false)
      this.scheduleReconnect()
    }
    ws.onerror = () => {
      // onclose follows; nothing else to do
    }
  }

  private scheduleReconnect() {
    if (this.timer !== null) return
    const delay = this.backoff
    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS)
    this.timer = window.setTimeout(() => {
      this.timer = null
      this.connect()
    }, delay)
  }

  on(channel: Channel, h: Handler): () => void {
    this.start()
    let set = this.handlers.get(channel)
    if (!set) {
      set = new Set()
      this.handlers.set(channel, set)
    }
    set.add(h)
    return () => {
      set?.delete(h)
    }
  }

  onOpen(h: () => void): () => void {
    this.start()
    this.openHandlers.add(h)
    return () => {
      this.openHandlers.delete(h)
    }
  }

  subscribeState = (l: () => void): (() => void) => {
    this.start()
    this.stateListeners.add(l)
    return () => {
      this.stateListeners.delete(l)
    }
  }

  getConnected = (): boolean => this.connected
}

export const wsClient = new WsClient()

/** Subscribe to one WebSocket channel. The handler may change between renders. */
export function useWsChannel<C extends Channel>(channel: C, handler: (payload: PayloadOf<C>) => void) {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => wsClient.on(channel, (p) => ref.current(p as PayloadOf<C>)), [channel])
}

/** Run `handler` after every successful (re)connect — use it to resync via REST. */
export function useWsReconnect(handler: () => void) {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => wsClient.onOpen(() => ref.current()), [])
}

export function useWsConnected(): boolean {
  return useSyncExternalStore(wsClient.subscribeState, wsClient.getConnected, () => false)
}
