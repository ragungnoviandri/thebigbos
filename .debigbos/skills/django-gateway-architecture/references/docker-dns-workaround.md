# Docker DNS Resolution Workaround for Internal Hostnames

## The Problem

Docker containers use an embedded DNS resolver (`127.0.0.11`) that forwards to the host's DNS. When the host can resolve private/internal hostnames (e.g., `sdp.rutanjakpus.id`) but the container cannot, it's often because:

- The internal hostname is only resolvable via the host's network adapter (not forwarded to Docker's DNS)
- Docker Desktop on Windows uses a VM layer (`192.168.65.7` for DNS proxy) that doesn't have the same DNS zone visibility  
- The internal hostname resolves nslookup on the Windows host but not from containers

### Symptom

```python
import socket
socket.gethostbyname('internal.hostname.local')
# → [Errno -2] Name or service not known

import requests
requests.get('http://internal.hostname.local/path', timeout=5)
# → NameResolutionError: Failed to resolve 'internal.hostname.local'
```

## The Fix: IP Rewrite + Host Header

The internal host is reachable **by IP** from the container. Use the IP for the actual HTTP request and set the `Host` header so the server knows which virtual host to serve:

```python
SDP_IP = '172.27.225.9'  # internal server IP
SDP_DOMAIN = 'sdp.rutanjakpus.id'


def _rewrite_url(url):
    """Replace internal hostname with IP for Docker containers."""
    if SDP_DOMAIN in url:
        return url.replace(SDP_DOMAIN, SDP_IP)
    return url


def _sdp_get(url, params=None, timeout=60):
    """GET request to internal server with DNS workaround."""
    actual_url = _rewrite_url(url)
    headers = {'Host': SDP_DOMAIN}
    return requests.get(actual_url, params=params, headers=headers, timeout=timeout)


# Usage
url = 'http://sdp.rutanjakpus.id/api/search'
resp = _sdp_get(url, params={'q': 'test'})
# Actually hits: http://172.27.225.9/api/search
# With Host header: sdp.rutanjakpus.id
```

### Why This Works

- Docker can resolve the IP (direct network route), just not the hostname
- The `Host` HTTP header tells the target server which virtual host configuration to use (critical for shared hosting / name-based virtual hosting)
- The IP doesn't change for internal services (they're on a fixed private network)

## Implementation Patterns

### Pattern 1: Standalone Helper (recommended)

Create a shared helper module that all SDP-facing code imports:

```python
# core/sdp_base.py
import requests
from django.conf import settings

SDP_BASE = settings.SDP_BASE        # e.g. 'http://172.27.225.9/sdp' (IP for Docker)
SDP_HOST = settings.SDP_HOST        # e.g. 'sdp.rutanjakpus.id' (domain for Host header)


def sdp_get(endpoint, params=None, timeout=60):
    url = f'{SDP_BASE}/{endpoint}'
    headers = {'Host': SDP_HOST}
    return requests.get(url, params=params, headers=headers, timeout=timeout)


def sdp_post(endpoint, data=None, files=None, timeout=60):
    url = f'{SDP_BASE}/{endpoint}'
    headers = {'Host': SDP_HOST}
    return requests.post(url, data=data, files=files, headers=headers, timeout=timeout)
```

⚠️ **Two settings, not one**: `SDP_BASE` (IP-based, for actual requests) and `SDP_HOST` (domain name, for Host header and frontend URL construction) serve different purposes. In `.env`, configure both:
```
SDP_BASE=http://172.27.225.9/sdp
SDP_HOST=sdp.rutanjakpus.id
```
The old practice of putting the domain in `SDP_BASE` and letting `requests` resolve it doesn't work inside Docker containers (DNS fails). The IP in `SDP_BASE` works for routing; the domain in `SDP_HOST` satisfies the upstream server's virtual hosting requirements.

### Pattern 2: Inline (for one-off usage)

```python
import requests

SDP_DOMAIN = 'sdp.rutanjakpus.id'
SDP_IP = '172.27.225.9'

url = 'http://sdp.rutanjakpus.id/sdp/media/photos/test.jpg'.replace(SDP_DOMAIN, SDP_IP)
headers = {'Host': SDP_DOMAIN}
resp = requests.get(url, headers=headers, timeout=15)
```

## Verification

From inside the container, test connectivity:

```bash
# Test by IP (should work)
docker exec api curl -s -o /dev/null -w '%{http_code}' http://172.27.225.9/sdp/

# Test by hostname (WILL FAIL in Docker)
docker exec api curl -s -o /dev/null -w '%{http_code}' http://sdp.rutanjakpus.id/sdp/

# Test with workaround (SHOULD work)
docker exec api python -c "
import requests
url = 'http://172.27.225.9/sdp/api/ss_api/findWBP'
r = requests.get(url, params={'NAMA_LENGKAP': 'test'}, headers={'Host': 'sdp.rutanjakpus.id'}, timeout=10)
print(f'Status: {r.status_code}, Results: {r.json().get(\"total\", 0)}')
"
```

## Where to Apply

Every module in your Django container that makes HTTP requests to `sdp.rutanjakpus.id` needs the workaround:

| Module | File | Endpoints |
|--------|------|-----------|
| SDP API client | `core/sdp_client.py` | SDP search (`findWBP`), detail, referensi |
| SDP proxy | `sdp_proxy/views.py` | Full HTML proxy for biometric pages |
| SDP photo handler | `core/sdp_photo_handler.py` | Photo download for caching |

## Caveats

- **IP can change**: If the internal server moves to a different subnet, update `SDP_IP` in settings or environment variables
- **Host header required**: Without it, the server may return a default virtual host (often the login page or a 404)
- **HTTPS**: If the internal server uses HTTPS with a certificate issued to the hostname, the IP mismatch will trigger SSL errors. Use `verify=False` or disable SSL verification ONLY if you trust the internal network
- **Cookies**: The domain set by `Set-Cookie` headers will be the domain from the URL (neither `sdp.rutanjakpus.id` nor the IP will match the browser's origin). Modern browsers typically ignore domain attributes that don't match the origin, falling back to the request origin — which is correct for the proxy pattern

## When NOT to Use Direct IP: Prefer the Internal Proxy Path

For **media downloads** (photos, documents) from an internal host like SDP, **DO NOT use the direct IP + Host header pattern**. Instead, route the download through the Django internal proxy:

### ❌ Wrong (direct IP download)

```python
url = 'http://sdp.rutanjakpus.id/sdp/upload/2025/10/xxx.jpg'.replace('sdp.rutanjakpus.id', '172.27.225.9')
resp = requests.get(url, headers={'Host': 'sdp.rutanjakpus.id'})
```

### ✅ Correct (via internal proxy path)

```python
import re

# 1. Extract path from SDP URL
m = re.match(r'^.*sdp\\.rutanjakpus\\.id/sdp/(.*?)(\\?.*)?$', sdp_url)
path = m.group(1)  # e.g. 'upload/2025/10/xxx.jpg'

# 2. Download via Django's own proxy_full view (csrf_exempt, no auth needed)
#    ⚠️ Timeout must be generous — the proxy_full view itself calls SDP with
#       timeout=60, and the internal call adds another layer of latency.
#       Use timeout=60 to match the proxy's own timeout.
resp = requests.get(f'http://localhost:5005/sdp-proxy/{path}', timeout=60)

# 3. Content is the actual image (proxy_full returns image if SDP responds 200)
```

### Why Proxy Path Is Better

| Aspect | Direct IP | Proxy Path |
|--------|-----------|------------|
| Follows gateway architecture | ❌ Bypasses Django | ✅ All traffic through Django |
| Resilient to IP changes | ❌ Hardcoded IP | ✅ Uses SDP_BASE from settings |
| Same path as frontend | ❌ Different path | ✅ Same `/sdp-proxy/` path |
| Cookie/auth handling | ❌ Manual | ✅ Automatic (proxy handles headers) |
| Consistency | ❌ Duplicate connection logic | ✅ Single code path via proxy_full |

### Rule of Thumb

- **API calls** (search, detail, referensi) → use direct IP + Host header (fast, no HTML rewriting needed)
- **Media downloads** (photo, document files) → use internal proxy path (`localhost:5005/sdp-proxy/{path}`)
- **HTML page proxy** (biometric login flow) → use `proxy_full` view (already handles IP rewrite internally)
