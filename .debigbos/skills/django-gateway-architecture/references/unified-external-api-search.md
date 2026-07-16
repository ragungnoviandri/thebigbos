# Unified Search: Combining Local DB + External API Through Django

When the frontend needs to search data that exists partly in Django's local DB
and partly in an external system (SDP, legacy API, third-party service), use
Django as the **search aggregator** — a single endpoint that queries both
sources, merges results, and marks each result's origin.

## When to Use This Pattern

- The user searches by name/ID and expects results from **both** local DB and
  an external system
- External data can be imported into the local DB on-demand (user clicks →
  create local record from external data)
- The external API is slower or unreliable — graceful fallback is required
- You want a **single search endpoint** consumed by the frontend, not two
  separate calls from the browser

## Architecture

```
Frontend (search)  →  Django endpoint  →  [Local DB query]
                                       →  [External API call]
                     ←  Merged results with source: "local" | "sdp"
```

Django runs both searches in the same view, deduplicates, and returns a
unified response. The frontend only calls one URL.

## Implementation Pattern

### 1. Backend View

```python
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.db.models import Q
from wargabinaan.models import WargaBinaan
from core.sdp_client import find_wbp


@api_view(['GET'])
@permission_classes([AllowAny])
def cari_wbp(request):
    q = request.GET.get('q', '').strip()
    sdp = request.GET.get('sdp', '').lower() == 'true'
    if len(q) < 2:
        return Response({'results': []})

    # ── 1. Search local DB ──
    local_results = WargaBinaan.objects.filter(
        Q(identitas__firstname__icontains=q) |
        Q(identitas__lastname__icontains=q) |
        Q(nomor_registrasi__icontains=q)
    ).select_related('identitas')[:10]

    results = []
    for w in local_results:
        i = w.identitas
        results.append({
            'id': str(w.id),
            'nomor_registrasi': w.nomor_registrasi,
            'nama': f'{i.firstname} {i.middlename} {i.lastname}'.strip(),
            'nik': i.nik,
            'source': 'local',           # ← mark origin
        })

    # ── 2. Search external API (optional flag) ──
    if sdp:
        try:
            sdp_result = find_wbp(nama=q, page_size=10)
            sdp_items = sdp_result.get('results', [])
            existing_regs = {r['nomor_registrasi'] for r in results}

            for item in sdp_items:
                reg = (item.get('nomor_registrasi') or '').strip()
                if reg not in existing_regs:          # ← deduplicate
                    foto_url = (item.get('foto') or '').strip()
                    if foto_url and not foto_url.startswith('http'):
                        foto_url = f'http://sdp.rutanjakpus.id/sdp/{foto_url.lstrip("/")}'
                    results.append({
                        'id': None,                      # not yet in local DB
                        'nomor_registrasi': reg,
                        'nama': (item.get('name') or '').strip(),
                        'nik': (item.get('nik') or '').strip(),
                        'source': 'sdp',                 # ← mark origin
                        'nomor_induk_sdp': (item.get('id') or '').strip(),
                        'foto': foto_url,                # ← photo URL
                        'tempat_lahir': (item.get('tempat_lahir') or '').strip(),
                        'tanggal_lahir': (item.get('tanggal_lahir') or '').strip() if item.get('tanggal_lahir') else None,
                        'jenis_kelamin': (item.get('jenis_kelamin') or '').strip(),
                        'agama': (item.get('agama') or '').strip(),
                    })
        except Exception:
            pass  # External failure must never block search

    return Response({'results': results})
```

### 2. Deduplication Strategy

Deduplicate by the **primary business key** (usually `nomor_registrasi` or
`nik`), not by Django PK — external records don't have one yet.

```python
existing_keys = {r['nomor_registrasi'] for r in local_results}

for item in external_results:
    if item.get('NMR_REG_GOL') not in existing_keys:
        results.append(...)
```

### 3. Source-Aware Frontend Handling

Each result carries a `source` field. The frontend uses it to:

- **Show a badge** (`local` vs `SDP`) so the user knows which source
- **Handle selection differently**: local results are ready to use; external
  results need a local record created first

```javascript
// Frontend handler
const handleSelectWbp = async (wb) => {
  if (wb.source === 'sdp' && !wb.id) {
    // 1. Create local record from external data
    const newWbp = await createWbpFromSdp({
      name: wb.nama,
      nik: wb.nik,
      nomor_registrasi: wb.nomor_registrasi || '-',
      id: wb.nomor_induk_sdp,
    });
    // 2. Use the new local ID
    wb = { ...wb, id: newWbp.id, source: 'local' };
  }
  setSelectedWbp(wb);
};
```

### 4. Creating Local Records from External Data

The POST handler reuses the same endpoint that the admin would use to
manually create a WBP record. The data shape maps from the external API
response format to Django models:

```python
# Minimal fields required to create a local WBP from SDP data
# NOTE: The SDP API (find_wbp) accepts ALL_CAPS query params
# (NAMA_LENGKAP, NMR_REG_GOL, NIK) but RETURNS camelCase response
# fields (name, nomor_registrasi, nik, id, foto, etc.)
{
    "name": "Budi Santoso",           # name (not NAMA_LENGKAP)
    "nik": "3273010203040001",         # nik (not NIK)
    "nomor_registrasi": "R1234",       # nomor_registrasi (not NMR_REG_GOL)
    "id": "SDP-12345",                 # id (not NOMOR_INDUK) — saves as nomor_induk_sdp
    "foto": "http://sdp.rutanjakpus.id/sdp/...",  # auto-prefixed photo URL
    "tempat_lahir": "Jakarta",
    "tanggal_lahir": "1990-01-01",
    "jenis_kelamin": "Laki-Laki",
    "agama": "Islam",
}
```

## Frontend UX

The frontend should give the user clear signals about result origin:

- **Local results**: plain row, click → select immediately
- **SDP results**: badge/label "SDP", click → show loading → create local
  record → select
- **Creating state**: spinner text like "Mengambil data dari SDP..." while
  the POST request completes

```jsx
{wb.source === 'sdp' && (
  <span style={{
    background: 'rgba(23,162,184,0.2)', color: '#17a2b8',
    padding: '1px 6px', borderRadius: 3, fontSize: '0.7rem',
    fontWeight: 'bold', verticalAlign: 'middle',
  }}>SDP</span>
)}
```

## Pitfalls

- **External API timeout**: Always wrap external calls in try/except. A slow
  or down external service must not block the entire search. Consider using
  `timeout=` on the `requests` call.
- **Duplicate results**: If local DB already has a record that matches an
  external record (same nomor_registrasi), deduplicate client-side or
  server-side. Server-side dedup is cleaner (avoids confusing the frontend).
- **Rate limiting**: If the external API has rate limits, consider caching
  results or limiting SDP search to authenticated users only.
- **Partial data**: External search results may have minimal fields (name,
  reg number only). The create-record step may need a second API call
  (`get_detail`) to fetch full data — or accept partial creation and let
  the user fill in details later.
- **Graceful degradation**: When `?sdp` flag is used but the external API
  fails, the endpoint should still return local results. Never return an
  error for a failed external search when local results are available.
- **SDP field naming mismatch**: The SDP `/findWBP` endpoint accepts
  **ALL_CAPS** query params (`NAMA_LENGKAP`, `NMR_REG_GOL`, `NIK`) but
  **returns camelCase** response fields (`name`, `nomor_registrasi`, `nik`,
  `id`, `foto`). Always use camelCase accessors on the response dict, not
  the query parameter names. The `find_wbp()` function in `sdp_client.py`
  handles the params; you handle the response.
