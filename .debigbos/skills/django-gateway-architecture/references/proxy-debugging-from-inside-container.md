# Proxy Debugging from Inside the Container

When a Django HTML proxy (sdp-proxy) returns 502 Bad Gateway or unexpected errors,
isolate the problem by running the EXACT same request from inside the container —
this bypasses the Nginx → Vite → Django chain and tests the proxy code directly.

## Step-by-Step

### 1. Quick connectivity check

```bash
# From host machine:
docker compose exec api python -c "
import requests
# Test with the EXACT same URL the proxy would construct
r = requests.get('http://172.27.225.9/sdp/biometrickunjungan/home',
                 headers={'Host': 'sdp.rutanjakpus.id'},
                 timeout=10)
print(f'Status: {r.status_code}')
print(f'Content-Type: {r.headers.get(\"Content-Type\")}')
print(f'First 300 chars: {r.text[:300]}')
"
```

### 2. Test with the same method, headers, and body as the browser

```bash
docker compose exec api python -c "
import requests

target_url = 'http://172.27.225.9/sdp/Login/loginsimple'
headers = {
    'Host': 'sdp.rutanjakpus.id',
    'Content-Type': 'application/x-www-form-urlencoded',
    'User-Agent': 'Mozilla/5.0',
}
data = b'username=test&password=test&submit=Login'

# Test with allow_redirects=False (matching the proxy behavior)
resp = requests.post(target_url, headers=headers, data=data,
                     timeout=30, allow_redirects=False)

print(f'Status: {resp.status_code}')
print(f'Location: {resp.headers.get(\"Location\", \"none\")}')
print(f'Refresh: {resp.headers.get(\"Refresh\", \"none\")}')
print(f'Set-Cookie: {resp.headers.get(\"Set-Cookie\", \"none\")}')
print(f'Body preview: {resp.text[:300]}')
"
```

### 3. Common findings & root causes

| Finding | Root Cause | Fix |
|---------|-----------|-----|
| `ConnectionError: Failed to resolve 'sdp.rutanjakpus.id'` | `allow_redirects=True` and SDP redirects to its domain; container can't resolve it | Set `allow_redirects=False`, rewrite Location header |
| `Status: 200` from container but `502` from browser | HTTP method mismatch (proxy uses `requests.get()` for POST) | Use `requests.request(method, ...)` |
| Browser gets 302 to `http://sdp.rutanjakpus.id/...` | `allow_redirects=False` but Location not rewritten | Add Location rewrite rules for BOTH `{SDP_BASE}` and `{SDP_HOST}` |
| Login form submits but session doesn't persist | `Set-Cookie` not forwarded from SDP to browser | Forward `Set-Cookie` headers or iterate `resp.cookies` |
| Form submits, SDP returns 200 but browser shows error | `Content-Type` not forwarded — using `request.META.get('HTTP_CONTENT_TYPE')` which returns `None` in Django WSGI (WSGI spec: Content-Type is stored as `CONTENT_TYPE`, NOT `HTTP_CONTENT_TYPE`) | Use `request.META.get('CONTENT_TYPE', '')` instead of `HTTP_CONTENT_TYPE` — this is invisible: no error raised, the upstream just can't parse the body |
| POST login returns `Status: 200`, `Content-Length: 0`, empty body, `Refresh` header present but `Location` absent | SDP (or legacy app) uses meta-refresh (`Refresh` header) instead of HTTP 302 for post-login redirect. `requests` only follows 3xx, not Refresh. | Add `Refresh` header detection: extract `url=` param, rewrite the embedded URL to `/sdp-proxy/...` prefix, forward the rewritten `Refresh` header to the browser. See "Meta-Refresh (Refresh Header) Redirects" in SKILL.md. |

### 4. Path resolution for git-bash

When running `docker compose exec` from git-bash, POSIX paths like `/app/...` get
converted to Windows paths (`C:/Program Files/Git/app/...`). Use Python to read files:

```bash
# ❌ Broken — git-bash converts the path
docker compose exec api cat /app/sdp_proxy/views.py

# ✅ Works — Python opens the correct path
docker compose exec api python -c "print(open('/app/sdp_proxy/views.py').read())"
```

Or use `docker compose exec` with shell redirection:
```bash
docker compose exec api sh -c "cat /app/sdp_proxy/views.py"
```
