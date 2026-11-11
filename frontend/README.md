# FinAlly frontend

Next.js (App Router) + TypeScript + Tailwind v4, built as a static export and served by FastAPI.

| Command | What it does |
|---|---|
| `npm run dev` | Dev server on :3000; proxies `/api/*` to `FINALLY_BACKEND_URL` (default `http://localhost:8000`) |
| `npm run build` | Static export to `out/` (copy into `backend/static/`) |
| `npm test` | Vitest + React Testing Library (needs Node 22+) |
| `npm run lint` / `npm run typecheck` | ESLint / `tsc --noEmit` |

Layout: `app/` (page shell), `components/` (UI panels), `hooks/` (SSE stream, REST data),
`lib/` (API client, types, formatting, portfolio math, treemap, price store).
