# Nav Injection: Tab Filter + Fallback Timeout (2026-06-19)

## Problem
After injecting nav detection script into proxied iframes, tab switches on the SDP page
("Warga Binaan yang Dikunjungi" / "Data Pengunjung") triggered the spinner overlay
because `<a href="#tab-id">` clicks fired the injected click handler.
No `sdp_nav_end` arrived (AJAX tab switch = no page load), so the spinner stayed forever.

## Root Cause
The original click handler caught ALL anchor clicks:
```javascript
if(t && t.tagName === "A" && t.href) u();
```

Tab links (href="#tab1" or href="#tab2") have non-empty href values, so they passed
the filter and triggered nav_start with no corresponding nav_end.

## Two-Sided Fix

### 1. Proxy side: Filter # and javascript: links
Add getAttribute("href") check in the injected click handler:

```javascript
document.addEventListener("click", function(e) {
  var t = e.target && (e.target.closest ? e.target.closest("a") : e.target);
  if(t && t.tagName === "A" && t.href) {
    var h = t.getAttribute("href");
    if(!h || h[0] === "#" || h.indexOf("javascript:") === 0) return;  // ← SKIP TABS
    u();
  }
}, {capture: true});
```

Three conditions to skip:
1. `!h` — null/undefined href
2. `h[0] === "#"` — anchor link (tab switch, scroll-to-top)
3. `h.indexOf("javascript:") === 0` — javascript: pseudo-protocol

### 2. React side: Per-navigation fallback timeout
Even with the filter, there's still a risk of a stuck spinner from other false
positives (custom AJAX buttons, programmatic navigation). Add a per-nav timeout:

```javascript
const navFallbackRef = useRef(null);

// In handleMessage for sdp_nav_start:
clearTimeout(navFallbackRef.current);
navFallbackRef.current = setTimeout(() => {
  navActive.current = false;
  setProgress(100);
  setLoading(false);
}, 8000);  // 8 seconds

// In handleMessage for sdp_nav_end:
clearTimeout(navFallbackRef.current);  // cancel the fallback

// In useEffect cleanup:
clearTimeout(navFallbackRef.current);
```

The 30-second global timeout (in the useEffect mount) serves as a final safety net.

## Verification
- Click tab → no spinner overlay
- Click real link (href="/other-page") → spinner shows, hides on nav_end
- Click form submit → spinner shows, hides on nav_end
- If nav_end is somehow missed → spinner auto-closes after 8 seconds

## Files Changed
- `ss_api/sdp_proxy/views.py` — nav injection click handler
- `ss_app/src/pages/antrian/SdpIframe.js` — navFallbackRef + timeout logic
