---
name: react-axios-token-refresh
description: "Implementasi JWT token refresh di React (axios interceptors) + Django (SimpleJWT). Mencakup pola doRefresh, error handling, retry logic, dan pitfall umum."
tags: [react, axios, jwt, django, simplejwt, token-refresh, interceptor, auth]
platforms: [windows, linux, macos]
related_skills: [django-gateway-architecture]
---

# React Axios JWT Token Refresh Pattern

Pola implementasi automatic token refresh menggunakan axios interceptors di React + Django REST Framework dengan SimpleJWT. Mencakup 4 bug umum yang sering muncul dan cara memperbaikinya.

## Arsitektur

```
Browser → Vite :3000 → proxy /api → Django :5005
                                    ├── /api/auth/login/       (dapat access + refresh)
                                    ├── /api/auth/token/refresh/ (refresh access token)
                                    └── /api/*                  (endpoint lain, butuh auth)
```

## Frontend: `index.js` — doRefresh + Interceptor

### Struktur Lengkap

```javascript
// === Config Axios ===
axios.defaults.baseURL = '/api'; // VITE_API_URL=/api

// Set initial token
const initToken = localStorage.getItem('token');
if (initToken) {
  axios.defaults.headers.common['Authorization'] = 'Bearer ' + initToken;
}

// === Request Interceptor ===
axios.interceptors.request.use(config => {
  if (config.url === '/auth/token/refresh/') return config; // skip auth header
  const token = localStorage.getItem('token');
  if (token) config.headers['Authorization'] = 'Bearer ' + token;
  return config;
});

// === doRefresh (native fetch + retry) ===
async function doRefresh() {
  const refreshTokenVal = localStorage.getItem('refresh_token');
  if (!refreshTokenVal) throw new Error('No refresh token');

  const maxRetries = 1;
  let lastError;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      // native fetch() — bypass axios interceptor, fresh connection
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
      // Retry hanya untuk 5xx / network error
      if ((status >= 500 || !status) && attempt < maxRetries) {
        await new Promise(r => setTimeout(r, 500));
        continue;
      }
      throw e;
    }
  }
  throw lastError;
}

// === Response Interceptor ===
axios.interceptors.response.use(
  response => response,
  async error => {
    const originalRequest = error.config;
    if (error.response?.status !== 401 || originalRequest._retry) {
      return Promise.reject(error);
    }

    // Jangan retry refresh endpoint sendiri
    if (originalRequest.url === '/auth/token/refresh/') {
      localStorage.clear();
      window.location.href = '/login';
      return Promise.reject(error);
    }

    // Skip non-GET auth endpoints (login, register — 401 berarti invalid credentials)
    if (originalRequest.url?.startsWith('/auth/') && originalRequest.method !== 'GET') {
      return Promise.reject(error);
    }

    const refreshToken = localStorage.getItem('refresh_token');
    if (!refreshToken) {
      localStorage.clear();
      window.location.href = '/login';
      return Promise.reject(error);
    }

    originalRequest._retry = true;
    try {
      const newToken = await handleRefresh();  // pakai refreshCoordinator
      originalRequest.headers['Authorization'] = 'Bearer ' + newToken;
      return axios(originalRequest);
    } catch (refreshError) {
      // HANYA logout kalau beneran auth error (401/403)
      const status = refreshError.response?.status;
      if (status === 401 || status === 403) {
        localStorage.clear();
        window.location.href = '/login';
      }
      return Promise.reject(refreshError);
    }
  }
);
```

## Bug #1: `doInit()` — `refreshTokenApi()` tanpa `await`

**File:** `actions/auth.js`

```javascript
// ❌ SALAH — tidak ada await, result undefined
refreshTokenApi(refresh);
token = result.access; // ReferenceError!

// ✅ BENAR
const result = await refreshTokenApi(refresh);
token = result.access;
```

**Dampak:** Saat app reload dan access token expired, `doInit()` crash → user ke-logout padahal refresh token masih valid.

## Bug #2: Response Interceptor — Semua Error Langsung Logout

```javascript
// ❌ SALAH — network error / 5xx juga bikin logout
} catch (refreshError) {
  localStorage.clear();
  window.location.href = '/login';
}

// ✅ BENAR — cuma logout kalau 401/403 (auth error beneran)
} catch (refreshError) {
  const status = refreshError.response?.status;
  if (status === 401 || status === 403) {
    localStorage.clear();
    window.location.href = '/login';
  }
  return Promise.reject(refreshError);
}
```

**Dampak:** Network glitch atau backend 502 pas refresh → user ke-logout padahal token masih valid.

## Bug #3: `fetch()` Raw Tanpa Auth Header

```javascript
// ❌ SALAH — fetch() tidak kirim Authorization header
fetch('/api/users/').then(r => r.json())

// ✅ BENAR — axios otomatis attach token via interceptor
axios.get('/users/').then(r => r.data)
```

**Dampak:** Dropdown / list kosong karena API return 401/403. Terjadi di mana saja yang pakai raw `fetch()` ke endpoint butuh auth.

## Bug #4: 502 First Attempt — Connection Reuse Race

**Gejala:** Di local Docker, refresh attempt pertama selalu 502, attempt kedua sukses. Hanya terjadi saat akses via nginx (port 80), bukan Vite langsung (port 3000).

**Root Cause #1 (Nginx):** Nginx config `proxy_set_header Connection "upgrade"` diterapkan ke SEMUA request. Untuk API biasa (bukan WebSocket), header ini bikin koneksi keep-alive dalam state rusak setelah response 401 → request berikutnya kena 502.

**Fix #1:** Pakai conditional `$connection_upgrade` di nginx:
```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
server {
    location / {
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;  # upgrade utk WS, close utk API
    }
}
```
File: `nginx/conf.d/appseed-app.conf`. Lihat `references/nginx-connection-upgrade-fix.md`.

**Root Cause #2 (Vite proxy):** HTTP/1.1 connection reuse di Vite http-proxy. Request refresh nyamber koneksi yg belum fully closed dari request sebelumnya.

**Fix #2:** `doRefresh()` pakai **native `fetch()`** (bukan `axios.post()`) + **retry 1x** untuk 5xx:
```javascript
async function doRefresh() {
    const refreshTokenVal = localStorage.getItem('refresh_token');
    if (!refreshTokenVal) throw new Error('No refresh token');

    const maxRetries = 1;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
        try {
            // native fetch() — bypass axios interceptor, fresh connection
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
            // ... simpan token, return newToken
        } catch (e) {
            const status = e.response?.status;
            if ((status >= 500 || !status) && attempt < maxRetries) {
                await new Promise(r => setTimeout(r, 500));
                continue;
            }
            throw e;
        }
    }
}
```

**Kenapa native `fetch()`?** `axios.post()` buat refresh request akan lewat axios interceptor lagi → potensi recursive loop + connection reuse. `fetch()` bikin koneksi HTTP baru, isolated dari axios.

## Backend: Django `custom_token_refresh`

```python
@api_view(['POST'])
@permission_classes([])          # bypass IsAuthenticated default
@authentication_classes([])      # bypass GracefulJWTAuthentication
def custom_token_refresh(request):
    refresh_token_str = request.data.get('refresh')
    if not refresh_token_str:
        return Response({'error': 'Refresh token required'}, status=400)

    try:
        # Manual decode — bypass blacklist check (hindari race condition)
        payload = jwt.decode(
            refresh_token_str,
            jwt_settings.SIGNING_KEY,
            algorithms=[jwt_settings.ALGORITHM],
            options={'verify_exp': True},
        )

        if payload.get('token_type') != 'refresh':
            return Response({'error': 'Not a refresh token'}, status=401)

        # Pertahankan lifetime asli
        orig_exp = payload.get('exp', 0)
        orig_iat = payload.get('iat', 0)
        lifetime_seconds = max(orig_exp - orig_iat, 900)

        user = User.objects.get(id=payload.get('user_id'))

        # Blacklist token lama (ignore kalau sudah di-blacklist)
        try:
            refresh = RefreshToken(refresh_token_str)
            refresh.blacklist()
        except Exception:
            pass

        # Buat token baru dengan lifetime sama
        new_refresh = RefreshToken.for_user(user)
        new_refresh.set_exp(lifetime=timedelta(seconds=lifetime_seconds))

        return Response({
            'access': str(new_refresh.access_token),
            'refresh': str(new_refresh),
        })
    except jwt.ExpiredSignatureError:
        return Response({'error': 'Token is expired'}, status=401)
    except Exception as e:
        return Response({'error': str(e)}, status=401)
```

**Catatan penting view ini:**
- `@permission_classes([])` + `@authentication_classes([])` **wajib** — kalau tidak, `DEFAULT_PERMISSION_CLASSES = IsAuthenticated` akan ngeblok (token expired → 401, bukan refresh)
- Manual `jwt.decode` bypass `check_blacklist()` — mencegah error "token already blacklisted" saat concurrent refresh

## Django Settings (SIMPLE_JWT)

```python
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=5),
    "REFRESH_TOKEN_LIFETIME": timedelta(minutes=15),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}
```

## `refreshCoordinator.js` — Mencegah Concurrent Refresh

```javascript
let isRefreshing = false;
let failedQueue = [];

export function beginRefresh() {
  if (isRefreshing) return queueRequest(); // antri
  isRefreshing = true;
  return null; // lanjut refresh
}

export function finishRefresh(token) {
  processQueue(null, token); // resolve semua yg ngantri
  isRefreshing = false;
}

export function abortRefresh(error) {
  processQueue(error); // reject semua yg ngantri
  isRefreshing = false;
}
```

**Fungsi:** Kalau 3 request kena 401 bersamaan, cuma 1 yg beneran manggil refresh endpoint. 2 lainnya ngantri — dapat token baru dari `finishRefresh()`.

## Checklist Implementasi

- [ ] `doRefresh()` punya jeda 100ms sebelum attempt pertama
- [ ] Retry hanya untuk 5xx / network error (bukan 4xx)
- [ ] Response interceptor: logout HANYA pada 401/403
- [ ] `doInit()`: `const result = await refreshTokenApi(refresh)`
- [ ] Semua API call pakai axios (bukan raw `fetch`)
- [ ] Backend view pakai `@permission_classes([])` + `@authentication_classes([])`
- [ ] Backend pakai manual `jwt.decode` (bypass blacklist check)
- [ ] `refreshCoordinator` mencegah concurrent refresh
