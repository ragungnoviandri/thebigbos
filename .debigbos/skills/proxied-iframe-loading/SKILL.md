---
name: proxied-iframe-loading
description: Show loading overlay (spinner + progress bar) for proxied cross-origin iframes, and inject external libraries (Ruffle, polyfills) into proxied HTML. Detects navigation starts via proxy-injected postMessage script + onLoad fallback for redirect chains.
trigger: building or debugging an iframe that loads proxied third-party content — need a loading indicator that covers blank-page transitions, OR need to enable legacy Flash content (camera, uploaders) in the proxied page.
---

# Proxied Iframe Loading Overlay

## Problem

A cross-origin iframe shows a **blank page** during navigation (form submit, link click, redirect). The browser doesn't expose iframe navigation-start events. The `onLoad` event only fires *after* the page finishes loading — too late to cover the blank transition.

## Solution: Hybrid Approach

Two complementary mechanisms:

1. **Proxy-injected script + postMessage** — detect user interaction (form submit, link click) inside the iframe *before* navigation starts. Injected by the proxy rewriting HTML responses.
2. **Even/odd load counter** — fallback for auto-redirects (Refresh header, 302) that don't trigger user interaction. Tracks onLoad parity to distinguish transition pages from content pages.

## Architecture

```
┌─ Parent Page ──────────────────────────┐
│  <SdpIframe />                         │
│  ┌─ overlay ─────────────────────────┐ │
│  │  ◌ spinner + progress bar + %     │ │
│  │  opacity: 1 (loading) or 0 (done) │ │
│  └───────────────────────────────────┘ │
│  ┌─ iframe ──────────────────────────┐ │
│  │  src="/proxy/..."                 │ │
│  │  onLoad={handleLoad}              │ │
│  └───────────────────────────────────┘ │
│  window.addEventListener('message')     │
└─────────────────────────────────────────┘
                    ↕ postMessage({type:'sdp_nav'})
┌─ Proxy (rewrites HTML) ────────────────┐
│  Injects <script> before </body>:       │
│  - intercepts form submit               │
│  - intercepts link click (capture)      │
│  - sends postMessage to parent          │
│  - backup: beforeunload listener        │
└─────────────────────────────────────────┘
```

## Steps

### 1. Proxy injects navigation detection script

In the proxy's HTML rewrite section (e.g. Django `views.py`), inject before `</body>`:

```python
inject = '''
<script>
!function(){
var n="sdp_nav",
u=function(){window.parent&&window.parent.postMessage({type:n},"*")};
document.addEventListener("submit",u),
document.addEventListener("click",function(e){
var t=e.target&&(e.target.closest?e.target.closest("a"):e.target);
if(t&&t.tagName==="A"&&t.href){
var h=t.getAttribute("href");
if(!h||h[0]==="#"||h.indexOf("javascript:")===0)return;
u()
}
},{capture:true});
window.addEventListener("beforeunload",function(){setTimeout(u,0)});
}();
</script>
'''
if '</body>' in html:
    html = html.replace('</body>', inject + '</body>')
```

### 1b. Secondary injection: external libraries (Flash emulator, polyfills)

When the proxied page has **legacy Flash content** (camera, uploaders, embeded SWF) that modern browsers block, inject **Ruffle** (WebAssembly Flash emulator) into `<head>`. This must load *before* the body so Ruffle can intercept `<object>`/`<embed>` tags as they parse.

Add in the same HTML rewrite block, **before** the nav injection:

```python
# Inject Ruffle Flash emulator for legacy Flash content (camera, etc.)
ruffle_script = (
    '<script src="https://unpkg.com/@ruffle-rs/ruffle"></script>\n'
)
if '</head>' in html:
    html = html.replace('</head>', ruffle_script + '</head>')
elif '<head>' in html:
    html = html.replace('<head>', '<head>' + ruffle_script)
else:
    # Fallback: inject after first meta tag
    html = html.replace('<meta', ruffle_script + '<meta', 1)

# Then continue with nav injection before </body>...
```

**Ruffle Camera caveat:** Ruffle's `flash.media.Camera` API is partial. For visitor-photo use cases, skip Ruffle and inject an HTML5 replacement directly (see Section 1c).

### 1c. Path-specific legacy component replacement (ScriptCam → HTML5)

When a proxied page uses **Adobe Flash via ScriptCam** (a jQuery plugin wrapping `swfobject.js`) for camera access, override the three camera functions at page-load time with HTML5 equivalents. This runs **in addition to** (or instead of) the Ruffle injection.

**Detection:** In the proxy's HTML rewrite section, check the request path:

```python
if '/biometric/register_kunjungan' in path or '/register_kunjungan' in path:
    camera_script = (
        '<script>\\n'
        # ... see reference file for full script
        '</script>\\n'
    )
    if '</body>' in html:
        html = html.replace('</body>', camera_script + '</body>')
```

**Three-function override pattern** (replace `register_accordion_foto` prefix with your page's context prefix):

| Original Flash function | HTML5 replacement |
|------------------------|-------------------|
| `{context}_camera_on()` | `getUserMedia({video:true})` → attach to `<video autoplay playsinline>` |
| `{context}_camera_off()` | `stream.getTracks().forEach(t => t.stop())` |
| `{context}_camera_capture()` | `canvas.drawImage(video, ...)` → `canvas.toDataURL('image/jpeg', 0.85)` → store in same variable |

**Camera switching:** The original Flash dropdown change handler calls `jQuery.scriptcam.changeCamera()`. Override this by:
1. Enumerating devices via `navigator.mediaDevices.enumerateDevices()`
2. Populating the `<select>` with device labels
3. Rebinding the change event: stop current stream → `getUserMedia({video: {deviceId: {exact: id}}})`

**Data format compatibility:** Store captured photos in the same variable the original form submit script reads:
```javascript
_enrolled_foto_images_base64[foto_type] = b64;
```

**Key gotchas when writing JS inside Python string literals:**
- Use `//` for JavaScript comments, NOT `#` (which causes JS syntax errors)
- Escape Python strings properly: use `'...\\n'` for newlines, `\"` for JS quotes inside Python `'...'` strings
- The injection script must run **after** the page's own script (inject before `</body>`, not `</head>`) so the function references exist to be overridden
- Use `jQuery` (not `$`) for jQuery calls to avoid conflicts

#### Browser permission flow

```
User clicks camera dialog
  → getUserMedia triggers browser permission prompt
    → User grants "Allow"
      → Video feed appears in <video> element
    → User denies
      → Show error message in camera container
```

### 1d. SWF/Ruffle cleanup for path-specific camera replacement

When injecting an HTML5 camera override, Ruffle may still try to load the SWF (ScriptCam SWF) in the background, causing console errors and visual glitches (Ruffle logo appearing before the HTML5 video takes over).

**Order matters:** The SWF cleanup must run BEFORE the original page's swfobject.js/scriptcam.min.js tries to embed the SWF. Since the injected script is at `</body>` (after the page's scripts in `<head>`), the SWF may already be embedded. Force-remove it immediately.

Add a **SWF cleanup** function at the very start of the camera injection script that:
1. Removes all `<embed>` and `<object>` elements with Flash content
2. Stops Ruffle's SWF player if active

```javascript
var _html5_swf_cleanup=function(){
    try{
        var x=document.querySelectorAll("embed,object");
        for(var i=0;i<x.length;i++){
            if(x[i].type&&x[i].type.indexOf("flash")>=0)x[i].remove();
        }
        if(window.RufflePlayer&&window.RufflePlayer.stop)window.RufflePlayer.stop();
    }catch(e){}
};
_html5_swf_cleanup();
```

The `c.empty()` call in `_html5_start_camera()` also removes any remaining SWF elements from the camera container.

### 1e. Pitfall: Python-to-JS escaping triple-chain (critical)

When writing JavaScript inside Python string literals for proxy injection, you deal with **three levels of escaping**:

1. **Python source file** (what's in `views.py`)
2. **Python string value** (what `camera_script` evaluates to)  
3. **JavaScript output** (what the browser executes)

#### Level 1 → Level 2: Python source to Python value

| In Python source (`'...'`) | Python string value |
|---|---|
| `'<script>\n'` | actual newline (0x0A) |
| `'<script>\\n'` | backslash + n (two chars) |
| `'<script>\\\n'` | backslash + actual newline |
| `'style=\"color\"'` | `style="color"` (no backslash — `\"` is recognized escape for `"`) |
| `'style=\\"color\\"'` | `style=\"color\"` (`\\` → backslash, `"` → double-quote) |

#### Full escaping chain for common JS constructs

| What JS needs | Python source must have | From execute_code's triple-quoted string |
|---|---|---|
| `\n` (newline in JS output) — for readability | `'<script>\n'` | `"...\\n..."` (double backslash) |
| `\"` (escaped quote inside JS string) | `'style=\\"color\\"'` | `"...style=\\\\"color\\\""` (4 backslashes) |
| `\\` in JS (for regex) | `'regex \\\\d+'` | `"...\\\\\\\\d+..."` (8 backslashes) |

**Real example from this session:**

To produce JS: `c.html("<p style=\"color:red;\">")` the Python source needs:
```python
'c.html("<p style=\\"color:red;\\">")'
```
Which from an execute_code triple-quoted write string needs:
```python
"""'c.html("<p style=\\\\"color:red;\\\\">")'"""
```

**The most common mistake:** Using `\"` in Python `'...'` (one backslash + double-quote). Python recognizes `\"` as a valid escape that produces just `"` — the backslash is silently consumed. This results in JavaScript seeing unescaped double quotes, breaking the string syntax with error `missing ) after argument list`.

**Solution:** Always use `\\"` (two backslashes + double-quote) in the Python source when you need JavaScript to see `\"`.

**Protip for debugging:** Use `exec()` to evaluate the Python string production and `repr()` to see what the string value actually contains:
```python
code_block = "camera_script = ('<script>\\n' ... ')</script>\\n'"
ns = {}
exec(code_block, ns)
cs = ns["camera_script"]
# cs has the actual Python string value
print(repr(cs[:200]))  # see exact characters
```

#### Detecting \n vs newline

Check the file bytes:
```bash
# Hex 5c 6e = backslash + n (\\n escape in Python source)
# Hex 0a    = actual newline
xxd views.py | grep -m1 'script>'
```

```python
# In Python, test the resulting string value
if "\\n" in camera_script:     # has literal backslash-n characters
    pass
if "\n" in camera_script:      # has actual newline characters  
    pass
```

### 1f. Pitfall: Conditional variable scope (UnboundLocalError)

When `camera_script` (or any injected script variable) is assigned only inside a Python `if` block, any code that references it must also be inside that same block:

When adding conditional injection blocks (like the path-specific camera replacement above), the `if '</body>' in html:` block MUST be at the SAME indentation level as the surrounding code. Python is strict about this. A common error:

```python
# WRONG — indentation mismatch causes IndentationError
            if path == '/camera':
                camera_script = (...)
                if '</body>' in html:       # ← this must be AT the inner level
                    html = ...
# But if this if-block lines up with the outer 'if "text/html"' level, it fails.
```

**Fix:** Count the indentation carefully. The camera injection code lives inside the `if "text/html" in content_type:` block, which is inside a method. Each level = 4 spaces.

### 2. React component with overlay + message listener

Create an iframe wrapper component (e.g. `SdpIframe.js`):

- **State:** `loading` (bool), `progress` (0-88%)
- **Refs:** `timerRef` (progress simulation), `navFallbackRef` (per-nav safety timeout), `navActive` (debounce flag), `loadCount` (even/odd)
- **On mount:** `loading=true`, start simulated progress
- **On postMessage:** clear timers, `loading=true`, restart progress
- **On iframe onLoad:** even/odd counter decides show/hide
  - **Odd load** (1,3,5...): content page → `loading=false`, hide overlay
  - **Even load** (2,4,6...): transition page → show overlay (skip progress restart if postMessage already triggered it)
- **On `sdp_nav_start`:** show overlay + start progress + set 8-second fallback timeout (auto-hides spinner if `sdp_nav_end` never comes, e.g. tab switches)
- **On `sdp_nav_end`:** hide overlay + cancel fallback timeout
- **Fallback timeout logic:**

```javascript
const computeProgress = useCallback(() => {
  const STEPS = [
    { max: 20, speed: 80 },
    { max: 40, speed: 150 },
    { max: 60, speed: 300 },
    { max: 75, speed: 500 },
    { max: 88, speed: 800 },
  ];
  // ... timer chain that increments progress through steps
}, []);

// Per-navigation fallback timeout ref
const navFallbackRef = useRef(null);

// postMessage handler
const handleMessage = (event) => {
  const type = event.data?.type;
  if (type === 'sdp_nav_start') {
    clearTimeout(navFallbackRef.current);        // cancel previous
    show overlay, start progress
    // Auto-close after 8s if no sdp_nav_end (e.g. tab switch)
    navFallbackRef.current = setTimeout(() => {
      hide overlay
    }, 8000);
  }
  if (type === 'sdp_nav_end') {
    clearTimeout(navFallbackRef.current);        // cancel fallback
    hide overlay
  }
};

const handleLoad = () => {
  loadCount.current += 1;
  if (loadCount.current % 2 === 1) {
    // Odd (1,3,5...) = content → hide overlay
    setProgress(100);
    setLoading(false);
  } else {
    // Even (2,4,6...) = transition → show overlay
    setLoading(true);
    // Don't restart progress if postMessage already started it
    if (!navMsg.current) { setProgress(0); simulateProgress(); }
    navMsg.current = false;
  }
};
```

### 3. Overlay CSS

- `position: absolute; inset: 0; z-index: 10` — covers iframe
- `opacity: 1` loading / `opacity: 0; z-index: -1; pointer-events: none` hidden
- Dark background `rgba(10, 12, 35, 0.85)` during loading
- Simulated progress bar with `linear-gradient` and `transition: width 0.3s ease`
- Spinner: CSS animation `@keyframes spin { to { transform: rotate(360deg); } }`

## Pitfalls

- **Double spinner:** Both postMessage and even-load handler can trigger overlay. Use a ref flag (`navMsg`) to skip progress restart if postMessage already handled it.
- **Refresh header redirects (meta refresh):** The `beforeunload` in the injected script does NOT fire for browser-triggered Refresh redirects. The even/odd fallback handles this: the transition page (even load) shows overlay, covering the auto-redirect blank period.
- **First paint race:** On mount, if the iframe loads faster than the overlay's CSS paints, the user sees content before the overlay. The injected postMessage + simulation start on mount mitigates this.
- **Overlay stays visible if next onLoad never comes:** Add a safety timeout (e.g., 10s) to auto-hide.
- **Artificial delays frustrate users:** No minimum display time. Overlay hides immediately on onLoad. User preference: "g usah nunggu" — real-time responsiveness only.
- **Tab/spa link false positives:** If the proxied page uses tab switches via `<a href="#tab-id">`, the injected click handler fires `sdp_nav_start` for every tab click. The tab switch never triggers an `sdp_nav_end` because it's an AJAX/SPA update, not a page load. **Fix:** Filter `#` and `javascript:` links in the nav injection:
  ```javascript
  var h = t.getAttribute("href");
  if(!h || h[0] === "#" || h.indexOf("javascript:") === 0) return;
  ```
  Also add a per-navigation **fallback timeout** (e.g. 8 seconds) in the React component that auto-closes the spinner if no `sdp_nav_end` arrives.
- **Ruffle may not cover all Flash Camera APIs:** If Ruffle doesn't work for camera access (logo appears but camera stays white), use path-specific HTML5 getUserMedia injection instead (see Section 1c). The full implementation reference is in `references/sdp-flash-camera-2026-06-19.md`.
- **Python string escaping with JS content:** When writing JavaScript inside Python `'...\\\\n'` string literals for proxy injection:
  - Use `//` for comments, NOT `#` (which JavaScript reads as syntax error)
  - Escape quotes: `\\\"` for JS double-quotes, `\\\\\\\"` for JS quotes inside f-strings
  - Avoid nested single quotes (use double quotes for JS strings when outer Python string is single-quoted)
  - Use `'...\\\\n'` (double backslash + n) to produce `\n` in the JS output, which IS valid in browser JavaScript context. Do NOT use raw newlines (`\\n` → `\n` with actual newline chars): that breaks the Python string literal syntax.

- **UnboundLocalError from conditional variable assignment:** If `camera_script` (or any injected script variable) is assigned only inside a Python `if` block (e.g. `if '/biometric/register_kunjungan' in path:`), then any code that references it MUST also be inside that same `if` block. Otherwise, when the condition is False, the reference raises `UnboundLocalError: cannot access local variable 'X' where it is not associated with a value`.
  - **Fix:** Indent the `if '</body>' in html:` injection block INSIDE the camera path condition, not after it. In Python, this means:
    ```python
    if '/biometric/' in path:       # ← outer condition defines camera_script
        camera_script = (
            '<script>...'
        )
        if '</body>' in html:       # ← MUST be inside this block
            html = html.replace(...)
    ```

- **Gunicorn hot-reload:** If the project uses Docker with volume mounts (`volumes: .\\ss_api:/app`) and gunicorn has `reload=True`, changes to `views.py` (or any Python file) are picked up automatically. No Docker restart needed. Wait ~2 seconds for gunicorn to detect the change.

## Verification

- Spinner appears immediately when clicking a link/submit button in the iframe
- Progress bar animates and percentage increases
- On page load complete (onLoad), overlay disappears within 350ms
- No double spinner: only one spinner animation per navigation
- After auto-redirect (Refresh/302), overlay stays visible through the redirect chain
- Flash content (SWF, camera) renders in Ruffle — check the Ruffle logo badge appears on Flash elements

## Reference Files

- `references/sdp-proxy-iframe-2026-06-18.md` — original session detail for iframe loading overlay implementation (SDP SmartServices)
- `references/sdp-flash-camera-2026-06-19.md` — Flash camera investigation: Ruffle injection + HTML5 fallback plan
- `references/sdp-html5-camera-2026-06-19.md` — full HTML5 camera injection implementation + debugging session (this session)
- `references/iframe-alternatives-2026-06-19.md` — comparison of iframe alternatives (HTMX, Fetch, Turbo, SW) for proxied content
- `references/sdp-fingerprint-neurotech-analysis-2026-06-23.md` — SDP fingerprint system analysis: Neurotech ActiveX SDK API (`window.external.*`), enroll/matching flow, template format, scanner compatibility (URU-5000), cross-platform limitations
