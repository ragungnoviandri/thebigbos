# Auth Debugging Patterns (Smart Services)

## Symptom 1: Public Endpoints Return 401

**Error:** `GET /api/antrian/layanan/ 401 (Unauthorized)`  
**View has:** `@permission_classes([AllowAny])`  
**User state:** Logged in but access token expired (browser console shows `[Token] Access: ❌ EXPIRED`)

### Root Cause
Frontend axios interceptor attaches `Authorization: Bearer <token>` to EVERY request. Django's `JWTAuthentication` validates the token BEFORE the permission class runs. If the token is expired, `JWTAuthentication` raises `AuthenticationFailed` → 401, and `AllowAny` never gets a chance to run.

### Fix
Use `GracefulJWTAuthentication` — catches `AuthenticationFailed`/`InvalidToken` and returns `None` (AnonymousUser) instead of raising. The permission class then makes the access decision normally.

```python
# core/auth_backend.py
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken

class GracefulJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except (AuthenticationFailed, InvalidToken):
            return None
```

Register in `settings.py`:
```python
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "core.auth_backend.GracefulJWTAuthentication",
    ),
}
```

### Verification
```bash
docker exec api python -c "
import requests
# Without token
r1 = requests.get('http://localhost:5005/api/antrian/layanan/')
print(f'No token: {r1.status_code}')   # 200
# With invalid token
r2 = requests.get('http://localhost:5005/api/antrian/layanan/',
    headers={'Authorization': 'Bearer garbage'})
print(f'Invalid token: {r2.status_code}')   # 200 (was 401)
# IsAuthenticated still works
r3 = requests.get('http://localhost:5005/api/antrian/')
print(f'Protected without token: {r3.status_code}')   # 401
"
```

## Symptom 2: Login Succeeds But Stays on Login Page

**Error:** No console error, but after submitting credentials, user remains on `/login`.

### Root Cause
`loginUser` Redux action does not dispatch a navigation action.

### Fix
Add `dispatch(push('/'))` after `dispatch(doInit())`.

```javascript
dispatch(receiveToken(result.access));
dispatch(doInit());
dispatch(push('/'));  // ← missing line
```

### ⚠️ React Router v6 Caveat
This project uses **React Router v6** but the `push()` action in `actions/navigation.js` uses `window.history.pushState` + `PopStateEvent` which **React Router v6 does not respond to**. Use `window.location.href` instead:

```javascript
dispatch(receiveToken(result.access));
await dispatch(doInit());    // await first
window.location.href = '/';  // hard redirect
```

See the main SKILL.md "Frontend Auth Flow" section for details.

## Symptom 3: 500 on SDP Proxy

**Error:** `GET /sdp-proxy/biometrickunjungan/home 500 (Internal Server Error)`  
**Console:** `AttributeError: 'Cookie' object has no attribute 'max_age'`

### Root Cause
Accessing `cookie.max_age` on `requests.cookies.RequestsCookieJar` cookies. The `http.cookiejar.Cookie` object does NOT have a `.max_age` attribute.

### Fix
Replace with safe attribute access:

| Broken | Fixed |
|--------|-------|
| `cookie.max_age` | `None` |
| `cookie.httponly` | `bool(cookie.get_nonstandard_attr('httponly', False))` |
| `cookie.get(...)` | `cookie.get_nonstandard_attr('samesite', 'Lax')` |

## Symptom 5: Iframe Shows Login Form but Stays on POST URL After Submit / "Confirm Form Resubmission"

**Error:** Iframe at `/app/antrian/loket` should load `/sdp-proxy/biometrickunjungan/home` but shows the SDP login form. After filling credentials and submitting, the iframe URL changes to `/sdp-proxy/Login/loginsimple` and shows "Confirm Form Resubmission" on refresh.

### Root Cause
The SDP external service uses a **POST → 302 redirect → GET** authentication flow:
1. Login form POSTs to `/sdp/Login/loginsimple`
2. SDP authenticates → 302 redirect to `/sdp/biometrickunjungan/home` + `Set-Cookie` session
3. The proxy had `allow_redirects=True`, so `requests` followed the redirect internally
4. The session cookie was consumed by the proxy, never reaching the browser
5. Browser URL stayed at `/sdp-proxy/Login/loginsimple` (the POST endpoint)
6. On refresh, browser saw it was a POST endpoint → "Confirm Form Resubmission"

### Diagnosis
Check two things:

```bash
# 1. Does the external service respond with a redirect?
curl -s -o /dev/null -w '%{http_code}' \
  -X POST http://sdp.rutanjakpus.id/sdp/Login/loginsimple \
  -d 'username=test&password=test' --max-time 10
# If 302, the proxy must handle it manually (see fix below)

# 2. After successful login (from another tab/browser), does the page load directly?
# Open http://sdp.rutanjakpus.id/sdp/biometrickunjungan/home in a new tab
# Log in there, then go back to the iframe page
# If the iframe loads the authenticated page, the proxy session cookie path is broken
```

### Fix
Change `allow_redirects=True` to `allow_redirects=False` in the HTML proxy and handle redirects manually:

1. The proxy returns the **302 status + rewrote Location header + Set-Cookie** to the browser
2. The browser stores the session cookie from Set-Cookie
3. The browser follows the redirect (Location → rewritten proxy URL)
4. The proxy forwards the GET with the session cookie from the browser
5. The external service sees the valid session → returns the authenticated page

### Code Pattern
See the "Full HTML Proxy" section in the main SKILL.md — the reference code now shows `allow_redirects=False` with manual redirect handling.

### Key Insight
When the externally-proxied service uses **cookie-based session auth** with a redirect chain, the `requests` library's automatic redirect following (`allow_redirects=True`) **consumes the session cookies** that should go to the browser. Always return 3xx redirects to the browser so cookies are handled naturally by the browser's redirect processing.

This differs from **token-based auth** (JWT, API keys) where the proxy can safely follow redirects because auth is carried in headers or request bodies, not cookies.

## Symptom 4: SDP Proxy Shows Plain Text Error Page

**Error:** `503 (Service Unavailable)` when SDP server is unreachable.

### Fix
Replace inline `<h3>` with a centered HTML page with refresh button using the `sdp_error_page()` helper function (defined in `sdp_proxy/views.py`).

```python
def sdp_error_page(title, message):
    """Return centered HTML error page with refresh button."""
    return f'''<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Proxy</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#f5f5f5; display:flex; align-items:center; justify-content:center;
    min-height:100vh; color:#333; }}
  .card {{ background:#fff; border-radius:12px; padding:48px 40px; text-align:center;
    box-shadow:0 2px 12px rgba(0,0,0,0.08); max-width:420px; width:90%; }}
  .icon {{ font-size:48px; margin-bottom:16px; display:block; }}
  h3 {{ font-size:20px; font-weight:600; margin-bottom:8px; color:#dc3545; }}
  p {{ font-size:14px; color:#666; margin-bottom:24px; line-height:1.5; }}
  button {{ background:#0d6efd; color:#fff; border:none; border-radius:8px;
    padding:10px 28px; font-size:14px; font-weight:500; cursor:pointer; }}
  button:hover {{ background:#0b5ed7; }}
</style>
</head>
<body>
<div class="card">
  <span class="icon">🔌</span>
  <h3>{title}</h3>
  <p>{message}</p>
  <button onclick="location.reload()">🔄 Coba Lagi</button>
</div>
</body>
</html>'''
```
