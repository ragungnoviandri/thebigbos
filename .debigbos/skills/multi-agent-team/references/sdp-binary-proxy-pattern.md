# SDP Binary/Media Proxy Pattern

## Problem
SDP server serves photos and binary assets at paths like `upload/20xxx/photo.jpg`. The existing SDP proxy only handles:
- JSON API: `/sdp/ss_api/<endpoint>` (returns JSON)
- HTML pages: `/sdp-proxy/<path>` (full page proxy with injection)

Binary assets need a separate streaming proxy that preserves Content-Type.

## Solution

### 1. Backend: Binary Proxy View (`sdp_proxy/views.py`)

```python
from django.http import StreamingHttpResponse
import requests

SDP_MEDIA_BASE = getattr(settings, "SDP_BASE", "http://sdp.rutanjakpus.id/sdp")

def proxy_media(request, path):
    """Proxy binary content (images, uploads) from SDP server."""
    url = f"{SDP_MEDIA_BASE}/{path}"
    try:
        resp = requests.get(url, stream=True, timeout=30)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        response = StreamingHttpResponse(
            resp.iter_content(chunk_size=8192),
            content_type=content_type,
            status=resp.status_code,
        )
        response["Cache-Control"] = "public, max-age=3600"
        return response
    except requests.RequestException as e:
        return HttpResponse("SDP media not available", status=502)
```

### 2. Backend: URL Route (`core/urls.py`)

```python
path('api/sdp-media/<path:path>', sdp_proxy_views.proxy_media, name='sdp-media-proxy'),
```

No auth required (`@permission_classes` omitted) — these are public assets.

### 3. Frontend: URL Helper

```javascript
function getFotoUrl(foto) {
    if (!foto) return null;
    // Already a full URL or data URL
    if (foto.startsWith('http') || foto.startsWith('data:')) return foto;
    // Strip SDP base path, proxy through our API
    const clean = foto
        .replace(/^(sdp\.rutanjakpus\.id\/sdp\/|sdp\/|\/sdp\/)/, '')
        .replace(/^\//, '');
    return `/api/sdp-media/${clean}`;
}
```

### 4. Restart

```bash
docker compose restart api
```

## Flow

```
SDP server:   upload/20xxx/photo.jpg
      ↓
Frontend:     getFotoUrl("upload/20xxx/photo.jpg")
      ↓       → "/api/sdp-media/upload/20xxx/photo.jpg"
      ↓
Vite proxy:   /api/* → Django
      ↓
Django:       proxy_media → requests.get("http://sdp.rutanjakpus.id/sdp/upload/20xxx/photo.jpg")
      ↓       → StreamingHttpResponse(iter_content, content_type)
      ↓
Browser:      <img src="/api/sdp-media/upload/20xxx/photo.jpg" />
```
