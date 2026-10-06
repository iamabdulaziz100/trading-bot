# MSC Bot — Web UI

React + Vite + TypeScript single-page app (dashboard, charts, journal, backtests, settings, logs)
built against the API contract in `../docs/API.md`.

The built bundle in `web/dist/` is committed and served by the FastAPI process at
`http://127.0.0.1:8000/`, so running the bot on Windows does **not** require Node.js.

## Develop

```bash
cd web
npm install
npm run dev        # http://localhost:5173 — proxies /api and /ws to http://127.0.0.1:8000
```

Start the bot backend first so the proxy has something to talk to.

## Build

```bash
npm run build      # tsc --noEmit (type check) + vite build -> web/dist
```

Commit the refreshed `web/dist/` after UI changes.

## Layout

- `src/types.ts` — types mirroring `docs/API.md`
- `src/api.ts` — REST client (`/api/...`, relative paths)
- `src/ws.ts` — shared `/ws` connection, exponential reconnect 1 s → 30 s, REST resync hooks
- `src/status.tsx` — global status (WS `status` push + 5 s poll fallback)
- `src/pages/*` — Dashboard, Charts, Journal, Backtests, Settings, Logs
- `src/components/*` — UI primitives and a small lightweight-charts series wrapper
