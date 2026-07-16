# HTTP Proxy Debugging Patterns

Key techniques for debugging HTTP reverse proxies — especially with legacy systems (PHP, Java, old SDP-style apps).

## 1. Test with `requests` from Within the Container

The most reliable way to debug proxy behavior is to run Python directly inside the container:

```bash
docker compose exec api python -c "
import requests
r = requests.get('http://target-server/path', headers={'Host': 'domain.tld'})
print(r.status_code, r.reason)
print(dict(r.headers))
print(len(r.content))
"
```

### Why this works
- Eliminates browser caching, cookie storage, client-side JS
- Gives raw HTTP response (headers, status, body)
- Runs from the same network environment as your production proxy code

## 2. Check Raw HTTP Response Properties, Not Just Status

Always check these four things for every request:

```python
r.status_code        # 200? 302? 500?
r.headers            # All response headers
r.headers.get('Set-Cookie')  # Session cookies?
len(r.content)       # 0 means empty body — often a clue
```

### Common patterns

| Status | Headers | Body | Meaning |
|--------|---------|------|---------|
| 200 | `Content-Length: 0` or empty | Empty/practically empty | May be a `Refresh` redirect or PHP error-without-body |
| 302 | `Location: ...` | Empty | Standard redirect — follow it if the domain is resolvable |
| 200 | `Refresh: 0;url=...` | `Content-Length: 0` | **Non-standard redirect** — PHP/laravel `Refresh` meta header, NOT an HTTP 302 |
| 200 | Login form content | Full HTML | Server rejected credentials (wrong content-type, missing fields) |
| 500 | — | HTML error page | Server-side error, check logs on target |

## 3. SDP/Legacy Systems Use `Refresh` Header, Not 302

**This is a common trap.** Many PHP and legacy Java apps (including SDP) do not return HTTP 302/303 redirects after login. Instead they return:

```
HTTP/1.1 200 OK
Content-Length: 0
Refresh: 0;url=http://sdp.example.com/sdp/dashboard
```

The browser interprets the `Refresh` header as a meta-refresh directive and navigates to the URL after 0 seconds.

### What happens with a naive proxy

```python
# ❌ Does NOT catch Refresh redirects:
if resp.status_code in (301, 302, 303, 307, 308):
    # Only handles HTTP redirects — misses Refresh header
```

### The fix

```python
import re

# Handle Refresh header (meta refresh redirect)
if "Refresh" in resp.headers:
    refresh = resp.headers["Refresh"]
    match = re.search(r'url=(\S+)', refresh, re.IGNORECASE)
    if match:
        old_url = match.group(1)
        # Rewrite the SDP domain to your proxy path:
        sdp_host = "sdp.example.com"
        new_url = old_url.replace(f"http://{sdp_host}/sdp/", "/sdp-proxy/")
        refresh = refresh.replace(old_url, new_url)

    response = HttpResponse(status=200)
    response["Refresh"] = refresh
    # Forward Set-Cookie too:
    if "Set-Cookie" in resp.headers:
        response["Set-Cookie"] = resp.headers["Set-Cookie"]
    return response
```

## 4. `allow_redirects` Behavior in `requests`

```python
# DEFAULT (True): requests follows 3xx redirects automatically
# PROBLEM: If the target server redirects to a domain name that the
#          container CANNOT resolve (DNS error), requests will throw
#          ConnectionError → your proxy returns 502

# FIX: Set allow_redirects=False, then manually rewrite the Location header
r = requests.get(url, allow_redirects=False, ...)
location = r.headers.get("Location", "")
if location:
    # Rewrite SDP domain to proxy path
    location = location.replace("http://sdp.example.com/sdp/", "/sdp-proxy/")
    response["Location"] = location
    response.status_code = r.status_code
```

## 5. Form Login — Check Content-Type Header Name

**Django WSGI vs PHP expectation**: Django WSGI (gunicorn/uwsgi) stores the incoming `Content-Type` header under the key `CONTENT_TYPE` in `request.META`, NOT `HTTP_CONTENT_TYPE`.

```python
# ❌ Wrong — Django returns None for this:
ct = request.META.get('HTTP_CONTENT_TYPE')

# ✅ Correct:
ct = request.META.get('CONTENT_TYPE', 'application/x-www-form-urlencoded')
```

If your proxy sends the `Content-Type` header under the wrong key, the legacy server won't parse form data (username, password) and login will silently fail — returning the login page again instead of the dashboard.

### How to detect this bug

```python
# Inside the container, test login directly to SDP:
s = requests.Session()
s.headers.update({'Host': 'sdp.example.com'})

# GET login form to get session cookie
r1 = s.get(f'http://172.27.225.9/sdp/biometrickunjungan/home')
# Extract form action URL from HTML
# POST with form data
r2 = s.post(f'http://172.27.225.9/sdp/Login/loginsimple',
    data={'username': ..., 'password': ..., 'submit': 'Login', 'url': 'biometric/home'},
    headers={'Content-Type': 'application/x-www-form-urlencoded'})

# If r2.status_code == 200 and 'Login' in r2.text → login failed
# If r2.status_code == 200 and r2.headers.get('Refresh') → login succeeded
```

## 6. URL Rewriting Patterns

When proxying a legacy app, the HTML response contains hardcoded URLs that need rewriting:

```python
# In the response body:
# 1. Absolute URLs with the original domain
#    http://sdp.example.com/sdp/Login/loginsimple
#    → /sdp-proxy/Login/loginsimple
#
# 2. Root-relative paths (most common in PHP apps)
#    /sdp/Login/loginsimple
#    → /sdp-proxy/Login/loginsimple
#
# 3. Form action URLs (absolute)
#    <form action="http://sdp.example.com/sdp/Login/loginsimple">
#    → <form action="/sdp-proxy/Login/loginsimple">
#
# 4. CSS/JS asset references
#    href="http://sdp.example.com/sdp/public/js/jquery.js"
#    → href="/sdp-proxy/public/js/jquery.js"

# Simple string replacement:
body = body.replace('http://sdp.example.com/sdp/', '/sdp-proxy/')
body = body.replace('="/sdp/', '="/sdp-proxy/')  # root-relative
body = body.replace("='/sdp/", "='/sdp-proxy/")   # single-quote variant
body = body.replace("http://sdp.example.com:80/sdp/", "/sdp-proxy/")  # with port
```

## 7. Session Cookie Forwarding

Legacy systems set a session cookie on login. Your proxy MUST forward this:

```python
# In the login response:
if "Set-Cookie" in resp.headers:
    response["Set-Cookie"] = resp.headers["Set-Cookie"]

# On subsequent requests, forward the cookie from the browser:
cookie = request.META.get("HTTP_COOKIE", "")
if cookie:
    proxy_headers["Cookie"] = cookie
```

## 8. Testing the Full Flow End-to-End

After fixing the proxy, verify the ENTIRE flow in one script:

```python
import requests

s = requests.Session()
s.headers.update({'Host': 'sdp.example.com'})

# Step 1: Initial page load (expect login form)
r1 = s.get('http://IP/sdp/biometrickunjungan/home')
assert 'Login' in r1.text or 'loginsimple' in r1.text, \
    f"Expected login form, got status {r1.status_code}"

# Step 2: Submit login
r2 = s.post('http://IP/sdp/Login/loginsimple',
    data={'username': 'user', 'password': 'pass', 'submit': 'Login', 'url': 'biometric/home'},
    headers={'Content-Type': 'application/x-www-form-urlencoded'})

# Step 3: Check for Refresh header (SDP redirect)
if 'Refresh' in r2.headers:
    # Extract redirect URL from Refresh header
    import re
    m = re.search(r'url=(\S+)', r2.headers['Refresh'])
    if m:
        redirect_url = m.group(1)
        r3 = s.get(redirect_url)
        assert 'Login' not in r3.text, \
            "After login+redirect, still seeing login form"
```

## Common Pitfalls Summary

| Symptom | Likely Root Cause | Fix |
|---------|------------------|-----|
| 502 Bad Gateway | `allow_redirects=True` + container can't resolve domain | `allow_redirects=False` + rewrite Location |
| Login succeeds but immediately logs out | `Content-Type` header not forwarded (Django `CONTENT_TYPE` vs `HTTP_CONTENT_TYPE`) | Use `request.META.get('CONTENT_TYPE', ...)` |
| Blank page after login form submit | SDP uses `Refresh` header, not 302 | Handle `Refresh` header and rewrite URL |
| Redirect goes to external domain | HTML/Location URLs not rewritten | Regex/string replacement of domain |
| Function works in isolation but times out from request handler | Gunicorn single-worker deadlock (self-call) | `workers >= 2` |
