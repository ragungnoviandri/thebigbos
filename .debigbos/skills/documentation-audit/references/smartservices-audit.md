# Smart Services — Documentation Audit (2026-06-15)

Real-world example of a documentation audit on a multi-component project (Django + React + Flutter + Docker + Nginx).

## Background

A 6-service project (api, app, mobile, file-service, sdp-api, nginx, db) with extensive markdown documentation. Task was: "baca semua dokumen dan sesuaikan dengan project yg jalan sekarang — ada perbedaan ga?"

## Files Audited

| File | Type | Lines |
|------|------|-------|
| `PROJECT.md` | Root overview | 154 |
| `SMARTSERVICES.md/INFRASTRUCTURE.md` | Infrastructure docs | 360 |
| `SMARTSERVICES.md/README.md` | Doc index | 279 |
| `nginx/conf.d/appseed-app.conf` | Actual Nginx config | 90 |
| `docker-compose.yaml` | Actual Docker config | 124 |
| `ss_app/vite.config.js` | Actual Vite config | 134 |
| `ss_app/.env.local` | Actual env vars | 2 |

## Findings (8 Total)

### Critical

| # | File | Issue | Fix |
|---|------|-------|-----|
| 1 | `PROJECT.md:103` | Quick Start says `Proxy: /api -> http://localhost:8080` | Should be `:5005` — Django runs on 5005 (nginx, docker-compose, and runserver command all agree) |
| 2 | `vite.config.js:51` | Hardcoded fallback: `proxyTarget = 'http://localhost:8080'` | Should be `http://localhost:5005` — `.env.local` doesn't set `VITE_API_PROXY`, so local dev silently proxies to wrong port |

### Misleading

| # | File | Issue | Fix |
|---|------|-------|-----|
| 3 | `INFRASTRUCTURE.md:9,79` | Says "5 services (db, api, nginx, app, **demo**)" | Should say "**file-service**" — "demo" service was removed, file-service replaced it |
| 4 | `INFRASTRUCTURE.md:281` | Network diagram says `frontend network: nginx, app, **demo**` | Should say `nginx, app` — file-service is on backend network, not frontend |
| 5 | `INFRASTRUCTURE.md:128-189` | Nginx config in docs shows only 4 `location` blocks (/, /api/, /files/, /django-admin/) | Missing: `location /sdp-proxy/`, `location /media/`, `location /static/` — all exist in actual config |

### Cosmetic

| # | File | Issue | Fix |
|---|------|-------|-----|
| 6 | `PROJECT.md:154` | `Last Updated: 2026-05-26` | `2026-06-11` — Jun 11 session section exists |
| 7 | `INFRASTRUCTURE.md:360` | `Last Updated: 2026-05-25` | Needs update |
| 8 | `SMARTSERVICES.md/README.md:275` | `Last Updated: 2026-05-25` | Needs update |

## Patterns Observed

1. **Port 8080 appears 3x where 5005 is correct** — root cause: vite.config.js had a wrong default fallback from an old template, and docs copied the wrong value downstream.
2. **"demo" service persisted in 2 doc locations** — after replacing demo with file-service, the INFRASTRUCTURE.md text and network diagram were never updated.
3. **Nginx docs were frozen at an old version** — 3 new location blocks were added later but the documentation's config excerpt was never refreshed.
4. **"Last Updated" dates only updated in PROJECT.md** — sub-docs in SMARTSERVICES.md/ were forgotten.

## Recommended Fix Order

1. Fix `vite.config.js:51` fallback default (prevents future local dev breaks)
2. Fix `PROJECT.md:103` (documentation of the fix)
3. Fix `INFRASTRUCTURE.md` (service names, networks, Nginx config)
4. Update all "Last Updated" timestamps
