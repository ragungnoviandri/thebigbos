---
name: django-gateway-architecture
category: software-development
tags: [django, nginx, docker, proxy, architecture, infrastructure]
description: Design patterns for Django as a single backend gateway — Nginx as thin proxy, Vite intermediate, Django handling all backend traffic, internal services network-isolated.
---

# Django Gateway Architecture

Design infrastructure so **Django is the single backend gateway**. No backend service (file-server, legacy API, SDP, etc.) is directly accessible from the browser or Nginx — all traffic flows through Django.

## Architecture Chain

```
Browser → Nginx → Vite/App → Django → internal services
```

### Principles
- **Nginx**: thin proxy — 1 location (`/`) passthrough to the app container only
- **Vite dev server**: serves the SPA AND proxies API paths to Django (`/api/*`, `/files/*`, `/media/*`, etc.)
- **Django**: single backend service exposed — handles all API, file serving, HTML proxying
- **Internal services**: Flask, legacy PHP APIs, other backends — only Django talks to them (network-isolated)

## Docker Compose Network Design

```yaml
networks:
  backend:
    driver: bridge
  frontend:
    driver: bridge
```

### Service-to-network assignment

| Service | Networks | Why |
|---------|----------|-----|
| db | `backend` | Only Django needs it |
| file-service | `backend` | Only Django needs it |
| api (Django) | `backend` + `frontend` | Needs DB (backend) + needs to receive proxy from Vite (frontend) |
| app (Vite) | `frontend` | Needs to proxy to Django |
| nginx | `frontend` | Only proxies to app |

- `api` on both networks = the gateway bridge
- All other backend services on `backend` only = fully internal

## Nginx: Single-Location Config

```nginx
upstream app_frontend { server app:3000; }

server {
    listen 80;
    client_max_body_size 200M;

    location / {
        proxy_pass http://app_frontend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

**Consequences:**
- No `location /api/`, `/files/`, `/media/` — Vite handles these
- No direct route to any backend service
- `/static/` serving can move to Django or stay in Nginx if needed

## Vite Proxy Configuration

In `vite.config.js` — proxy ALL paths that go to Django:

```js
proxy: {
    '/api':      { target: proxyTarget, changeOrigin: true },
    '/media':    { target: proxyTarget, changeOrigin: true },
    '/files':    { target: proxyTarget, changeOrigin: true },
    '/django-admin': { target: proxyTarget, changeOrigin: true },
    '/sdp-proxy':    { target: proxyTarget, changeOrigin: true },
    '/static':   { target: proxyTarget, changeOrigin: true },
}
```

**Must include `/static/`** — Django admin serves its CSS/JS at `/static/admin/...`. Without this rule, Vite returns 404 for admin static files because it doesn't have them in its dev bundle.

Where `proxyTarget` defaults to `http://api:5005` in Docker or `http://localhost:5005` for local dev.

Set explicitly in `.env.local`:
```
VITE_BACKEND=true
VITE_API_URL=/api
VITE_API_PROXY=http://localhost:5005
```

## Django Admin Static Files (Production with Whitenoise)

When Django runs behind gunicorn with `DEBUG=False`, it **does not serve static files** by default. In this architecture where Nginx forwards ALL traffic to Vite (no Nginx static route), you need **Whitenoise** to serve Django admin static files through the Django process itself.

### Middleware setup

Add `WhiteNoiseMiddleware` right after `SecurityMiddleware` in `settings.py`:

```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',   # <-- here
    # ... rest of middleware
]
```

### Verification

```bash
# Ensure static files are collected
python manage.py collectstatic --noinput

# Check they're served through the chain:
curl http://localhost/static/admin/css/base.css
# Expected: 200, not 404
```

### Why not Nginx?

In the single-gateway architecture, Nginx has exactly **one location** (`/` → Vite). Adding a second location for `/static/` would create an alternate backend path, violating the principle that all traffic flows through Django. Whitenoise keeps the chain clean without adding Nginx complexity.

## Django: Internal Pass-Through Proxy (API Forwarder)

For internal backend services (legacy APIs, microservices) that you call via REST — not HTML — use a simple **pass-through proxy**. Django forwards the request verbatim and returns the response.

### When to use
- The internal service exposes a REST API (JSON in/out)
- No HTML rewriting needed
- You want to **migrate gradually** — proxy first, then move logic into Django later

### View pattern

```python
import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

INTERNAL_BASE = 'http://internal-service:5006'

@csrf_exempt
def forward_to_internal(request, subpath):
    """Generic pass-through proxy — forwards request to internal service."""
    target_url = f'{INTERNAL_BASE}/{subpath}'

    # Forward file if present (e.g. upload)
    files = {}
    if 'file' in request.FILES:
        f = request.FILES['file']
        files['file'] = (f.name, f.read(), f.content_type)

    # Forward POST data
    data = {}
    for key in request.POST:
        data[key] = request.POST[key]

    try:
        resp = requests.request(
            method=request.method,
            url=target_url,
            files=files or None,
            data=data or None,
            params=request.GET,
            timeout=60,
        )
        return JsonResponse(resp.json(), status=resp.status_code, safe=False)
    except requests.RequestException:
        return JsonResponse({'error': 'Service tidak dapat dijangkau'}, status=503)


@csrf_exempt
def download_via_proxy(request):
    """Stream a file from internal service as Django FileResponse."""
    private_url = request.GET.get('privateUrl', '')
    try:
        resp = requests.get(
            f'{INTERNAL_BASE}/download',
            params={'privateUrl': private_url},
            timeout=60, stream=True,
        )
        if resp.status_code != 200:
            return JsonResponse(resp.json(), status=resp.status_code)
        return FileResponse(
            resp.raw,
            content_type=resp.headers.get('Content-Type', 'application/octet-stream'),
        )
    except requests.RequestException:
        return JsonResponse({'error': 'Service tidak dapat dijangkau'}, status=503)
```

### URL routing

```python
path('files/', include('files_proxy.urls')),
```

Where `files_proxy/urls.py`:
```python
urlpatterns = [
    path('upload/<path:subpath>', views.upload, name='files-upload'),
    path('download', views.download, name='files-download'),
    path('delete', views.delete, name='files-delete'),
    path('health', views.health, name='files-health'),
]
```

## Django: Direct File Handling (Post-Migration)

After migrating file logic from a microservice into Django, replace the pass-through proxy with direct file I/O in the same view signatures. This eliminates the external dependency.

### Config

In `settings.py`:
```python
FILE_UPLOAD_DIR = os.environ.get('FILE_UPLOAD_DIR', default=os.path.join(BASE_DIR, 'file_uploads'))
```

Use a **separate upload directory** from `MEDIA_ROOT` — `MEDIA_ROOT` is for Django ImageField/FileField-managed files; `FILE_UPLOAD_DIR` is for user-uploaded documents, avatars, etc.

### Validation rules (port from Flask)

```python
import os
import re
import logging
from django.conf import settings
from django.http import FileResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

UPLOAD_DIR = getattr(settings, 'FILE_UPLOAD_DIR', ...)
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg', 'pdf'}


def _secure_filename(filename):
    """Sanitize filename — strips path separators and dangerous chars."""
    filename = os.path.basename(filename)
    filename = re.sub(r'[^\w.\-]', '_', filename)
    return filename or 'unnamed'


def _validate_path(real_path):
    """Check for path traversal — must be inside UPLOAD_DIR."""
    uploads_real = os.path.realpath(UPLOAD_DIR)
    return os.path.realpath(real_path).startswith(uploads_real)


@csrf_exempt
def upload(request, subpath):
    """Handle multipart file upload directly in Django."""
    if 'file' not in request.FILES:
        return JsonResponse({'error': 'No file provided'}, status=400)
    file = request.FILES['file']
    filename = request.POST.get('filename', file.name)
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        return JsonResponse({'error': 'File type not allowed'}, status=400)
    if file.size > MAX_FILE_SIZE:
        return JsonResponse({'error': 'File too large (max 10MB)'}, status=413)

    safe_name = _secure_filename(filename)
    save_dir = os.path.join(UPLOAD_DIR, subpath)
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, safe_name)

    with open(save_path, 'wb+') as dest:
        for chunk in file.chunks():
            dest.write(chunk)

    return JsonResponse({'message': 'File uploaded', 'path': f'{subpath}/{safe_name}'}, status=201)
```

### Download with path traversal check
```python
@csrf_exempt
def download(request):
    private_url = request.GET.get('privateUrl', '')
    file_path = os.path.join(UPLOAD_DIR, private_url)
    real_path = os.path.realpath(file_path)
    if not _validate_path(real_path):
        return JsonResponse({'error': 'Invalid path'}, status=400)
    if not os.path.exists(real_path):
        return JsonResponse({'error': 'File not found'}, status=404)
    return FileResponse(open(real_path, 'rb'))
```

## Migration Strategy: Pass-Through → Direct Handling

When migrating an internal service's logic into Django, use this phased approach. **Important:** not all services should be migrated — some users prefer to keep services (e.g. file uploads) in separate containers for operational isolation. The proxy pattern is a valid end state, not just a transition step.

### Decision Criteria

Consider migrating **into** Django when:
- The service has trivial logic (thin validation + file I/O)
- Eliminating the container reduces operational overhead
- You own and maintain the service code

**Keep as separate container** (proxy-only) when:
- The service has complex standalone logic (transcoding, batch processing)
- You want clear operational boundaries (restart/scale independently)
- The team prefers separation of concerns (container-per-concept)

The proxy pattern works equally well as a permanent arrangement or a stepping stone.

### Phase 1: Proxy (keep existing service running)
```
Browser → Django (pass-through proxy) → internal service
```
- Django files_proxy forwards requests verbatim
- Internal service still handles all file I/O
- No changes to frontend URLs or response contracts

### Phase 2: Migrate (move logic into Django)
- Identify: validation rules (allowed extensions, file size limits, path traversal)
- Copy the logic from the internal service into Django views
- Add `FILE_UPLOAD_DIR` setting (separate from `MEDIA_ROOT`)
- **Register the proxy app in INSTALLED_APPS** — Django requires it even if the app has no models (just `views.py` + `urls.py`)
- Keep both paths working during transition (feature flag or env var)

### Phase 3: Remove
- Delete internal service from docker-compose
- Prune unused services / networks
- Remove proxy code from views (clean up unused imports)
- **Update signal handlers** that previously called the old service endpoint; either:
  - Import a shared `delete_file()` utility from the view module (see below)
  - Or call the new Django internal endpoint via `requests`
- Remove old env vars (e.g. `FILE_SERVICE_BASE`) from settings.py
- Update docs: healthcheck chain, services table, architecture diagram

### Shared Utility Pattern (Signal-Friendly)

When replacing an HTTP-based file delete call with Django internal logic, extract a standalone function so both the view and signal handlers use the same code path:

```python
# In files_proxy/views.py — added at the bottom

def delete_file(private_url: str) -> bool:
    """Delete a file by its privateUrl, returning True on success.
    Importable by views AND signal handlers — avoids duplicating
    path traversal and existence checks."""
    if not private_url:
        return False
    file_path = os.path.join(UPLOAD_DIR, private_url)
    real_path = os.path.realpath(file_path)
    if not _validate_path(real_path):
        return False
    if not os.path.exists(real_path) or not os.path.isfile(real_path):
        return False
    os.remove(real_path)
    logger.info('Deleted %s (via utility)', private_url)
    return True
```

Then in `settings/signals.py`:
```python
from files_proxy.views import delete_file

def delete_file_from_service(private_url):
    if not private_url:
        return
    delete_file(private_url)
```

## Django: Full HTML Proxy (Nginx sub_filter Replacement)

When you need to proxy an external web app (legacy UI, biometric gateway, etc.) through Django — replacing Nginx's `sub_filter` — use the pattern below.

### Key Mechanics
1. `@csrf_exempt` — the external service may POST arbitrary data
2. Forward ALL request details: method, body, headers (Cookies, User-Agent, Accept, Referer)
3. Rewrite URLs in HTML responses so the browser stays on the proxy path
4. Rewrite `Set-Cookie` domains to `None` (browser assigns current domain automatically)
5. Timeout should be generous (120s+) for legacy apps
6. **HTML paths (`/sdp/...`) need separate rewriting** — external services often emit `<form action="/sdp/Login/loginsimple">` (relative path, not full URL). These won't match `{EXTERNAL_BASE}` prefix replacements. Add explicit rewriting for `"/sdp/"` → `"/sdp-proxy/"` (and single-quoted variants).

### Reference View Structure

```python
import requests
import logging
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

SDP_EXTERNAL_BASE = 'http://external.service.internal/sdp'
SDP_PROXY_PREFIX = '/sdp-proxy'

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
  body {{
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#f5f5f5; display:flex; align-items:center; justify-content:center;
    min-height:100vh; color:#333;
  }}
  .card {{
    background:#fff; border-radius:12px; padding:48px 40px; text-align:center;
    box-shadow:0 2px 12px rgba(0,0,0,0.08); max-width:420px; width:90%;
  }}
  .icon {{ font-size:48px; margin-bottom:16px; display:block; }}
  h3 {{ font-size:20px; font-weight:600; margin-bottom:8px; color:#dc3545; }}
  p {{ font-size:14px; color:#666; margin-bottom:24px; line-height:1.5; }}
  button {{
    background:#0d6efd; color:#fff; border:none; border-radius:8px;
    padding:10px 28px; font-size:14px; font-weight:500; cursor:pointer;
  }}
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


@csrf_exempt
def proxy_full(request, path):
    target_url = f'{SDP_EXTERNAL_BASE}/{path}'
    if request.META.get('QUERY_STRING'):
        target_url += f'?{request.META["QUERY_STRING"]}'

    headers = {
        'Host': 'external.service.internal',
        'X-Real-IP': request.META.get('REMOTE_ADDR', ''),
        'User-Agent': request.META.get('HTTP_USER_AGENT', ''),
        'Accept': request.META.get('HTTP_ACCEPT', ''),
        'Accept-Language': request.META.get('HTTP_ACCEPT_LANGUAGE', ''),
    }
    if request.META.get('HTTP_COOKIE'):
        headers['Cookie'] = request.META['HTTP_COOKIE']

    data = request.body if request.method in ('POST', 'PUT', 'PATCH') else None
    content_type = request.META.get('CONTENT_TYPE', '')
    if content_type:
        headers['Content-Type'] = content_type

    try:
        resp = requests.request(
            method=request.method, url=target_url,
            headers=headers, data=data, params=request.GET,
            timeout=120, allow_redirects=False,
        )

        # ── Redirect handling: return redirect to the browser, rewrote
        #     Location so the client stays on-proxy. The browser then
        #     follows the redirect with the session cookie it just received.
        #
        # IMPORTANT: Some legacy apps use a 200 + `Refresh` header instead
        # of 3xx. After this redirect block, check "Meta-Refresh (Refresh Header)
        # Redirects" section below — you MUST handle it or the browser bypasses
        # the proxy entirely.
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get('Location', '')
            if location:
                for old_base, new_prefix in [
                (f'{SDP_EXTERNAL_BASE}/', f'{SDP_PROXY_PREFIX}/'),
                (f'http://external.service.internal/sdp/', f'{SDP_PROXY_PREFIX}/'),
                ('/legacy-base/', f'{SDP_PROXY_PREFIX}/'),
                (f'{SDP_EXTERNAL_BASE}', f'{SDP_PROXY_PREFIX}'),
                ]:
                location = location.replace(old_base, new_prefix)
                # If the external service redirects to its OWN hostname
                # (not the IP/SDP_EXTERNAL_BASE value), also try that:
                # location.startswith('http://sdp.rutanjakpus.id/sdp/')
                # → add the domain-based prefix to old_bases above.
            django_resp = HttpResponse(content='', status=resp.status_code)
            django_resp['Location'] = location
            # Forward Set-Cookie so the browser has the session for the redirect
            for cookie in resp.cookies:
                django_resp.set_cookie(
                    key=cookie.name, value=cookie.value,
                    max_age=None, expires=cookie.expires,
                    path=cookie.path or '/', domain=None,
                    secure=cookie.secure,
                    httponly=bool(cookie.get_nonstandard_attr('httponly', False)),
                    samesite=cookie.get_nonstandard_attr('samesite', 'Lax'),
                )
            return django_resp

        content_type = resp.headers.get('Content-Type', 'text/html; charset=utf-8')
        content = resp.content

        # Rewrite HTML URLs
        if 'text/html' in content_type.lower():
            content = content.decode('utf-8', errors='replace')
            for old_base, new_prefix in [
                (f'{SDP_EXTERNAL_BASE}/', f'{SDP_PROXY_PREFIX}/'),
                ('/legacy-base/', f'{SDP_PROXY_PREFIX}/'),
            ]:
                content = content.replace(old_base, new_prefix)
            # Also rewrite relative /sdp/ paths (form actions, hrefs)
            content = content.replace('"/sdp/', '"/sdp-proxy/')
            content = content.replace("'/sdp/", "'/sdp-proxy/")
            content = content.encode('utf-8')

        django_resp = HttpResponse(content=content, status=resp.status_code, content_type=content_type)

        # Forward cookies — clear domain so browser uses current domain
        for cookie in resp.cookies:
            django_resp.set_cookie(
                key=cookie.name, value=cookie.value,
                max_age=None, expires=cookie.expires,
                path=cookie.path or '/', domain=None,
                secure=cookie.secure,
                httponly=bool(cookie.get_nonstandard_attr('httponly', False)),
                samesite=cookie.get_nonstandard_attr('samesite', 'Lax'),
            )
        return django_resp

    except requests.ConnectionError:
        return HttpResponse(
            sdp_error_page('Service unreachable', 'Server sedang offline atau tidak terhubung ke internet.'),
            status=503, content_type='text/html',
        )
    except requests.Timeout:
        return HttpResponse(
            sdp_error_page('Service timeout', 'Koneksi ke server terputus. Silakan coba lagi.'),
            status=504, content_type='text/html',
        )
```

### URL Routing

In `core/urls.py`:
```python
from django.urls import path, include, re_path
from sdp_proxy import views as sdp_proxy_views

urlpatterns = [
    # ...
    re_path(r'^sdp-proxy/(?P<path>.*)$', sdp_proxy_views.proxy_full, name='sdp-proxy-full'),
]
```

## Django: Public API with JWT (Graceful Token Handling)

When a frontend axios interceptor attaches `Authorization: Bearer <token>` to **every** request (including public endpoints), Django's default `JWTAuthentication` rejects expired tokens with a 401 **before** the permission class (`AllowAny`) ever runs.

**Symptom:** Public endpoints (AllowAny) return 401 when the user has a stale token in localStorage, even though the endpoint should be openly accessible.

**Fix:** Subclass `JWTAuthentication` to fall back to anonymous user instead of raising 401:

```python
# core/auth_backend.py
import logging
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken

logger = logging.getLogger(__name__)

class GracefulJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except (AuthenticationFailed, InvalidToken):
            logger.debug(
                'GracefulJWTAuthentication: ignoring invalid/expired token, '
                'proceeding as anonymous'
            )
            return None
```

Then in `settings.py`:

```python
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "core.auth_backend.GracefulJWTAuthentication",
    ),
    ...
}
```

**Why this is safe:**
- `IsAuthenticated` still returns 401 for expired tokens (because `request.user` will be `AnonymousUser`, and `IsAuthenticated` checks `.is_authenticated`)
- `AllowAny` returns 200 regardless of token validity
- No change to how valid tokens authenticate — the subclass only catches exceptions, it doesn't alter valid token validation

## Frontend Auth Flow: Login Redirect

The `loginUser` Redux action typically stores tokens and calls `doInit()` to fetch the current user, but **forgets to navigate away from the login page**. This leaves the user staring at the sign-in form after successful authentication.

### Symptom
- User logs in successfully (tokens saved, no error)
- Console shows `[Auth] loginUser: success, storing tokens...`
- Page stays on `/login` — no redirect
- Manually navigating to `/` works (auth state is valid)

### Root Cause
The `loginUser` action does not dispatch a navigation action after success:

```javascript
// ❌ Broken — no redirect
dispatch(receiveToken(result.access));
dispatch(doInit());
// ← missing navigation

// ✅ Fixed
dispatch(receiveToken(result.access));
dispatch(doInit());
dispatch(push('/'));  // or the intended destination
```

### When This Happens
- Any custom‑written `loginUser` thunk that was originally written with `doInit()` expected to trigger a redirect (which it doesn't)
- After migrating from a pre-built auth library (e.g., `redux-auth-wrapper`) to custom thunks
- When a `PrivateRoute`/`UserRoute` guard redirects unauthenticated users to `/login` but there's no reciprocal redirect **out** of `/login` when authenticated

### Fix Checklist
1. Import `push` from the project's navigation module (e.g. `actions/navigation`)
2. Add a navigation call as the **last statement** in the success branch of `loginUser`
3. Do NOT put it inside `doInit()` — `doInit()` is shared (called from index.js on load too), and you don't want to redirect on every page reload
4. Verify that `push` is already imported at the top of the file

### Meta-Refresh (Refresh Header) Redirects

Some legacy web applications (like SDP/Lapas biometric gateways) do **not** use HTTP 3xx redirects for post-login navigation. Instead, they return:

```
HTTP/1.1 200 OK
Refresh: 0;url=http://external.service.internal/sdp/biometric/home
Content-Length: 0
Set-Cookie: session_id=abc123; path=/
```

The `Refresh` header tells the browser to immediately navigate to the given URL — effectively a redirect, but at the browser level rather than the HTTP level. The `requests` library **does not follow** `Refresh` headers; they are strictly browser-semantics.

#### Symptom (what goes wrong)

When the proxy forwards the login POST to the external service and receives a `Refresh` + `Set-Cookie` response:

- If the proxy does **nothing**: the browser receives the `Refresh` header, navigates to the original external URL (e.g. `http://external.service.internal/sdp/biometric/home`), and **bypasses the proxy entirely**.
- The session cookie (`Set-Cookie`) is correctly stored for the proxy domain, but the browser's subsequent navigation to the external URL doesn't include that cookie — the session is lost.

#### Fix: rewrite the URL inside the `Refresh` header

After building the Django `HttpResponse`, forward the rewritten `Refresh` header:

```python
# Extract and rewrite the Refresh header
refresh = resp.headers.get('Refresh', '')
if refresh and 'url=' in refresh.lower():
    import re
    def rewrite_refresh_url(m):
        url = m.group(1)
        for old_base, new_prefix in [
            (f'{SDP_EXTERNAL_BASE}/', f'{SDP_PROXY_PREFIX}/'),
            (f'http://external.service.internal/sdp/', f'{SDP_PROXY_PREFIX}/'),
            ('/legacy-base/', f'{SDP_PROXY_PREFIX}/'),
            (f'{SDP_EXTERNAL_BASE}', f'{SDP_PROXY_PREFIX}'),
        ]:
            url = url.replace(old_base, new_prefix)
        return f'{m.group(0).split("url=")[0]}url={url}'
    refresh = re.sub(r'url=(\S+)', rewrite_refresh_url, refresh, flags=re.IGNORECASE)

# ... build django_resp ...

# Set the rewritten Refresh header on the response
if refresh:
    django_resp['Refresh'] = refresh
```

The browser then receives `Refresh: 0;url=/sdp-proxy/biometric/home`, stores the session cookie, and navigates to the rewritten proxy URL — staying same-origin.

#### How to detect the header type

Run a quick probe from Django:

```bash
docker exec api python -c "
import requests
resp = requests.post(
    'http://external.service.internal/sdp/Login/loginsimple',
    headers={'Content-Type': 'application/x-www-form-urlencoded'},
    data={'username': 'test', 'password': 'test', 'submit': 'Login', 'url': 'biometric/home'},
    allow_redirects=False,
    timeout=30,
)
print(f'Status: {resp.status_code}')
print(f'Location: {resp.headers.get(\"Location\", \"none\")}')
print(f'Refresh: {resp.headers.get(\"Refresh\", \"none\")}')
print(f'Body: {repr(resp.text[:200])}')
print(f'Cookies: {list((c.name, c.value) for c in resp.cookies)}')
"
```

If `Refresh` is present but `Location` is absent → you need the meta-refresh handling above.

### ⚠️ React Router v6 Caveat: Window History API Doesn't Trigger Navigation

If the project uses **React Router v6** (look for `useNavigate` imports or `Routes`/`Route` from `'react-router-dom'`), the `push()` action from `actions/navigation.js` may use `window.history.pushState` + `PopStateEvent` dispatch, **which React Router v6 does not respond to**. The URL changes in the address bar, but the component tree does not re-render to the new route.

**Symptom:** After login success, the browser URL stays on `/login` (or changes to `/` but shows a blank/spinner page forever with no re-render).

**Fix:** Use `window.location.href` for a hard redirect, or use React Router v6's `useNavigate()` from inside a component:

```javascript
// ❌ Broken with React Router v6 — push() uses window.history.pushState
dispatch(push('/'));

// ✅ Correct — hard redirect, triggers full app re-init from localStorage
window.location.href = '/';
```

**For Redux actions** (where hooks like `useNavigate` aren't available), `window.location.href` is the correct approach. The page reloads, finds tokens in localStorage, and the app's `doInit()` on startup re-initializes authentication.

**For in-component navigation** (inside React components), prefer the standard React Router v6 hook:
```javascript
const navigate = useNavigate();
navigate('/app/home');  // works correctly from components
```

### Timing: Await doInit Before Redirect

When calling `window.location.href` after `dispatch(doInit())`, **await the thunk** to ensure token validation completes before the page unloads:

```javascript
dispatch(receiveToken(result.access));
await dispatch(doInit());    // ← await — validates token first
window.location.href = '/';  // then hard redirect
```

Without `await`, the page may reload before `doInit()` stores user/menu data, preventing subsequent pages from rendering correctly.

## Frontend Auth Flow: Token Refresh Robustness

The axios interceptor handles 401 → refresh → retry. This section covers hardening that flow against transient backend failures and avoiding silent logouts.

### Interceptor: Differentiate Auth Errors from Network Errors

When the refresh call fails, do NOT immediately clear auth and redirect to login. Only logout when the refresh endpoint explicitly returns 401/403 (refresh token truly expired). Network errors, 5xx, and timeouts should keep the session intact — the user can retry.

```javascript
// In axios response interceptor catch block:
} catch (refreshError) {
    const status = refreshError.response?.status;
    if (status === 401 || status === 403) {
        // Refresh token truly expired — logout
        localStorage.clear();
        window.location.href = '/login';
    } else {
        // Network error / 5xx — keep session, reject so caller can retry
        console.log('[Axios] Refresh failed with non-auth error, keeping session');
    }
    return Promise.reject(refreshError);
}
```

**Why this matters:** Without this differentiation, a single backend hiccup (502, 503, timeout) during token refresh logs the user out mid-workflow, even though both tokens are still valid.

### Retry on Transient Backend Errors

The `doRefresh()` function should retry on 5xx and network errors with exponential backoff. 4xx errors (like expired tokens) should fail immediately — retrying won't help.

```javascript
async function doRefresh() {
    const refreshTokenVal = localStorage.getItem('refresh_token');
    if (!refreshTokenVal) throw new Error('No refresh token');

    const maxRetries = 2;
    let lastError;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
        try {
            // Use native fetch() — NOT axios.post() — to avoid:
            // 1. axios interceptor recursion (401 on refresh → interceptor fires again)
            // 2. Connection reuse issues with Vite proxy keep-alive
            const res = await fetch('/api/auth/token/refresh/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh: refreshTokenVal }),
            });
            if (!res.ok) {
                const err = new Error(`Refresh failed: ${res.status}`);
                err.response = { status: res.status, data: await res.json().catch(() => ({})) };
                throw err;
            }
            const data = await res.json();
            const newToken = data.access;
            localStorage.setItem('token', newToken);
            if (data.refresh) {
                localStorage.setItem('refresh_token', data.refresh);
            }
            axios.defaults.headers.common['Authorization'] = 'Bearer ' + newToken;
            return newToken;
        } catch (e) {
            lastError = e;
            const status = e.response?.status;
            if ((status >= 500 || !status) && attempt < maxRetries) {
                const delay = Math.pow(2, attempt) * 500;
                await new Promise(r => setTimeout(r, delay));
                continue;
            }
            throw e;
        }
    }
    throw lastError;
}
```

### Race Condition: Multiple Simultaneous 401s

When multiple API calls return 401 simultaneously (e.g., a page loads several resources), only ONE should trigger the refresh endpoint. Use a coordinator module with a queue:

```javascript
// refreshCoordinator.js
let isRefreshing = false;
let failedQueue = [];

export function beginRefresh() {
    if (isRefreshing) return queueRequest();  // wait in line
    isRefreshing = true;
    return null;  // proceed to refresh
}
export function finishRefresh(token) {
    failedQueue.forEach(p => p.resolve(token));
    failedQueue = [];
    isRefreshing = false;
}
export function abortRefresh(error) {
    failedQueue.forEach(p => p.reject(error));
    failedQueue = [];
    isRefreshing = false;
}
```

### Bug: Missing `await` on Async Thunks

A subtle typo in Redux thunks — calling an async function without `await` AND without capturing the result:

```javascript
// ❌ Bug — no await, no assignment. result is undefined → TypeError
try {
    refreshTokenApi(refresh);
    token = result.access;  // ReferenceError or undefined
}

// ✅ Fix
try {
    const result = await refreshTokenApi(refresh);
    token = result.access;
}
```

This silently logs the user out because the catch block clears auth. The absence of `await` means the function fires but its result is never consumed.

## Backend: Security-Gated Response Fields (Public Endpoint + Conditional Private Data)

When an endpoint must serve both public and private data (e.g., a registration lookup where anyone can see form auto-fill fields, but only the authenticated owner should see sensitive history), split the response construction **inside** the view:

```python
@api_view(['GET'])
@permission_classes([AllowAny])  # public endpoint
def cek_nik(request, nik):
    # ... query data ...

    # Always return: public data (form auto-fill)
    public_data = {
        'found': True,
        'nama_lengkap': ...,
        'telepon': ...,
        'hubungan': ...,
    }

    # Conditionally return: private data (WBP history)
    user_owns_nik = (
        request.user.is_authenticated and
        hasattr(request.user, 'identitas') and
        request.user.identitas.nik == nik
    )
    if user_owns_nik:
        public_data['wbp_history'] = build_wbp_history(...)

    return Response(public_data)
```

**Why not separate endpoints?** A single public endpoint simplifies the frontend — one call, one response shape. The frontend renders what it receives; if `wbp_history` is absent, it shows nothing. No API key management, no token gatekeeping.

**Key security rule:** Never leak one user's WBP visit history to another user. Always verify NIK ownership against the authenticated user's identity.

When a form field should only be editable by admins with specific menu access:

```javascript
import { usePermission } from 'utils/usePermission';

const perm = usePermission('/admin/identitas');
const canChangeUser = isAdmin && perm.bisa_ubah;

// In JSX:
{canChangeUser ? (
    <SelectFormItem name="user_id" options={userOptions} />
) : (
    <div className="form-control bg-light text-muted" style={{ cursor: 'not-allowed' }}>
        {currentUserLabel}
    </div>
)}
```

And in the submit handler, force the value when the user can't change it:
```javascript
if (!canChangeUser) {
    data.user_id = currentUserId;
}
```

This ensures the backend receives the correct user_id regardless of what the form renders.

## References

- [requests library docs](https://requests.readthedocs.io/)
- The `references/` directory in this skill has session-specific detail:
  - `references/cross-app-lazy-imports.md` — lazy import pattern for views that reference another app's model without circular deps
  - `references/vite-django-proxy-debugging.md` — symptom → root-cause mapping for proxy chain issues
  - `references/sdp-binary-media-proxy.md` — streaming binary proxy for SDP images (StreamingHttpResponse pattern)
  - `references/vite-proxy-502-keepalive-race.md` — full debugging trace: Vite proxy keep-alive → 502 on token refresh, elimination process, fix
  - `references/smart-services-sdp-proxy.md` — SDP proxy implementation for Smart Services project
  - `references/django-orm-null-handling.md` — nullable FK + select_related guard pattern
  - `references/unified-external-api-search.md` — combining local DB + external API search through a single Django endpoint, deduplication, and source-aware frontend handling
  - `references/docker-dns-workaround.md` — using IP + Host header when Docker containers can't resolve internal hostnames
  - `references/proxy-debugging-from-inside-container.md` — step-by-step proxy debugging via `docker compose exec api python` to isolate network, header, and redirect issues

## Pitfalls

- **Keep-alive race → 502 on token refresh (Vite OR Nginx)**: When the frontend makes API calls through a proxy chain (Browser → Nginx → Vite → Gunicorn), keep-alive connections can race. The sequence: (1) axios request returns 401 → upstream keep-alive connection starts timing out, (2) token refresh `fetch()` fires immediately → proxy reuses the dying keep-alive connection → upstream has already closed it → **502 Bad Gateway**, (3) retry 500ms later → new connection → success. **Pattern**: 502 on FIRST refresh attempt, SUCCESS on retry. Happens only with larger POST requests (e.g., jurnal save with JSON payload).\n\n  **Two scenarios, two fixes:**\n\n  **A) Direct Vite access (localhost:3000):** Disable keep-alive pooling in Vite proxy:\n  ```javascript\n  // vite.config.js\n  proxy: {\n      '/api': {\n          target: proxyTarget,\n          changeOrigin: true,\n          agent: false,  // disable keep-alive → no 502 race\n      },\n  }\n  ```\n  Requires Vite dev server restart.\n\n  **B) Nginx-proxied access (localhost:80 → nginx → Vite):** The issue is nginx's `Connection: upgrade` header applied to ALL requests (meant for WebSocket/HMR but also sent on normal HTTP). Fix with conditional connection header:\n  ```nginx\n  # In http block:\n  map $http_upgrade $connection_upgrade {\n      default upgrade;\n      ''      close;              # normal HTTP → close, not upgrade\n  }\n\n  # In location block:\n  proxy_set_header Upgrade $http_upgrade;\n  proxy_set_header Connection $connection_upgrade;\n  ```\n  With this, WebSocket requests (HMR) get `upgrade`, normal API requests get `close` — preventing the half-dead keep-alive connection that causes 502. Requires `docker compose restart nginx`.\n\n  **How to identify which fix you need:** Check the console — if API URLs show `http://localhost/api/...` (no port), you're going through Nginx → use fix B. If URLs show `http://localhost:3000/api/...`, you're direct to Vite → use fix A.\n\n  See `references/vite-proxy-502-keepalive-race.md` for the full debugging trace (Vite-only variant).\n\n- **Gunicorn single-worker deadlock on self-call**: When the Django app makes an HTTP request to `localhost:<port>` (e.g. `http://localhost:5005/sdp-proxy/{path}`) and gunicorn runs with `workers=1`, the single worker **deadlocks on itself**:
  1. Worker handles the original request (e.g. `POST /api/wargabinaan/`)
  2. Handler makes `requests.get('http://localhost:5005/sdp-proxy/...')`
  3. This second request targets the **same** gunicorn — but the only worker is busy
  4. No worker available → request queues indefinitely → timeout → HTTP 500

  **Symptoms:** The internal HTTP call works when tested via `docker compose exec api python -c "..."` (fresh process), but fails silently (timeout/500) when triggered through the API. Gunicorn logs show `Read timed out` or `[ERROR] Worker exiting` + `SystemExit: 1`.

  **Fix:** Increase `workers` in `gunicorn-cfg.py` to at least 2 (preferably 3) so one worker can handle proxy/forwarding requests while another processes the main request:
  ```python
  # gunicorn-cfg.py
  workers = 3          # was 1 — at least 2 to avoid deadlock
  threads = 2          # adds thread-level concurrency within each worker
  worker_class = 'sync'  # default; 'gthread' with threads=2 works too
  ```
  After changing gunicorn config, a **full container restart** is required (`docker compose restart api` — ask user first). SIGHUP does NOT re-read the gunicorn config file, only re-starts Python code.

  **Detection:**
  ```bash
  docker compose exec api sh -c 'grep "^workers" gunicorn-cfg.py'
  docker compose exec api ps aux | grep -c gunicorn
  # If gunicorn processes count is less than 3 with workers=3, not all spawned yet
  ```

- **Gunicorn needs explicit reload after code changes (unless `reload=True`)**: When Django runs under **gunicorn** (production mode, `ps aux | grep gunicorn` shows the master process), it does **NOT** auto-reload when you edit Python files unless the gunicorn config has `reload = True`. The SmartServices project uses `reload = True`, so Python code changes ARE auto-detected and reloaded automatically — no manual SIGHUP needed for `.py` changes. **But gunicorn config changes** (`workers`, `threads`, `bind`, `timeout`) still require a full container restart (`docker compose restart api` — ask user first) — these are read at startup only. To detect which mode you're in: look for `reload = True` in `gunicorn-cfg.py`, or run `docker compose exec api ps aux | grep gunicorn` to confirm gunicorn is in use. After editing views.py, models.py, settings.py, or any imported module without `reload = True`:

  ```bash
  # Graceful reload (SIGHUP) — zero-downtime
  kill -HUP $(cat /tmp/gunicorn.pid)
  # Or if no PID file:
  docker compose exec api sh -c 'kill -HUP $(ps aux | grep gunicorn | grep -v grep | head -1 | awk "{print \\$2}")'
  # Full container restart (ask user first):
  docker compose restart api
  ```

  **Note:** If gunicorn config has `reload = True` (dev mode), it DOES auto-reload on Python file changes. But config changes (workers, threads) still require a full restart.

  Detect gunicorn vs runserver with: `docker compose exec api ps aux | grep -E 'gunicorn|runserver|manage.py'`

- **Django `ValidationError` ≠ Python `ValueError`**: When catching UUID primary key lookup failures on models with `pk=some_string`, Django raises `django.core.exceptions.ValidationError` — NOT Python's built-in `ValueError`. A `try/except ValueError` block will NOT catch it, and the unhandled exception becomes a 500.\n\n  ```python\n  # ❌ WRONG — ValueError doesn't catch Django ValidationError\n  try:\n      obj = MyModel.objects.get(pk=some_string)\n  except (MyModel.DoesNotExist, ValueError):  # ValidationError leaks → 500\n      pass\n\n  # ✅ CORRECT — catch DoesNotExist + broad Exception, or import ValidationError\n  from django.core.exceptions import ValidationError\n  try:\n      obj = MyModel.objects.get(pk=some_string)\n  except (MyModel.DoesNotExist, ValidationError):\n      pass\n  ```\n\n  This is especially common with WBP/SDP lookups where the frontend sends an SDP internal ID (e.g. `\"142202209080008\"`) as a ForeignKey value, and the backend tries `objects.get(pk=...)` on a UUID-pk model. Even if a fallback lookup by `nomor_induk_sdp` or `nomor_registrasi` exists below, the uncaught `ValidationError` kills the request before reaching it.\n\n- **Container restart requires user approval**: Before running `docker compose restart` or `docker compose up -d --force-recreate`, always ask the user for explicit permission. The user manages container lifecycle and may have running operations, pending migrations, or data loss concerns. The system's BLOCKED guard is a safety net, not a substitute for asking first. This is a firm rule for this project.

- **Vite proxy DNS (`getaddrinfo ENOTFOUND api`)**: When Vite cannot resolve the Django hostname, login returns 500 (not 401 or 403). Root cause: the `api` container is not on the same Docker network as `app`. Fix: `docker network connect <frontend_network> <api_container>`. Verify with `docker inspect api --format='{{json .NetworkSettings.Networks}}'`.
- **Docker network changes require recreating containers**: Updating `networks:` in docker-compose.yaml and running `docker-compose up -d` only restarts containers — it does NOT apply new network connections to existing containers. The change only takes effect after `docker-compose up -d --force-recreate <service>` or `docker network connect`. Without this, Vite ends up on `frontend` and Django only on `backend`, causing silent 500 errors through the proxy chain.
- **Raw `fetch()` bypasses axios interceptors — no Auth header**: When frontend code uses the browser's native `fetch()` instead of `axios`, the request does NOT go through axios interceptors. This means the `Authorization: Bearer *** header is NOT attached, and the backend receives an unauthenticated request. With `IsAuthenticated` as the default DRF permission, the request silently returns 401/403. **Always use `axios.get/post()` for API calls** in projects with axios interceptors. If `fetch()` is unavoidable, manually read the token from localStorage:

  ```javascript
  // ❌ No auth header — will 401 on IsAuthenticated endpoints
  fetch('/api/users/')

  // ✅ Manual auth header
  fetch('/api/users/', {
      headers: { 'Authorization': 'Bearer ' + localStorage.getItem('token') }
  })

  // ✅ Best — use axios, interceptors handle auth automatically
  axios.get('/users/')
  ```

  This also applies to `fetch()` inside `Promise.all()` or any other composition pattern. Check for straggler `fetch()` calls when API responses are mysteriously empty or dropdowns stay blank.

- **Vite SCSS hang**: The Vite dev server can hang during SCSS compilation when proxying many paths. If this happens, the architecture is still ready — debug Vite/SCSS separately (check memory, problematic SCSS imports, or stale node_modules). The infra config (Nginx, Docker networks, Django views) is unaffected.
- **Cookie domains**: When proxying through Django, always clear `domain=None` on `set_cookie` — the browser sets the cookie domain to the current page's origin automatically. Setting the external service's domain would make cookies invisible to the browser.
- **Content-Length**: Don't set `Content-Length` manually when rewriting HTML — the new content may have different length. Let Django calculate it from `HttpResponse`.
- **CORS**: The HTML proxy preserves same-origin, so no CORS headers needed. If the proxied service includes `<script>` or `<link>` with absolute URLs, those must also be in the rewrite list.
- **Requests streaming**: The full HTML proxy pattern buffers the full response. For very large HTML pages (>10MB), consider streaming with `requests.get(stream=True)` and `StreamingHttpResponse`.
- **Redirect-based auth flow — don't let `requests` consume session cookies**: When the proxied external service uses a POST login form → 302 redirect → GET authenticated page pattern, setting `allow_redirects=True` makes the `requests` library follow the redirect internally. The session cookie (`Set-Cookie`) from the POST response is consumed internally by `requests` and never reaches the browser. The authenticated page's response arrives at the browser, but without the session cookie, the next request from the browser (form submit, page load) won't authenticate.
  
  **Additionally**, if the external service's redirect Location is an absolute URL using its **domain name** (e.g., `http://sdp.rutanjakpus.id/sdp/biometric/home`), and the Docker container can't resolve that domain, `allow_redirects=True` causes a `requests.ConnectionError` (`Failed to resolve '...'`) → **502 Bad Gateway** from the proxy. Never rely on the container resolving the external domain; always use `allow_redirects=False` with manual Location rewriting.
  
  **Fix:** Always use `allow_redirects=False` in the HTML proxy. Handle redirects manually by returning the 302 to the browser with:
  - The `Location` header rewritten to keep the client on the proxy path
  - All `Set-Cookie` headers forwarded so the browser stores the session
  - Include BOTH the external base URL (`http://172.27.225.9/sdp/...`) AND the domain name (`http://sdp.rutanjakpus.id/sdp/...`) in the Location rewrite list — the server may redirect to either one
  
  The browser then naturally follows the redirect chain with proper cookie management. See the Reference View Structure above for the implementation pattern.
- **FILE_UPLOAD_DIR vs MEDIA_ROOT**: Keep these separate. `MEDIA_ROOT` is for Django-managed files (ImageField, FileField). `FILE_UPLOAD_DIR` is for user-uploaded documents processed outside the ORM. Mixing them can cause `collectstatic` or view conflicts.
- **UploadService URL construction**: When the frontend constructs file URLs using `window.location.origin`, ensure the backend path matches. The frontend `UploadService.js` may use hardcoded `/files/` prefix that bypasses axios baseURL — make sure Vite or Nginx proxies `/files/` to Django, not the old microservice.
- **Flask `<path:subpath>` eats the filename**: When the route is `files/upload/<path:subpath>`, Flask captures EVERYTHING after `/upload/` into `subpath` — including the intended filename. If you `POST` to `/files/upload/identitas/foto/abc.jpg`, then `subpath='identitas/foto/abc.jpg'`. Combined with `secure_filename(filename)` which strips path separators, the file ends up at `uploads/identitas/foto/abc.jpg/abc.jpg` (a file named `abc.jpg` inside a directory named `abc.jpg`).
  
  **Fix:** Always pass the directory path as the URL subpath and the filename as a separate form field:
  ```python
  # ✅ Correct — subpath is directory only, filename is form data
  subpath = 'identitas/foto'               # URL: /files/upload/identitas/foto
  filename = 'abc.jpg'                      # form field
  # → saves to: uploads/identitas/foto/abc.jpg
  
  # ❌ Wrong — filename embedded in subpath
  subpath = 'identitas/foto/abc.jpg'        # URL: /files/upload/identitas/foto/abc.jpg
  # → Flask captures 'identitas/foto/abc.jpg' as subpath
  # → secure_filename('abc.jpg') + os.path.join(uploads, subpath)
  # → saves to: uploads/identitas/foto/abc.jpg/abc.jpg  (dead end!)
  ```
  The file-service in Smart Services project uses this exact pattern — `@app.route('/files/upload/<path:subpath>')` expects `subpath` as the directory and reads `filename` from `request.form.get('filename', file.filename)`.
- **`cookie.max_age` absent on `http.cookiejar.Cookie`**: The `requests` library's `Response.cookies` iterates `http.cookiejar.Cookie` objects. These DO have `.name`, `.value`, `.domain`, `.path`, `.secure`, `.expires`, but do NOT have `.max_age`, `.httponly`, or `.samesite` as direct attributes. Always use `cookie.get_nonstandard_attr('httponly', False)` and pass `max_age=None` (which means session-length by default in Django). Supplying `.max_age` from a non-existent attribute causes `AttributeError` → Django 500.
- **`CONTENT_TYPE` vs `HTTP_CONTENT_TYPE` in Django WSGI**: When forwarding `Content-Type` headers from an incoming Django request to an upstream `requests` call, use `request.META.get('CONTENT_TYPE')` — NOT `request.META.get('HTTP_CONTENT_TYPE')`. Per the WSGI spec, CGI-like headers are stored in `environ` without the `HTTP_` prefix. Specifically: `CONTENT_TYPE` and `CONTENT_LENGTH` are stored as-is (no `HTTP_` prefix), while standard headers like `User-Agent` become `HTTP_USER_AGENT`. Using `HTTP_CONTENT_TYPE` silently returns `None` → the upstream request has no Content-Type → the external server cannot parse form data → login fails silently → browser gets empty response or error. This is a VERY common Django proxy bug that's hard to debug because no error is raised — the upstream just ignores the body. Always verify with `docker compose exec api python -c "import requests; print(requests.post('...', headers={...}, data=form_data).status_code)"` from inside the container to isolate.
- **"Confirm Form Resubmission" for proxied login forms**: When a full HTML proxy forwards an external service's login page that uses a POST `<form action=...>`, the browser may show "Confirm Form Resubmission" / `ERR_CACHE_MISS` if the user navigates to the form `action` URL directly via GET or refreshes the page. This is **not a proxy bug** — it's the browser warning that the POST endpoint cannot be replayed via GET. The proxy should always serve the login page at the canonical URL (e.g. `/sdp-proxy/biometrickunjungan/home`), not at the form action target. The form action URL is only meant to be reached via POST submission.
- **External login page returned as 200 (not redirect)**: Some external services (e.g. SDP biometric gateway) return the login form as a **200 response** when no session exists, rather than a 302 redirect to a separate `/login` URL. This is normal — the proxy's `allow_redirects=True` is irrelevant here. The iframe loads this 200 response and renders the login form directly. If the iframe appears blank, check for JavaScript errors or URL rewriting issues in the proxy, not redirect handling.
- **`Refresh` header redirect (meta-refresh) bypasses proxy**: Legacy apps sometimes use the HTTP `Refresh` header (`Refresh: 0;url=http://external.service/sdp/...`) instead of a 302 redirect. Since `requests` doesn't process `Refresh` headers, a naive proxy lets the browser follow it directly to the external server — bypassing the proxy and losing session cookies. **Detect with `resp.headers.get('Refresh')`** and rewrite the URL inside it the same way you rewrite `Location`. See "Meta-Refresh (Refresh Header) Redirects" section above.
- **App registration**: A minimal Django app with only `views.py` and `urls.py` (no models) still needs to be added to `INSTALLED_APPS`. Without it, template tag resolution, signal discovery, and some migration commands may silently fail.
- **Signal dead ends**: After removing an internal service, scan for `pre_delete` / `post_save` signal handlers that call the old service's HTTP endpoint. Replace with the shared utility import pattern above.
- **Nullable FK access without guard → 500**: Accessing `obj.fk.nested_attr` crashes with `AttributeError` when the FK field is null — whether or not `select_related` is used. Django returns 500 with no user-visible traceback (Django catches the exception internally). Always check `if obj.fk:` before accessing chained attributes. See `references/django-orm-null-handling.md` for the full guard pattern and detection tips.
- **Docker containers can't resolve internal hostnames**: When Django containers need to reach an internal service (e.g., `sdp.rutanjakpus.id`) and DNS resolution fails with `[Errno -2] Name or service not known`, there are TWO approaches:
  
  **Approach A — Direct IP (for backend-to-backend API calls):** Use the server's IP address with a `Host` header. Suitable for API clients (`sdp_client.py`), proxy modules that need to forward requests verbatim.
  ```python
  url = 'http://sdp.rutanjakpus.id/path'.replace('sdp.rutanjakpus.id', '172.27.225.9')
  requests.get(url, headers={'Host': 'sdp.rutanjakpus.id'})
  ```
  
  **Approach B — Internal Proxy Path (for media downloads):** When downloading SDP photos/media, **DO NOT use direct IP**. Instead, extract the path from the SDP URL and download via the Django internal proxy at `http://localhost:5005/sdp-proxy/{path}`. This keeps traffic within the gateway architecture and avoids exposing internal IPs in download logic.
  ```python
  # Extract path: "http://sdp.rutanjakpus.id/sdp/upload/2025/10/xxx.jpg" → "upload/2025/10/xxx.jpg"
  path = re.match(r'^.*sdp\.rutanjakpus\.id/sdp/(.*?)(\?.*)?$', url).group(1)
  # Use timeout=60 to match the proxy_full view's own 60s timeout
  resp = requests.get(f'http://localhost:5005/sdp-proxy/{path}', timeout=60)
  ```
  This works because `proxy_full` (in `sdp_proxy/views.py`) is `@csrf_exempt` (no auth required) and handles the actual SDP connection with proper Host headers.
  
  See `references/docker-dns-workaround.md` for the full comparison and `references/smart-services-sdp-proxy.md` for the proxy download implementation.

- **CRLF line endings break shell scripts in Docker Linux**: Git on Windows (with `core.autocrlf=true`) converts LF → CRLF on checkout. Shell scripts (`.sh`) with CRLF fail mysteriously in Linux containers: `: not found`, `Unknown command: 'migrate\r'`. Fix with `.gitattributes` (`*.sh text eol=lf`) for prevention + `sed -i 's/\\r$//'` for immediate repair. Also: django-environ (`.env` files) is strict about `=` syntax — spaces around `=` (`KEY = value`) cause "Invalid line" warnings and silently missing env vars. See `references/docker-windows-cross-platform.md`.
