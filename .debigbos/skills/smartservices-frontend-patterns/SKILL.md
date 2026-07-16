---
name: smartservices-frontend-patterns
description: React frontend patterns for Smart Services — auth interceptors, axios vs fetch, menu-permission UI, and token refresh debugging.
triggers:
  - debugging auth/logout issues in ss_app
  - working with API calls in React frontend
  - identitas form or user dropdown
  - token refresh / JWT interceptor issues
  - menu permission-based UI rendering
  - usePermission hook usage
---

# Smart Services Frontend Patterns

React/Vite frontend (`ss_app/`) patterns and anti-patterns for the Smart Services project.

## 1. Golden Rule: Always Axios, Never Fetch

**NEVER use raw `fetch()` for API calls.** Raw `fetch()` bypasses the axios interceptor chain — no `Authorization` header is attached. All Django endpoints require `IsAuthenticated` (default), so `fetch()` calls silently return 401.

```javascript
// ❌ BAD — no auth header, always 401
fetch('/api/users/').then(r => r.json())

// ✅ GOOD — interceptor attaches Bearer token
axios.get('/users/').then(res => res.data)
```

Axios `baseURL` is set to `config.baseURLApi` (e.g., `http://localhost:8080/api`), so paths are relative to `/api/`.

### Audit command
```bash
cd ss_app/src && grep -rn "fetch('/api/" --include="*.js" --include="*.jsx"
```
Replace every hit with `axios.get/post/put/delete`.

## 2. Token Refresh Interceptor

Located in `ss_app/src/index.js`. Two patterns converged here:

### 2a. Queue coordinator (`refreshCoordinator.js`)
Prevents thundering herd when multiple 401s arrive simultaneously. First caller starts refresh; others queue and get the result.

### 2b. Response interceptor (index.js lines 91-142)
- Catches 401 → attempts token refresh via POST `/auth/token/refresh/`
- On success → retries original request with new token
- On failure → only logout if refresh endpoint returned 401/403 (real auth error)
- Network errors during refresh → KEEP session, reject the promise

### 2c. Common bugs
1. **Missing `await`**: `refreshTokenApi(refresh)` without `const result = await` → `ReferenceError: result is not defined`
2. **Logout on network errors**: Old code cleared localStorage on ANY refresh failure. Fixed to only logout on 401/403 status.
3. **`doRefresh()` uses native `fetch()`**: Since 2026-06-25, `doRefresh()` calls `fetch('/api/auth/token/refresh/', {method:'POST', ...})` instead of `axios.post()`. This prevents the axios interceptor from recursing on the refresh call and avoids connection reuse issues with Vite proxy.

### 2d. Nginx `Connection: upgrade` causes 502 on token refresh
**Symptom:** Token refresh always fails with 502 on first attempt, succeeds on retry (500ms later). Only happens when accessing via nginx (port 80), not Vite directly (port 3000).

**Root cause:** Nginx config had `proxy_set_header Connection "upgrade"` applied to ALL requests (for HMR WebSocket support). Normal HTTP API requests with `Connection: upgrade` behave unpredictably — after a 401 response, the keep-alive connection is in a broken state → next request gets 502.

**Fix:** Use conditional `$connection_upgrade`:
```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    location / {
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
}
```
File: `nginx/conf.d/appseed-app.conf`. See `references/nginx-connection-upgrade-fix.md` for the full fix.

## 3. Menu Permission UI Pattern (`usePermission`)

Hook: `usePermission(menuUrl)` reads from `localStorage.getItem('menus')` and returns CRUD flags `{ bisa_lihat, bisa_buat, bisa_ubah, bisa_hapus }`.

### Pattern: Editable vs read-only fields

```javascript
// In page component
import { usePermission } from '../../../utils/usePermission';

const perm = usePermission('/admin/identitas');
const canChangeUser = isAdmin && perm.bisa_ubah;

// In form component — pass canChangeUser as prop
{canChangeUser ? (
  <SelectFormItem name="user_id" options={userOptions} />
) : (
  <div className="form-control bg-light text-muted" style={{ border: '1px dashed #ccc' }}>
    {currentUserLabel || '(tidak diketahui)'}
  </div>
)}
```

### Key: `handleSubmit` must also respect `canChangeUser`
```javascript
if (!canChangeUser) {
  data.user_id = currentUserId;  // force to current user
} else if (!data.user_id) {
  data.user_id = null;
}
```

## 4. Menu URLs Reference

Common `usePermission` URLs used across the app:

| Menu | URL |
|------|-----|
| Identitas | `/admin/identitas` |
| Users | `/admin/users` |
| Roles | `/admin/roles` |
| Menus | `/admin/menus` |
| WBP | `/admin/wbp` |
| Pegawai | `/admin/pegawai` |
| Reference | `/admin/reference` |
| Kunjungan | `/app/kunjungan` |
| Pengunjung | `/app/kunjungan/pengunjung` |
| Role-Users | `/admin/role-users` |
| Role-Menus | `/admin/role-menus` |

## 5. Auth Files Map

| File | Purpose |
|------|---------|
| `index.js` | Axios interceptors (request + response), `doRefresh()`, `handleRefresh()` |
| `services/authService.js` | API calls: login, logout, refreshToken, fetchCurrentUser |
| `services/refreshCoordinator.js` | Queue for concurrent token refreshes |
| `actions/auth.js` | Redux actions: doInit, loginUser, logoutUser, receiveToken |
| `config.js` | baseURL, isBackend flag, mock credentials |

## 6. FileUploader Custom Filename

`FileUploader.upload()` now accepts a 4th parameter for custom filename:

```javascript
// Before: always generates UUID filename → path/{uuid}.ext
FileUploader.upload('folder', file, {})

// After: optional custom filename → path/{name}.webp
FileUploader.upload('karutan', webpFile, {}, `${slug}.webp`)
```

Location: `ss_app/src/components/FormItems/uploaders/UploadService.js`

Backward compatible — existing calls without the 4th parameter still get UUID filenames.

## 7. Pendaftaran Admin Workflow (SDP Search + Two-Step Approval)

### Search from SDP (not local DB)
```javascript
import { searchPerkara } from 'services/sdpService';

// Auto-detect numeric vs text input
const isNumeric = /^\d+$/.test(q.trim());
const params = isNumeric ? { nmrReg: q.trim() } : { nama: q.trim() };
const data = await searchPerkara(params);
```

### Two-Step Approval (Select → Confirm → Save)
```javascript
const [selectedWbp, setSelectedWbp] = useState(null);

// Step 1: Click "Pilih" → preview WBP in Widget
// Step 2: Click "Setujui" → save with status=Disetujui + warga_binaan
// "Setujui" button disabled until selectedWbp is set
```

### Reject with Reason Modal
```javascript
const [rejectModal, setRejectModal] = useState(false);
const [rejectAlasan, setRejectAlasan] = useState('');

const handleReject = async () => {
    await updatePendaftaran(id, { status: 'Ditolak', catatan: rejectAlasan });
};
```

### WBP History Widget
Fetches `GET /pendaftaran/wbp/history/` — returns WBPs previously visited by the logged-in user (via Pengunjung NIK lookup). Shows as selectable cards above the search box.

### SDP-Media Proxy
Photos from SDP (e.g., `upload/20xxx/photo.jpg`) must be proxied through Django:
- Backend: `proxy_media` view in `sdp_proxy/views.py` → streams binary from SDP
- URL: `/api/sdp-media/<path>`
- Frontend: `getFotoUrl(foto)` strips SDP prefixes → `/api/sdp-media/{clean}`

### SDP ID ≠ Local WBP UUID — Use `nomor_induk_sdp` for Matching

When linking WBP from SDP search, the SDP `id` field is the **NOMOR INDUK SDP** (e.g. `"142202209080008"` — 15-digit string), NOT a local UUID. The local `WargaBinaan` model stores this in `nomor_induk_sdp` (CharField).

Backend `admin_detail` PUT handler should try THREE lookup levels:

```python
wb = None
# 1) Try by local UUID
try:
    wb = WargaBinaan.objects.get(pk=wb_id)
except (WargaBinaan.DoesNotExist, ValidationError):  # ← ValidationError, not ValueError!
    pass
# 2) Try by nomor_induk_sdp (SDP internal ID)
if not wb:
    wb = WargaBinaan.objects.filter(nomor_induk_sdp=wb_id).first()
# 3) Try by nomor_registrasi (formal format like "AIII. 0687/P/2025")
if not wb and nomor_reg:
    wb = WargaBinaan.objects.filter(nomor_registrasi=nomor_reg).first()
```

**Critical pitfall:** Django `ValidationError` ≠ Python `ValueError`. UUID foreign key fields raise `django.core.exceptions.ValidationError` when the value isn't a valid UUID. Catching only `ValueError` silently leaks the exception → 500. Import from `django.core.exceptions` or use a broad `except Exception`.

### Auto-Import WBP from SDP (when not found locally)

When the WBP doesn't exist in local DB, auto-import it through the wargabinaan API:
```python
sdp_data = data.get('sdp_data') or {}  # full SDP row from frontend
if sdp_data:
    resp = requests.post('http://localhost:5005/api/wargabinaan/', json=sdp_data, ...)
    if resp.status_code in (200, 201):
        new_wb_id = resp.json().get('id')
        wb = WargaBinaan.objects.get(pk=new_wb_id)
```

**Frontend must include full SDP row** in the PUT request:
```javascript
// Keep full SDP data when mapping search results
const mapped = results.map(w => ({
    ...w,           // all SDP fields
    _sdp: w,        // explicit reference for auto-import
}));

// Send sdp_data in the approve request
updatePendaftaran(id, {
    status: 'Disetujui',
    warga_binaan: selectedWbp.id,
    nomor_registrasi: selectedWbp.nomor_registrasi,
    sdp_data: selectedWbp._sdp,   // full SDP row
});
```

This uses the same `_create_wbp` endpoint as `/admin/wbp` — single entry point for WBP import.

## 8. Docker Windows: Gunicorn `reload=True` Tidak Reliable

**Gejala:** Edit file Python di host, file terlihat di container (`docker exec api cat ...`), tapi kode lama masih jalan. Error lama terus muncul meski sudah di-fix.

**Penyebab:** `reload=True` pakai filesystem polling/watcher. Di Docker dengan Windows volume mount (`- .\\\\ss_api:/app`), perubahan file sering tidak terdeteksi.

**Fix bertahap:**
1. `docker kill api && docker start api` — restart container (biasanya cukup)
2. `docker compose up -d --build api` — rebuild image (paling reliable)
3. Verifikasi: `docker exec api cat //app/.../views.py | grep "kata_kunci_baru"`

**JANGAN** percaya `reload=True` di Docker Windows. Selalu verifikasi kode baru sudah dipakai.

- `references/token-refresh-debugging.md` — Full session notes from 2026-06-25: root cause analysis, before/after diffs, token lifecycle diagram, and backend refresh endpoint details.
- `references/pendaftaran-admin-workflow.md` — Complete pendaftaran admin flow: SDP search, two-step approval, reject modal, WBP history widget, layout structure.
- `references/sdp-api-id-mapping.md` — SDP PHP API fix: `id` field mapped to `ID_PERKARA` instead of `NOMOR_INDUK`. Deploy instructions and database field relationships.

## 9. Skill Overlap Note

`fullstack-jwt-auth` covers the same JWT token refresh territory. This skill (`smartservices-frontend-patterns`) is the canonical reference for Smart Services project patterns. `fullstack-jwt-auth` should eventually be absorbed into this one.
