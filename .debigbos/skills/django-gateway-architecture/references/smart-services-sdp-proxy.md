# Smart Services: SDP Full Proxy Implementation

## Context

The Smart Services project (Django 6.0 + React/Vite + Flask file-service + PHP SDP API) restructured from Nginx multi-location routing to a **single backend gateway** pattern where Django proxies all backend traffic internally.

## Files Changed

### Nginx
- **`nginx/conf.d/appseed-app.conf`**: From 6 locations (`/`, `/api/`, `/files/`, `/media/`, `/django-admin/`, `/sdp-proxy/`) to 1 location (`/` → app:3000)

### Django
- **`ss_api/sdp_proxy/views.py`**: Added `proxy_full` view — full HTML proxy replacing Nginx sdp-proxy with `sub_filter`
- **`ss_api/core/urls.py`**: Added `re_path(r'^sdp-proxy/(?P<path>.*)$', sdp_proxy_views.proxy_full)` + `path('files/', include('files_proxy.urls'))`
- **`ss_api/core/settings.py`**: Added `SDP_EXTERNAL_BASE` env var

### Vite
- **`ss_app/vite.config.js`**: Added proxy rules for `/files`, `/django-admin`, `/sdp-proxy` → `api:5005`
- **`ss_app/.env.local`**: Set `VITE_API_PROXY=http://localhost:5005` (local dev)

### Docker
- **`docker-compose.yaml`**: `api` service added to `frontend` network; `nginx` simplified to `frontend` only; nginx deps reduced to just `app`

## SDP Proxy View Details

The external SDP service runs at `http://sdp.rutanjakpus.id/sdp/`. 
Nginx originally used `proxy_pass` + `sub_filter` + `proxy_cookie_domain` to: 
1. Proxy `/sdp-proxy/` → `http://sdp.rutanjakpus.id/sdp/`
2. Rewrite URLs in HTML (`http://sdp.rutanjakpus.id/sdp/` → `/sdp-proxy/`)
3. Handle cookie domain rewriting

Django's replacement does the same via `requests.request()` + response body URL rewriting + cookie domain clearing.

## Network Architecture (before → after)

Before:
```
backend: db, api, nginx
frontend: nginx, app, demo
```

After:
```
backend:  db, api, file-service    (internal only)
frontend: api, app, nginx           (gateway bridge)
```

## Enhancement: Centered Error Page with Refresh (June 2026)

The bare `<h3>` error responses (`'<h3>SDP tidak dapat dijangkau</h3>'`) were replaced with a `sdp_error_page()` helper that renders a centered white card with an icon, title, description, and a **"🔄 Coba Lagi"** button that calls `location.reload()`. Both `ConnectionError` (503) and `Timeout` (504) handlers use it.

The `sdp_error_page()` function is now part of the reusable code pattern in SKILL.md.

## Known Behavior: Login Page via 200 (not Redirect)

The SDP endpoint `/sdp/biometrickunjungan/home` returns the login form as an HTML **200 response** when no session cookie exists — NOT a 302 redirect to a separate `/login` URL. This is why the iframe at `/app/antrian/loket` loads the login form directly (the proxy never follows a redirect chain). The form action targets `/sdp/Login/loginsimple` (POST), and the `url` hidden field carries `biometric/home` as the post-login redirect target.

This is different from many web apps that redirect to `/login` and is important to remember when debugging iframe loading issues — a 200 response with login form is the expected behavior, not a proxy malfunction.

## Fix: URL Rewriting in HTML Proxy Responses (June 2026)

**Symptom:** Browser shows `sdp.rutanjakpus.id's server IP address could not be found` when loading pages served through the SDP proxy (e.g., the biometric iframe on the Antrian Loket page).

**Root cause:** The SDP server returns HTML pages containing **absolute URLs** to its own domain (`http://sdp.rutanjakpus.id/sdp/css/style.css`, `<img src="http://sdp.rutanjakpus.id/sdp/images/...">`, `<script src="http://sdp.rutanjakpus.id/sdp/js/...">`). The Django proxy passes this HTML through without rewriting, so the browser tries to load those resources directly from the SDP domain — which it cannot resolve (DNS failure).

### The Fix

Before returning the proxied response to the browser, rewrite all absolute SDP URLs to proxy-relative paths:

```python
# In proxy_full view, before returning response:
content_type = resp.headers.get("Content-Type", "text/html")

if "text/html" in content_type:
    html = resp.text  # read full text for rewriting
    sdp_host = settings.SDP_HOST  # e.g. "sdp.rutanjakpus.id"
    
    # Replace ALL variations of absolute SDP URLs with proxy prefix
    for prefix in (
        f"http://{sdp_host}/sdp/",      # standard http
        f"https://{sdp_host}/sdp/",     # https (if used)
        f"//{sdp_host}/sdp/",           # protocol-relative
        f"http://{sdp_host}:80/sdp/",  # explicit port
    ):
        html = html.replace(prefix, "/sdp-proxy/")
    
    response = HttpResponse(html, content_type=content_type, status=200)
else:
    # Non-HTML: stream directly (images, JS, CSS)
    response = HttpResponse(resp.iter_content(chunk_size=8192),
                            content_type=content_type, status=200)
```

### Coverage

This handles all URL patterns found in SDP responses:
- `<link href="http://sdp.rutanjakpus.id/sdp/css/bootstrap.min.css">`
- `<img src="//sdp.rutanjakpus.id/sdp/images/bg.png">` (protocol-relative)
- `<script src="http://sdp.rutanjakpus.id/sdp/js/jquery.js">`
- `<form action="http://sdp.rutanjakpus.id/sdp/Login/check">`
- `<a href="http://sdp.rutanjakpus.id/sdp/biometric/home">`

### Why `resp.text` Over `resp.iter_content`

- `resp.text` reads the full response body into memory — necessary for string replacement operations
- Risk: very large HTML pages (>10MB) could consume significant memory
- Mitigation: SDP biometric pages are typically small (login form, dashboard)
- For non-HTML content (images, JS bundles, CSS), streaming via `iter_content` is used instead

### Verification

After applying this fix, the browser should no longer show DNS errors for the SDP domain:
1. Open `/app/antrian/loket`
2. Check browser console — no `ERR_NAME_NOT_RESOLVED` for `sdp.rutanjakpus.id`
3. Iframe should load and render SDP pages correctly
4. CSS/JS/images from SDP should all load through the proxy

### Related: The `resp.text` vs `resp.content` vs `resp.iter_content` distinction

| Method | Read Once | Seekable | Encoding | Best for |
|--------|-----------|----------|----------|----------|
| `resp.text` | Yes | No | Auto-decoded | HTML with URL rewriting |
| `resp.content` | Yes | No | Raw bytes | Binary + non-streaming |
| `resp.iter_content(n)` | No | No | Raw chunks | Streaming large files |
| `resp.raw` | No | No | Raw bytes | FileResponse streaming |

After calling `resp.text`, you CANNOT call `resp.iter_content` because the response body has already been consumed. In the current implementation, the `resp.text` branch uses `HttpResponse(html, ...)` while the `else` branch (images, etc.) uses `HttpResponse(resp.iter_content(...), ...)` — these are exclusive branches.

## Fix: Redirect-Based Auth Flow Through Proxy (June 2026)

The SDP login flow is a **POST → 302 redirect → GET** pattern:
1. Login form at `/sdp-proxy/biometrickunjungan/home` POSTs to `/sdp-proxy/Login/loginsimple`
2. Proxy forwards POST to `http://sdp.rutanjakpus.id/sdp/Login/loginsimple`
3. SDP authenticates → returns **302** to `/sdp/biometrickunjungan/home` + **Set-Cookie** for session
4. Proxy must return this redirect to the browser, NOT follow it internally

### The Bug
The original `proxy_full` used `allow_redirects=True`. When SDP returned the 302 + Set-Cookie, the `requests` library:
- Followed the redirect internally (consuming the session cookie)
- Returned the authenticated page's HTML to the browser
- BUT the browser never received the `Set-Cookie` → session was lost
- Next request from the browser (e.g., form resubmission) had no session → login loop

### The Fix
Changed to `allow_redirects=False` with manual redirect handling:

```python
resp = requests.request(..., allow_redirects=False)

if resp.status_code in (301, 302, 303, 307, 308):
    location = resp.headers.get('Location', '')
    # Rewrite Location: external → proxy
    location = location.replace('http://sdp.rutanjakpus.id/sdp', '/sdp-proxy')
    django_resp = HttpResponse(content='', status=resp.status_code)
    django_resp['Location'] = location  # browser follows this
    # Forward Set-Cookie so browser has session
    for cookie in resp.cookies:
        django_resp.set_cookie(key=cookie.name, value=cookie.value, ...)
    return django_resp
```

Now the browser:
1. Receives the 302 redirect with rewrote Location (`/sdp-proxy/biometrickunjungan/home`)
2. Stores the session cookie from Set-Cookie
3. Follows the redirect with GET + session cookie
4. SDP sees the valid session → returns the real biometric home page

### What Changed
| File | Change |
|------|--------|
| `ss_api/sdp_proxy/views.py` | `allow_redirects=True` → `False`; added redirect handling block |

## SDP Photo Download via Internal Proxy (June 2026)

When the system needs to download SDP photos (during "Perbaharui Data" flow on WBP save), use the **internal proxy path** rather than direct SDP access. This follows the single-gateway principle and avoids DNS resolution issues inside Docker containers.

### Flow

```
SDP search result → foto URL: "http://sdp.rutanjakpus.id/sdp/upload/2025/10/xxx.jpg"
                              ↓
Extract path using SDP_HOST setting: "upload/2025/10/xxx.jpg"
                              ↓
Download via: http://localhost:5005/sdp-proxy/upload/2025/10/xxx.jpg
    (calls Django's proxy_full view → proxies to SDP with Host header)
                              ↓
Upload to file-service → "identitas/{id}/foto/{id}.{ext}"
                              ↓
Save privateUrl in DB
```

### Key Components

#### 1. SDP_HOST Setting (June 2026)

To avoid hardcoding `sdp.rutanjakpus.id` everywhere, the SDP hostname is now a configurable setting:

```
# .env
SDP_BASE=http://172.27.225.9/sdp       # IP for backend HTTP requests (Docker can't resolve hostname)
SDP_HOST=sdp.rutanjakpus.id            # Domain for Host headers & frontend URL construction
```

These two settings serve different purposes:
- **`SDP_BASE`** — used for actual HTTP requests from Django containers to SDP (uses IP, works inside Docker)
- **`SDP_HOST`** — used for `Host` HTTP headers (SDP virtual hosting requires the domain) and for constructing URLs that the frontend displays and processes. SDP_HOST tidak berubah saat pindah lokasi (kantor/VPN) — hanya SDP_BASE yang berganti IP/domain.

#### 2. Path Extractor (`core/sdp_photo_handler.py`)

```python
from django.conf import settings

def extract_sdp_path(sdp_foto_url):
    """Extract path from SDP URL to use with /sdp-proxy/{path}."""
    if not sdp_foto_url:
        return None
    marker = f'{settings.SDP_HOST}/sdp/'
    if marker in sdp_foto_url:
        idx = sdp_foto_url.index(marker) + len(marker)
        path = sdp_foto_url[idx:]
        if '?' in path:
            path = path.split('?')[0]
        return path
    if '/sdp-proxy/' in sdp_foto_url:
        return sdp_foto_url.split('/sdp-proxy/')[-1]
    if sdp_foto_url.startswith('/') and not sdp_foto_url.startswith('//'):
        return sdp_foto_url.lstrip('/')
    return None
```

#### 3. Proxy Download Function

```python
def download_via_proxy(sdp_path):
    """Download SDP photo via internal proxy (Django → sdp_proxy → SDP)."""
    if not sdp_path:
        return None
    proxy_url = f'http://localhost:5005/sdp-proxy/{sdp_path}'
    try:
        resp = requests.get(proxy_url, timeout=60, stream=True)
        if resp.status_code == 200:
            ct = resp.headers.get('Content-Type', 'image/jpeg')
            if ct.startswith('image/'):
                return resp.content, ct
        return None
    except requests.RequestException:
        return None
```

⚠️ **Timeout must be 60s** — the proxy_full view itself calls SDP with `timeout=60`. Setting `timeout=15` on the internal proxy call causes `Read timed out` before the proxy finishes downloading the image.

#### 4. Upload to File-Service (with `<path:subpath>` workaround)

```python
def save_sdp_photo(sdp_foto_url, identitas_id):
    sdp_path = extract_sdp_path(sdp_foto_url)
    if not sdp_path:
        return sdp_foto_url

    result = download_via_proxy(sdp_path)
    if not result:
        return sdp_foto_url

    content, content_type = result
    ext = content_type.split('/')[-1].split(';')[0]
    ext = ext if ext in ('jpeg', 'jpg', 'png', 'gif', 'webp') else 'jpg'

    file_service = settings.FILE_SERVICE_BASE
    # ⚠️ subpath = directory only (NOT the full path with filename)
    # Flask's <path:subpath> captures everything including the filename
    subpath = f'identitas/{identitas_id}/foto'
    filename = f'{identitas_id}.{ext}'
    private_url = f'{subpath}/{filename}'

    files = {'file': (filename, content, content_type)}
    upload_resp = requests.post(
        f'{file_service}/files/upload/{subpath}',
        data={'filename': filename},  # filename as separate form field
        files=files, timeout=30
    )
    if upload_resp.status_code in (200, 201):
        return private_url
    return sdp_foto_url
```

### Integration in WBP views

In `wargabinaan/api/views.py`, the `_create_wbp` function handles both CREATE and UPDATE:

```python
foto_raw = data.get('foto') or ''
if foto_raw and not foto_raw.startswith('http'):
    foto_raw = f'http://{settings.SDP_HOST}/sdp/{foto_raw.lstrip("/")}'

# Always download fresh on update (foto can change)
if foto_raw and settings.SDP_HOST in foto_raw:
    saved = save_sdp_photo(foto_raw, str(identitas_obj.id))
    if saved and saved != foto_raw:
        foto_raw = saved

# Later...
ident.foto = foto_raw or ident.foto
```

### Folder Structure (June 2026)

The photo storage convention on the file-service is:

```
uploads/identitas/{identitas_uuid}/
├── foto/
│   └── {identitas_uuid}.{ext}        ← main SDP photo
└── carousel/
    ├── {carousel_id_1}.{ext}         ← carousel photos (for future use)
    ├── {carousel_id_2}.{ext}
    └── ...
```

This structure:
- Groups all files for one identitas under a single folder
- Separates main photo from carousel in subfolders
- Uses UUID as folder name AND filename for the main photo (easy to locate)
- Is forward-compatible with carousel/multiple-photo-per-identitas needs

### Key Design Decisions

- **Always re-download on "Perbaharui Data"**: The user explicitly chose to always download fresh rather than skip existing photos. Quote: "kadang foto berubah jg... dan g perlu bandwidth kan lokal server to server... jd klo klik perbaharui hajar aj semuanya"
- **Don't use SDP_BASE IP for downloads**: The user explicitly rejected using IP (`172.27.225.9`) or `SDP_BASE` for download URLs. These are ONLY for backend proxy configuration (`settings.py`). The correct download path is always via `/sdp-proxy/{path}`.
- **proxy_full is exempt from auth**: The `proxy_full` view uses `@csrf_exempt` and has no `@permission_classes`, so internal calls via `localhost:5005` work without authentication tokens.
- **Gunicorn needs workers ≥ 2 for self-referential HTTP calls**: When the same 1 worker handles `POST /api/wargabinaan/` and the handler calls `requests.get('http://localhost:5005/sdp-proxy/...')`, the single worker deadlocks — it's busy with the POST and can't handle the proxy GET. Set `workers = 3, threads = 2` in `gunicorn-cfg.py`. Requires full container restart.
- **Gunicorn `reload = True` causes auto-reload**: The SmartServices gunicorn config has `reload = True`, so Python file changes are auto-detected (no manual SIGHUP needed). BUT config changes (workers, threads, bind) still require full container restart. Detection: `docker compose exec api ps aux | grep gunicorn` shows the master process; code auto-reloads on edit, gunicorn config does not.
- **No `data.get('update')` flag needed anymore**: The backend 409 CONFLICT was removed — when an existing WBP with matching `nomor_registrasi` is found, the backend always updates (CREATE and UPDATE both work automatically). This means the frontend button always passing `update: false` is fine; the backend no longer relies on this flag.
- **WAJIB `from django.conf import settings`**: Setiap views.py yang pake `settings.SDP_HOST` atau `settings.SDP_BASE` harus punya import ini. Lupa import → `NameError: name 'settings' is not defined` → HTTP 500.

### React Naming Conflict: `fotoUrl` Function vs State Variable (June 2026)

**Symptom:** Console error `fotoUrl2 is not a function` saat render WBP table.

**Root cause:** Helper function `fotoUrl(foto)` defined MODULE-level shadowed by component state `const [fotoUrl, setFotoUrl] = useState('')`. Vite bundler detects the conflict and renames the module function to `fotoUrl2`, but the component calls the state variable (a string) as a function.

**Fix:** Always prefix helper function with `get` or similar to avoid collision:
```javascript
function getFotoUrl(foto) { ... }   // module-level → safe
const [fotoUrl, setFotoUrl] = useState(''); // component-level → state
```

### Auto-reload Gunicorn saat Edit File Python

SmartServices gunicorn config punya `reload = True`, jadi kalo lo edit `.py` file di host, gunicorn otomatis reload worker. **Gak perlu SIGHUP manual.** Tapi kalo edit `gunicorn-cfg.py` (workers, threads, dll), itu perlu `docker compose restart api` — minta izin user dl.

### Files Changed (This Session)

| File | Change |
|------|--------|
| `ss_api/.env` | Added `SDP_HOST=sdp.rutanjakpus.id` |
| `ss_api/core/settings.py` | Added `SDP_HOST` setting |
| `ss_api/core/sdp_photo_handler.py` | Created: extract via SDP_HOST, download via proxy with 60s timeout, upload with dir/filename split |
| `ss_api/wargabinaan/api/views.py` | Updated: use `settings.SDP_HOST` instead of hardcoded domain; call `save_sdp_photo` in both CREATE and UPDATE |
| `ss_api/pendaftaran_kunjungan/api/views.py` | Updated: use `settings.SDP_HOST` for foto URL construction |
| `ss_api/sdp_proxy/views.py` | Updated: use `settings.SDP_HOST` for Host header; added URL rewriting for HTML responses |
| `ss_api/gunicorn-cfg.py` | Updated: `workers = 3, threads = 2` (was `workers = 1`) |
| `ss_app/src/pages/wbp/list/WbpListTable.js` | Added `getFotoUrl()` helper to convert privateUrl → `/files/download?...` |

## Discovery: SDP Uses `Refresh` Header (not 302) for Post-Login Redirect (June 2026)

After the user submits the SDP login form (POST to `/sdp-proxy/Login/loginsimple`), the SDP server does **not** return a 302 redirect. Instead it returns:

```
HTTP/1.1 200 OK
Content-Type: text/html
Content-Length: 0
Refresh: 0;url=http://sdp.rutanjakpus.id/sdp/biometric/home
Set-Cookie: isSetDefaultTimeZone=1, sDp3SeSsIOn_id=abc123; path=/
```

The `Refresh` header tells the browser to navigate to the given URL **immediately** (0 seconds delay). This is an older HTTP mechanism predating JavaScript redirects.

### Impact on the Proxy

- The original `allow_redirects=True` proxy never encountered the `Refresh` header because the SDP post-login response is **Status 200**, not a 3xx. The `requests` library only follows 3xx redirects.
- Without proxy intervention, the browser receives `Refresh: 0;url=http://sdp.rutanjakpus.id/sdp/biometric/home` and navigates **directly to the external SDP server**, bypassing the proxy entirely.
- The session cookie (`Set-Cookie`) was forwarded correctly by the proxy, but the browser's auto-navigation to the external URL doesn't carry the cookie (it was set for `localhost`, not `sdp.rutanjakpus.id`).

### Fix Applied

The proxy now intercepts the `Refresh` header, rewrites the embedded URL to the proxy prefix, and forwards the rewritten header to the browser:

```python
refresh = resp.headers.get('Refresh', '')
if refresh and 'url=' in refresh.lower():
    # regex: capture URL after url= and rewrite it
    refresh = re.sub(r'url=(\S+)', rewrite_to_proxy_url, refresh, flags=re.IGNORECASE)

django_resp['Refresh'] = refresh
```

Result: browser receives `Refresh: 0;url=/sdp-proxy/biometric/home` → navigates to proxy → proxy forwards with session cookie → SDP returns real biometric page. Same-origin maintained.

### Probing Code (run from api container)

```bash
docker exec api python -c "
import requests
# Use IP + Host header — Docker can't resolve internal hostname
resp = requests.post(
    'http://172.27.225.9/sdp/Login/loginsimple',
    headers={'Content-Type': 'application/x-www-form-urlencoded', 'Host': 'sdp.rutanjakpus.id'},
    data={'username': 'ragungadmin', 'password': 'xxx', 'submit': 'Login', 'url': 'biometric/home'},
    allow_redirects=False,
    timeout=30,
)
print(f'Status: {resp.status_code}')
print(f'Refresh: {resp.headers.get(\"Refresh\", \"none\")}')
print(f'Location: {resp.headers.get(\"Location\", \"none\")}')
print(f'Body length: {len(resp.content)}')
print(f'Cookies: {[(c.name, c.value) for c in resp.cookies]}')
"
```

## Known Behavior: "Confirm Form Resubmission" Warning

When debugging the iframe, opening the view source of `/sdp-proxy/Login/loginsimple` directly (via GET) shows a browser warning: **"Confirm Form Resubmission"** / `ERR_CACHE_MISS`. This is expected — `/Login/loginsimple` is a POST-only endpoint on the SDP server. The proxy's form action correctly points to it for POST submissions. The warning appears because the browser cannot replay a POST form submission as GET via view-source. This is **not a proxy bug**.

## Enhancement: GracefulJWTAuthentication (June 2026)

After the proxy restructure, `AllowAny` endpoints like `/api/antrian/layanan/` returned 401 when the frontend's axios interceptor sent an expired access token in the `Authorization` header. Django's `JWTAuthentication` raised 401 **before** the permission class could run.

**Fix:** `GracefulJWTAuthentication` subclass catches `AuthenticationFailed`/`InvalidToken` and returns `None` (anonymous) instead. Safe because `IsAuthenticated` still rejects anonymous users — it just uses the permission check instead of the auth exception. See "Django: Public API with JWT" in SKILL.md.

## Known Bug: Cookie Forwarding (June 2026)

`proxy_full` view used `cookie.max_age`, `cookie.httponly`, `cookie.get('samesite')` — none of which exist on `http.cookiejar.Cookie` objects from the `requests` library. This caused an `AttributeError` → Django 500 for ANY proxied page.

**Fix:** Use `cookie.get_nonstandard_attr('httponly', False)` and pass `max_age=None`. Patch applied in commit (uncommitted as of 2026-06-15). The SKILL.md reference code was updated to match. Root cause: the `requests` library's `Response.cookies` iterates `http.cookiejar.Cookie` objects, which use `get_nonstandard_attr()` for non-standard attrs and lack `max_age` entirely.

## Refactoring Pattern: Replacing Hardcoded Domain Names (June 2026)

To avoid scattering `sdp.rutanjakpus.id` across the codebase, follow this refactoring pattern:

### 1. Add Setting
```python
# settings.py
SDP_HOST = os.environ.get('SDP_HOST', default='sdp.rutanjakpus.id')
```

### 2. Replace in URL construction
```python
# Before
foto_url = f'http://sdp.rutanjakpus.id/sdp/{path}'
# After
foto_url = f'http://{settings.SDP_HOST}/sdp/{path}'
```

### 3. Replace in URL detection (e.g., "is this URL from SDP?")
```python
# Before
if 'sdp.rutanjakpus.id' in foto_raw:
# After
if settings.SDP_HOST in foto_raw:
```

### 4. Replace in regex patterns
```python
# Before
SDP_FOTO_PATTERN = re.compile(r'^.*sdp\.rutanjakpus\.id/sdp/(.*?)(\?.*)?$')
# After — use settings in a helper function
def extract_sdp_path(url):
    marker = f'{settings.SDP_HOST}/sdp/'
    if marker in url:
        idx = url.index(marker) + len(marker)
        return url[idx:].split('?')[0]
    return None
```

### 5. Replace in HTTP Headers
```python
# Before
headers = {'Host': 'sdp.rutanjakpus.id'}
# After
headers = {'Host': settings.SDP_HOST}
```

### Search & Verify
```bash
# After refactoring, verify no hardcoded hostnames remain:
grep -rn 'rutanjakpus' /app --include='*.py' | grep -v '.env' | grep -v 'settings.py'
# Only settings.py and .env should appear — everything else is a bug.
```

### 6. WAJIB: Tambah import settings di views.py
```python
from django.conf import settings
```
⚠️ Kalo lupa → `NameError: name 'settings' is not defined` → **HTTP 500**.
Cek setelah refactor: `grep -rn 'settings\.SDP_HOST' /app --include='*.py' -l | xargs grep -L 'from django.conf import settings'`
