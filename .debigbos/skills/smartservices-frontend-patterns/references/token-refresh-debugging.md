# Token Refresh Debugging — Session 2026-06-25

## Root Cause: User logged out when access token expired but refresh token still valid

Two bugs found and fixed:

### Bug 1: Missing `await` in `doInit()` (auth.js line 115)

**File:** `ss_app/src/actions/auth.js`
**Symptom:** `ReferenceError: result is not defined` in console during app init

```javascript
// ❌ BROKEN
} else {
  try {
      refreshTokenApi(refresh);    // no await, no assignment
    token = result.access;         // result is undefined → crash
    ...
  } catch (e) {
    abortRefresh(e);               // caught here → logout
    ...
  }
}

// ✅ FIXED
} else {
  try {
      const result = await refreshTokenApi(refresh);
    token = result.access;
    ...
  }
}
```

**Impact:** During `doInit()` when no access token exists but refresh token does, the Promise was fired without awaiting. `result` was never assigned → `ReferenceError` → caught → user logged out unnecessarily.

### Bug 2: Network-error logout (index.js line 130)

**File:** `ss_app/src/index.js`
**Symptom:** Any error during token refresh (including network timeout) triggered full logout

```javascript
// ❌ BROKEN — logs out on ANY error
} catch (refreshError) {
    localStorage.clear();
    sessionStorage.clear();
    axios.defaults.headers.common['Authorization'] = '';
    window.location.href = '/login';
}

// ✅ FIXED — only logout on real auth errors
} catch (refreshError) {
    const status = refreshError.response?.status;
    if (status === 401 || status === 403) {
        // real auth error → logout
        localStorage.clear();
        sessionStorage.clear();
        axios.defaults.headers.common['Authorization'] = '';
        window.location.href = '/login';
    } else {
        // network error, 5xx, etc → keep session
        console.log('[Axios] Refresh failed with non-auth error, keeping session');
    }
}
```

### Bug 3: `fetch()` without auth header (IdentitasFormPage.js)

**File:** `ss_app/src/pages/identitas/form/IdentitasFormPage.js`
**Symptom:** User dropdown on identitas edit form always empty

```javascript
// ❌ BROKEN — fetch() has no Authorization header
fetch('/api/users/')
  .then(res => res.json())
  .then(data => { /* data is undefined because 401 */ })

// ✅ FIXED — axios includes token via interceptor
axios.get('/users/')
  .then(res => { /* res.data has users array */ })
```

**Key insight:** Raw `fetch()` bypasses all axios interceptors. The `Authorization: Bearer <token>` header is never sent → Django returns 401 → `.catch()` silently swallows it → state remains empty.

## Token Lifecycle in Smart Services

```
Login → access (5 min) + refresh (15 min / 30 days with remember_me)
  │
  ├─ API call → 401 → interceptor → POST /auth/token/refresh/
  │   ├─ Success → new access + new refresh → retry original request
  │   └─ 401 → both tokens expired → LOGOUT
  │   └─ Network error → keep session, reject promise
  │
  └─ App init (no token, has refresh) → doInit() → refresh → load user
```

## Backend Token Refresh (`custom_token_refresh`)

**File:** `ss_api/authentication/views.py`
- Manually decodes JWT to bypass blacklist check (handles concurrent refresh race condition)
- Preserves original token lifetime when issuing new refresh
- Blacklists old refresh token (ignores error if already blacklisted)
