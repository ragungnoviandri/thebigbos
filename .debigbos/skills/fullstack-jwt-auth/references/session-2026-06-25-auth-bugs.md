# Session: 2026-06-25 — Auth Token Refresh Bugs

## Bug 1: Missing `await` in doInit()

**File:** `ss_app/src/actions/auth.js` line 115

### Error signature
```
ReferenceError: result is not defined
  at doInit (auth.js:116)
```
Token refresh fires but `result` is never assigned because `refreshTokenApi(refresh)` is called without `await` or variable capture.

### Fix
```diff
-              refreshTokenApi(refresh);
+              const result = await refreshTokenApi(refresh);
```

### Trigger
User opens app, access token expired (after 5 min idle), refresh token still valid (within 15 min / 30 day window). `doInit()` path: no token + has refresh → tries refresh → crashes → clears auth → login page.

---

## Bug 2: Network-error-as-logout in interceptor

**File:** `ss_app/src/index.js` ~line 130

### Error signature
User gets redirected to `/login` after a momentary network glitch or server restart. No auth error — just the refresh POST timed out or connection refused.

### Root cause
`catch (refreshError)` in the response interceptor unconditionally clears localStorage and redirects, even when `refreshError` has no `.response` property (network error, timeout, DNS failure).

### Fix
Only logout on real auth errors:
```javascript
const status = refreshError.response?.status;
if (status === 401 || status === 403) {
    // real auth expiry → logout
    localStorage.clear();
    window.location.href = '/login';
} else {
    // network/server error → keep session, just reject
    console.log('[Axios] Refresh failed with non-auth error, keeping session');
}
```

---

## Bug 3: `fetch()` without auth header → blank dropdown

**File:** `ss_app/src/pages/identitas/form/IdentitasFormPage.js` line 45

### Symptom
Edit identitas page → "User" select dropdown empty. Console shows 401 on `/api/users/`.

### Root cause
`fetch('/api/users/')` doesn't include `Authorization: Bearer <token>`. Only `axios` gets the token via the global request interceptor. Backend requires `IsAuthenticated`.

### Fix
```diff
- fetch('/api/users/')
+ axios.get('/users/')
```
Also add `import axios from 'axios';` at top.

### Other affected files
Search: `grep -rn "fetch('/api/" ss_app/src/`
Found: `AntrianLoketPage.js:62` — `fetch('/api/antrian/loket/')` (same issue, not yet fixed)
