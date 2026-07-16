# Debugging Vite → Django Proxy Chain (Single Gateway Architecture)

## Quick Checklist

When the app returns 500 errors through `localhost` but endpoints work when hit directly via `docker exec`:

### 1. Is Vite resolving Django's hostname?

```bash
# Check Vite logs for DNS errors
docker logs app 2>&1 | grep -i 'proxy error\|ENOTFOUND\|EAI_AGAIN'

# If you see messages like:
#   http proxy error: /api/auth/login/
#   Error: getaddrinfo ENOTFOUND api
# → Network connectivity issue between app ↔ api containers
```

### 2. Are containers on the right Docker networks?

```bash
docker inspect api --format='{{json .NetworkSettings.Networks}}'
docker inspect app --format='{{json .NetworkSettings.Networks}}'
```

Expected:
| Container | Must be on |
|-----------|-----------|
| `api` (Django) | `backend` **+** `frontend` |
| `app` (Vite) | `frontend` |
| `nginx` | `frontend` |

If `api` is missing `frontend`, fix:
```bash
docker network connect smartservices_frontend api
```

> **Note:** `docker-compose up -d` does NOT re-apply network changes to existing containers. You must either use `--force-recreate` or `docker network connect`.

### 3. Does Vite have a proxy rule for the failing path?

Relevant paths that must be in `vite.config.js` proxy table:
- `/api` — DRF endpoints
- `/files` — file upload/download
- `/django-admin` — admin interface
- `/static` — Django admin CSS/JS
- `/media` — media files
- `/sdp-proxy` — legacy app proxy

Missing `/static/` is the most common oversight — causes 404 for admin static resources while the admin HTML page loads fine.

### 4. Is Django serving static files?

If using gunicorn + `DEBUG=False`, Django does NOT serve static files. Check:

```bash
# Settings
docker exec api grep -A2 'DEBUG\|STATIC_URL\|STATIC_ROOT\|MIDDLEWARE' /app/core/settings.py

# Whitenoise middleware must be present:
#   'django.middleware.security.SecurityMiddleware',
#   'whitenoise.middleware.WhiteNoiseMiddleware',   <-- required
```

If missing, add to `MIDDLEWARE` in settings.py and restart the api container:
```bash
docker restart api
```

## Symptoms → Root Cause

| Symptom | Likely Cause |
|---------|-------------|
| Login → 500 (full chain), 401 (direct to Django) | Vite can't resolve `api` hostname (Docker network) |
| Login → 500 (full chain), 500 (direct to Django) | Django process crashed or import error |
| Admin loads HTML but CSS/JS 404 | Missing `/static` in Vite proxy, or missing whitenoise middleware |
| Login → 200 (direct), 500 (through Nginx) | Vite proxy misrouting the path |
| Admin page → 302 redirect to login (correct) | Django auth is working, just static files need fixing |
