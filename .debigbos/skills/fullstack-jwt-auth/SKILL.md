---
name: fullstack-jwt-auth
description: Debug and fix JWT token refresh issues in React + Django REST stacks. Covers interceptor patterns, common pitfalls (missing await, fetch vs axios, network vs auth errors), and token storage.
---

# Fullstack JWT Auth Debugging (React + Django REST)

When the user reports unexpected logout or blank auth-dependent dropdowns while tokens should still be valid, follow this checklist.

## Quick Diagnostic Checklist

1. **Check console logs** — The codebase has `[Auth]`, `[Axios]`, `[Token]` prefixed logs. Grep for these.
2. **Identify the path** — Is the issue in `doInit()` (app startup) or the axios response interceptor (mid-session)?
3. **Check for missing `await`** — In async Redux action creators, especially `doInit()`, verify `refreshTokenApi()` calls have `const result = await`.
4. **Check HTTP client consistency** — Raw `fetch()` does NOT include the `Authorization` header. Only `axios` benefits from the global interceptor. Search for `fetch(` calls hitting authenticated endpoints.
5. **Check interceptor error handling** — Does the response interceptor differentiate network errors (no logout) from real auth errors (logout)?

## Architecture (Smart Services Reference)

```
Token storage: localStorage ('token', 'refresh_token')
Auth header:   axios.interceptors.request → adds 'Bearer ' + token
Refresh flow:  axios.interceptors.response → 401 → handleRefresh() → doRefresh() → POST /auth/token/refresh/
Queue:         refreshCoordinator.js — serializes concurrent 401s so only one refresh call runs
Backend:       custom_token_refresh (manual JWT decode, bypasses blacklist race condition)
```

## Common Pitfalls

### 1. Missing `await` on refreshTokenApi() in doInit()
**File:** `actions/auth.js` ~line 115
**Symptom:** App loads, access token expired, refresh token valid → user gets logged out.
**Root cause:** `refreshTokenApi(refresh)` called without `const result = await`. `result` is undefined → `ReferenceError` → caught → logout.

### 2. `fetch()` instead of `axios` for authenticated endpoints
**Symptom:** Dropdown/select empty, no visible error, console shows 401.
**Root cause:** `fetch('/api/users/')` doesn't attach `Authorization: Bearer <token>`. Use `axios.get('/users/')` which goes through the request interceptor.
**Search pattern:** `grep -rn "fetch('/api/" src/` to find all offenders.

### 3. Interceptor logs out on network errors
**File:** `index.js` ~line 130
**Symptom:** User gets logged out when refresh endpoint is temporarily unreachable (network blip, server restart).
**Root cause:** `catch (refreshError)` block clears localStorage and redirects for ANY error type, not just 401/403.
**Fix:** Check `refreshError.response?.status` — only logout on 401/403. Network errors (`!refreshError.response`) should be ignored.

### 4. Token lifetime mismatch
**Backend:** refresh token = 15 min (no remember_me) or 30 days (remember_me).
**Access token:** default 5 min (SimpleJWT).
**Pitfall:** User on non-remember_me session, idle >15 min → refresh token itself expires → legit logout. Not a bug — tell user to check remember_me or increase lifetimes in `authentication/views.py`.

### 5. Nginx `Connection: upgrade` causes 502 on refresh
**Symptom:** Token refresh always gets 502 on first call, succeeds on retry (500ms later). Only via nginx (port 80), not Vite directly (port 3000).
**Root cause:** `proxy_set_header Connection "upgrade"` applied to all requests (for HMR/WebSocket). Normal HTTP requests with `Connection: upgrade` break keep-alive after 401 → next request on stale connection → 502.
**Fix:** Conditional `$connection_upgrade` — `upgrade` for WebSocket, `close` for HTTP. See `smartservices-frontend-patterns` skill, reference `nginx-connection-upgrade-fix.md`.

## Key Files (Smart Services)
```
ss_app/src/index.js              — axios interceptors + doRefresh()
ss_app/src/actions/auth.js       — doInit(), loginUser(), logoutUser()
ss_app/src/services/authService.js — API calls (refreshToken, signInLocal)
ss_app/src/services/refreshCoordinator.js — queue for concurrent 401s
ss_api/authentication/views.py   — custom_token_refresh, login, logout
ss_api/core/settings.py          — DEFAULT_PERMISSION_CLASSES, JWT settings
```

## Overlap Note

This skill overlaps with `smartservices-frontend-patterns` which is the canonical reference for Smart Services project patterns including JWT token refresh, axios vs fetch, nginx 502 fix, and permission-based UI. New Smart Services patterns should be added to that skill. This skill may be absorbed into it in the future.

## Verification

After fixing, test:
1. Login → wait 5 min → perform action → should auto-refresh, not logout.
2. DevTools Network tab → filter `/auth/token/refresh/` → verify it fires on first 401 after expiry.
3. Open two tabs → both should survive token expiry (queue mechanism test).
4. Disconnect network → perform action → should show error, NOT logout.
