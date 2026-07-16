# Debugging 502 Bad Gateway — Docker/Vite/Nginx Stack

## Symptom
Token refresh (`POST /api/auth/token/refresh/`) returns **502 Bad Gateway** on the FIRST attempt. The 500ms retry always succeeds. This happens consistently on certain pages (e.g., jurnal harian save) but not on others.

Console output pattern:
```
/api/dashboard/jurnal/:1  401 (Unauthorized)
[Axios] 401 received, attempting token refresh
refresh/:1  502 (Bad Gateway)
[Axios] Refresh attempt 1 failed (502), retrying in 500ms...
[Token] Access: ✅ 4m59s lagi (access)    ← retry success
```

## Debugging Journey (False Leads → Root Cause)

### False Lead 1: Axios Connection Reuse
**Hypothesis:** The axios instance shares TCP connection state between the failed 401 request and the refresh request.

**Tested:** Replaced `axios.post('/auth/token/refresh/')` with native `fetch()` → Still got 502.

**Verdict:** ❌ Not the cause.

### False Lead 2: Vite Proxy Keep-Alive
**Hypothesis:** Vite's http-proxy reuses keep-alive connections that are half-closed on the Django/gunicorn side.

**Tested:** Added `agent: false` to Vite proxy config → Still got 502.

**Key clue missed:** User was accessing via `http://localhost` (port 80, nginx), NOT `http://localhost:3000` (Vite directly). The Vite proxy fix didn't apply because requests went through nginx first.

**Verdict:** ❌ Not the cause (wrong proxy layer).

### False Lead 3: Backend Code Issue
**Hypothesis:** Django `custom_token_refresh` view has unhandled exceptions or database issues.

**Tested:** Reviewed view code — all exceptions properly caught. Database operations wrapped in try/except. JWT settings correct. Other API endpoints work fine.

**Verdict:** ❌ Not the cause.

### ✅ Root Cause: Nginx `Connection: upgrade` on All Requests
**File:** `nginx/conf.d/appseed-app.conf`

The nginx config had:
```nginx
location / {
    proxy_pass http://app_frontend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";   # ← SET FOR ALL REQUESTS!
}
```

`Connection: upgrade` tells the upstream (Vite) that every connection might upgrade to WebSocket. After a 401 response on a normal HTTP request, the connection enters a confused state. When the next request (token refresh) reuses the same keep-alive connection, nginx gets a dead/broken upstream socket → **502 Bad Gateway**.

**Why retry succeeds:** The 500ms delay allows the broken connection to fully timeout/close. The retry opens a fresh connection.

**Why only certain pages:** Larger POST/PUT requests (like jurnal save) keep the connection open longer, making the timing window for the race condition wider.

## The Fix

Replace hardcoded `Connection "upgrade"` with a conditional mapping:

```nginx
# In http block (or before server block):
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    ...

    location / {
        proxy_pass http://app_frontend;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket/HMR — only upgrade when client requests it
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;

        keepalive_timeout 65s;
        keepalive_requests 100;
    }
}
```

| Client Request | `$http_upgrade` | `$connection_upgrade` | Result |
|---|---|---|---|
| Normal HTTP | `""` (empty) | `close` | Connection closes cleanly after response |
| WebSocket/HMR | `"websocket"` | `upgrade` | Connection upgrades for WS |

Restart: `docker compose restart nginx`

## Key Lesson: Verify which proxy layer is actually handling requests

Always check the browser's Network tab or console to see the actual URL being hit. If it's `http://localhost/api/...` (no port), requests go through **nginx** (port 80), NOT Vite directly (port 3000). Fixing Vite config won't help — you need to fix nginx.
