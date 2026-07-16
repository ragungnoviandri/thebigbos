# Nginx `Connection: upgrade` 502 Fix

## Symptom
Token refresh fails with 502 on first attempt via nginx, but succeeds on retry (500ms later). Only when accessing via nginx port 80, not Vite directly on port 3000.

## Root Cause
Nginx `proxy_set_header Connection "upgrade"` was applied universally, not just for WebSocket/HMR. When a normal HTTP request (like token refresh) gets `Connection: upgrade`, the keep-alive connection enters a broken state after a 401 response. The next request on that connection gets 502.

## Fix
Use conditional `$connection_upgrade` in nginx config:

```nginx
# Add in http block (before server block)
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    
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

        proxy_read_timeout 120s;
        proxy_connect_timeout 5s;
    }
}
```

## How It Works
- WebSocket/HMR requests (with `Upgrade: websocket` header) → `Connection: upgrade`
- Normal HTTP requests (no Upgrade header) → `Connection: close`
- Connection is properly closed after each HTTP response, preventing stale connection reuse.

## File
`nginx/conf.d/appseed-app.conf`

## Date
2026-06-26 — Smart Services project (Rutan Jakarta Pusat)
