# SDP Binary Media Proxy

Pattern for proxying binary content (images, uploads, assets) from an external SDP/legacy server through Django, when the external server serves photos at relative paths like `upload/2025/10/xxx.jpg`.

## Problem

SDP API returns WBP data including photo fields like `foto: "upload/2025/10/xxx.jpg"` — a path relative to the SDP server. The frontend can't load `http://sdp.rutanjakpus.id/sdp/upload/...` directly (CORS, network isolation). Need to proxy through Django.

The existing SDP proxy handles:
- JSON API: `/api/sdp/ss_api/<endpoint>` → `requests.get(SDP_BASE_API/endpoint)` → `JsonResponse`
- HTML pages: `/sdp-proxy/<path>` → `requests.get(SDP_BASE/path)` → HTML with URL rewriting

Neither handles binary image streaming.

## Solution: Streaming Binary Proxy

### Backend View (`sdp_proxy/views.py`)

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
    except requests.RequestException:
        return HttpResponse("SDP media not available", status=502)
```

### URL Route (`core/urls.py`)

```python
path('api/sdp-media/<path:path>', sdp_proxy_views.proxy_media, name='sdp-media-proxy'),
```

### Frontend URL Helper

```javascript
function getFotoUrl(foto) {
    if (!foto) return null;
    if (foto.startsWith('http') || foto.startsWith('data:')) return foto;
    // Strip SDP base prefixes, keep the relative path
    const clean = foto
        .replace(/^(sdp\.rutanjakpus\.id\/sdp\/|sdp\/|\/sdp\/)/, '')
        .replace(/^\//, '');
    return `/api/sdp-media/${clean}`;
}
```

### Usage in `<img>` tags

```jsx
<img src={getFotoUrl(w.foto)} alt="" style={{width:60, height:75, objectFit:'cover'}} />
```

## Why StreamingHttpResponse?

- **Memory**: Large photos don't buffer entirely in Django memory
- **Speed**: `iter_content(chunk_size=8192)` streams chunks as they arrive
- **Content-Type passthrough**: Preserves the original image format (JPEG, PNG, etc.)
- **Caching**: `Cache-Control: public, max-age=3600` — browser caches for 1 hour

## No Authentication

The media proxy is intentionally unauthenticated (no `@permission_classes`) because:
- Photos are displayed in `<img>` tags which don't send auth headers
- The SDP server itself handles access control
- Cached images don't trigger auth checks

If auth is needed, use signed URLs or a short-lived token in the query string.
