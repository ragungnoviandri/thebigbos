# SmartServices — Common Pitfalls & Patterns

Project-specific gotchas encountered during development sessions.

## Django REST Framework

### `ValidationError` ≠ `ValueError` on UUID lookups

```python
# ❌ WRONG — ValidationError is NOT a subclass of ValueError
try:
    obj = Model.objects.get(pk=non_uuid_string)
except (Model.DoesNotExist, ValueError):
    pass  # ValidationError still propagates → 500!

# ✅ CORRECT
from django.core.exceptions import ValidationError
try:
    obj = Model.objects.get(pk=non_uuid_string)
except (Model.DoesNotExist, ValueError, ValidationError):
    pass
```

Django's `UUIDField.to_python()` raises `django.core.exceptions.ValidationError` when the value isn't a valid UUID. This is a separate exception hierarchy from Python's built-in `ValueError`.

### Multi-step WBP lookup pattern

When linking a WBP from SDP search results to a pendaftaran:
1. Try local UUID (`WargaBinaan.objects.get(pk=...)`)
2. Try `nomor_induk_sdp` (CharField, stores SDP's `ID_PERKARA`)
3. Try `nomor_registrasi` (formal format like "AIII. 0687/P/2025")
4. Auto-import: POST full SDP data to `/api/wargabinaan/` (same endpoint used by WBP admin page)

### Serializer helper fields

Always `pop()` non-model fields from request data before passing to serializer:
```python
data.pop('nomor_registrasi', None)
data.pop('sdp_data', None)
serializer = MySerializer(instance, data=data, partial=True)
```

### API Security: Conditional Data Exposure

When an endpoint serves public data but some fields are sensitive:
```python
@api_view(['GET'])
@permission_classes([AllowAny])  # public endpoint
def cek_nik(request, nik):
    # Public data: always return
    response = {'found': True, 'nama_lengkap': ..., 'telepon': ...}

    # Sensitive data: only if user is authenticated AND owns the NIK
    user_owns_nik = (
        request.user.is_authenticated and
        hasattr(request.user, 'identitas') and
        request.user.identitas.nik == nik
    )
    if user_owns_nik:
        response['wbp_history'] = [...]  # sensitive: visiting history

    return Response(response)
```

Pattern: keep `@permission_classes([AllowAny])` so the endpoint works for everyone, but conditionally include sensitive sub-objects based on auth + ownership check. Avoids creating separate authenticated/unauthenticated endpoints.

## Docker / DevOps

### Gunicorn `reload=True` unreliable on Windows Docker

With `reload=True` and volume mounts (`.:/app`), file change detection via inotify/polling is unreliable on Windows Docker. Workers may not pick up code changes.

**Workaround:** `docker kill api && docker start api` forces full restart. If still stale, rebuild: `docker compose up -d --build api`.

**Better for production:** Set `reload=False` and use proper deployment.

### Nginx `Connection: upgrade` causes 502

Setting `proxy_set_header Connection "upgrade"` globally causes keep-alive race conditions between nginx → Vite proxy → gunicorn. After a 401 response, the next request on the same connection gets 502.

**Fix:** Use `map $http_upgrade $connection_upgrade` to only set `upgrade` for WebSocket requests:
```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

## Frontend

### Raw `fetch()` bypasses axios auth interceptor

`fetch('/api/...')` doesn't include the `Authorization: Bearer <token>` header. Must use `axios.get('/...')` which goes through the interceptor defined in `ss_app/src/index.js`.

### FileUploader custom filename

`FileUploader.upload(path, file, schema, customFilename)` — 4th parameter allows fixed filenames (no UUID). Used for predictable file paths like `karutan/joko-widodo.webp`.

### SDP Binary Asset Proxy

External server images (SDP photos) need a streaming proxy through Django:
1. Backend: `StreamingHttpResponse` + `requests.get(url, stream=True)`
2. Route: `path('api/sdp-media/<path:path>', proxy_media)`
3. Frontend helper:
```javascript
function getFotoUrl(foto) {
    if (!foto) return null;
    if (foto.startsWith('http') || foto.startsWith('data:')) return foto;
    const clean = foto.replace(/^(sdp\.rutanjakpus\.id\/sdp\/|sdp\/|\/sdp\/)/, '').replace(/^\//, '');
    return `/api/sdp-media/${clean}`;
}
```

### RegisterPage — Auto-fill NIK should trigger WBP history

When the form auto-fills the NIK from the user profile, also trigger the `cekNik` call to load WBP history. Without this, the user has to delete and re-type the NIK to see their history.

```javascript
useEffect(() => {
    const i = currentUser?.identitas;
    if (i?.nik && !filledRef.current) {
        // ... setForm with nik ...
        if (nikVal.length >= 16) {
            cekNik(nikVal).then(data => {
                if (data.wbp_history?.length) setWbpHistory(data.wbp_history);
            });
        }
    }
}, [currentUser]);
```

## SDP Integration

### SDP `id` field = `ID_PERKARA`, not `NOMOR_INDUK`

- `NOMOR_INDUK` = identity number (shared across multiple perkara for same person)
- `ID_PERKARA` = unique case/registration identifier

The SDP API (`ss_sdp_api/ss_api.php`) should map `id` to `ID_PERKARA`. The local `nomor_induk_sdp` field stores this value for matching.

In `buildMap`: keep `id_perkara` in the id-matching array, remove `nomor_induk`:
```php
if (in_array($lc, array('id','id_wbp','id_warga','id_napi','wbp_id','warga_id','napi_id','kode_bap','register_id','id_perkara'))) $map['id'] = $c;
```

In `detail()`: use `WHERE p.ID_PERKARA = $id` and `'id' => $row['ID_PERKARA']`.

After deploying the PHP fix, re-import WBPs from SDP so `nomor_induk_sdp` gets the correct `ID_PERKARA` values.

### Auto-import from SDP

Use the same approach as `/admin/wbp` page:
1. Frontend stores full SDP row from `searchPerkara` results (`_sdp: w`)
2. Frontend sends `sdp_data` in PUT request
3. Backend POSTs to `/api/wargabinaan/` with the full SDP data
4. `_create_wbp` handles create-or-update logic (matches by `nomor_registrasi`)

Do NOT use `sdp_client.get_detail()` — it requires `ID_PERKARA` which may not match the SDP proxy's current lookup field. The full SDP row from `findWBP` has all the fields `_create_wbp` needs.

### Auto-import via Internal API Call

When auto-importing WBP data from SDP, call the local wargabinaan API internally using `requests.post()` instead of trying to duplicate `_create_wbp` logic:

```python
import requests
resp = requests.post(
    'http://localhost:5005/api/wargabinaan/',
    json=sdp_data,
    headers={'Authorization': request.headers.get('Authorization', ''),
             'Content-Type': 'application/json'},
    timeout=60  # generous for photo downloads
)
if resp.status_code in (200, 201):
    created = resp.json()
    new_wb_id = created.get('id')
    wb = WargaBinaan.objects.get(pk=new_wb_id)
```

**Why internal API call instead of direct function call:** `_create_wbp` expects a DRF `Request` object with proper `request.user`, handles transactions, downloads photos from SDP, and has full error handling. Duplicating this logic risks inconsistency. The internal API call reuses the exact same code path as the `/admin/wbp` page.

**Pitfall:** Don't use `APIRequestFactory` from `rest_framework.test` — it's for tests, not production. Use `requests` library (already installed in Docker, used by `sdp_client.py`).
