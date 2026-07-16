# Vite Proxy 502 Keep-Alive Race — Full Debugging Trace

**Date:** 2026-06-25
**Project:** Smart Services (RUTAN Jakarta Pusat)
**Symptom:** `POST /api/auth/token/refresh/` returns 502 Bad Gateway on FIRST attempt, ALWAYS succeeds on retry (500ms later). Only happens on specific pages (jurnal harian edit).

## Architecture

```
Browser → Vite :3000 → (http-proxy) → Gunicorn :5005 → Django
```

All on Docker, single laptop — no network latency.

## Elimination Process

| Attempt | Hypothesis | Fix Tried | Result |
|---------|-----------|-----------|--------|
| 1 | axios connection reuse | Added 100ms delay before refresh | ❌ Still 502 |
| 2 | axios interceptor recursion | Replaced `axios.post()` with native `fetch()` | ❌ Still 502 |
| 3 | HTTP `Connection: close` header | Added to fetch headers | ❌ Browser drops forbidden header |
| 4 | Vite proxy keep-alive pool | `agent: false` in vite.config.js | ✅ PENDING TEST |

## Root Cause

Vite's http-proxy uses **keep-alive connection pooling** by default. When:

1. The jurnal save POST returns 401, gunicorn keeps the connection alive (default keepalive: 2s)
2. The token refresh `fetch()` fires immediately — Vite proxy tries to REUSE the existing keep-alive connection to gunicorn
3. But gunicorn has already started closing the connection (keepalive timeout window)
4. http-proxy sends the refresh request on a half-closed socket → **502 Bad Gateway**
5. 500ms retry → Vite opens a NEW connection → works perfectly

This ONLY manifests on pages where the failed request (returning 401) and the refresh request happen in rapid succession through the same proxy.

## Why Jurnal Only?

The jurnal save (`PUT /api/dashboard/jurnal/{uuid}/`) has a larger payload (many JSON fields). The request-response cycle timing aligns with gunicorn's keepalive timeout window, making the race more likely. Lighter endpoints (GET lists) complete faster and don't hit the window.

## Fix

```javascript
// vite.config.js
proxy: {
    '/api': {
        target: proxyTarget,
        changeOrigin: true,
        agent: false,  // ← disable keep-alive pooling
    },
}
```

`agent: false` forces http-proxy to create a new TCP connection for each request instead of reusing from the pool. Slightly more overhead but eliminates the race entirely.

**Requires Vite dev server restart** — vite.config.js changes don't hot-reload.

## Key Insight

When debugging 502 errors in Vite proxy with gunicorn backend:
- **First attempt fails, retry succeeds** → keep-alive race (fix: `agent: false`)
- **All attempts fail** → gunicorn worker deadlock or backend crash (fix: increase workers, check logs)
- **Intermittent across all pages** → network issue or resource exhaustion
