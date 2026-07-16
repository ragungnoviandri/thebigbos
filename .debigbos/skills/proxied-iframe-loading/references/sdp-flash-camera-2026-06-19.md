# SDP Flash Camera Investigation — Session Detail (2026-06-19)

## Problem

Halaman `/biometric/register_kunjungan` di SDP menggunakan **Adobe Flash** untuk mengakses webcam guna foto pengunjung. Flash Player di-block oleh semua browser modern (EOL 31 Dec 2020).

Error: "You need Adobe Flash Player 11.7 to use this software."

## Page Architecture Analysis

The camera page uses **ScriptCam** — a jQuery plugin that wraps `swfobject.js` to embed a Flash SWF for webcam access.

### Key Page Elements

| Element | ID | Purpose |
|---------|----|---------|
| Camera container | `register_dialog_foto_webcam_container` | 322×242px container for Flash/video |
| Flash placeholder | `register_dialog_foto_webcam_show` | Div where ScriptCam injects Flash `<object>` |
| Camera select | `register_accordion_foto_webcam_id` | Dropdown for camera selection |
| Photo type | `register_accordion_foto_type` | "fotopengunjung" or "ktppengunjung" |
| Capture button | `register_accordion_foto_btn_scan` | "Ambil Foto" button |
| Thumbnail | `register_accordion_foto_list_<type>` | `<img>` showing captured photo preview |

### JavaScript Functions

```javascript
// Flash-based camera initialization
function register_accordion_foto_camera_on() {
    jQuery('#register_dialog_foto_webcam_container').append(
        jQuery('<div>', {id: 'register_dialog_foto_webcam_show'})
    );
    jQuery('#register_dialog_foto_webcam_show').scriptcam({
        path: 'http://172.27.225.9/sdp/public/webcam/',
        width: 320,
        height: 240,
        onWebcamReady: function() {
            // populate camera dropdown, enable button
        },
        onPictureAsBase64: function(data) {
            // callback after capture (not used; capture is sync via getFrameAsBase64)
        }
    });
}

function register_accordion_foto_camera_off() {
    // ScriptCam cleanup
}

function register_accordion_foto_camera_capture() {
    var ft = jQuery('#register_accordion_foto_type').val();
    var b64 = jQuery.scriptcam.getFrameAsBase64();
    _enrolled_foto_images_base64[ft] = b64;
    jQuery('#register_accordion_foto_list_' + ft).attr('src', 'data:image/jpg;base64,' + b64);
}
```

### Data Submission

- Form action: `http://172.27.225.9/sdp/biometrickunjungan/register_submit`
- Photo data sent as: `data['FOTO_IMAGES[fotopengunjung]'] = base64string`
- Already via AJAX from SDP's own JS (jQuery.ajax, cross-origin, CORS: `Access-Control-Allow-Origin: *`)
- Form POST fields: `register_nama`, `register_alamat`, `register_tujuan`, FOTO fields, etc.

## Solution Attempted: Ruffle Injection (Failed)

### What was done

Modified `ss_api/sdp_proxy/views.py` to inject Ruffle Flash emulator into all proxied HTML. Injected **before `</head>`** so Ruffle intercepts `<object>`/`<embed>` tags early.

### Result

Ruffle logo appeared in the camera box, but camera feed stayed white. Camera dropdown remained empty. **Ruffle's `flash.media.Camera` API does not support ScriptCam's camera access pattern.**

## Final Solution: HTML5 Camera Injection

### Approach

In the proxy, detect the biometric page path and inject a script that **overrides** the three camera functions with HTML5 equivalents BEFORE the user opens the camera dialog.

### Escaping Chain (Critical)

The Python string literal `'<script>\n'` — note the `\n` inside single quotes — is interpreted by Python as an actual newline character. So the generated HTML has real line breaks. This is the correct behavior for multi-line JS output.

If you instead write `'<script>\\n'` (double backslash + n), Python preserves the backslash character, and the output has `\n` (backslash + n text) in the HTML. This keeps everything on one line.

**The `\\n` vs `\n` distinction matters** because Python recognizes `\n` as the newline escape in ALL string contexts (single, double, triple-quoted).

For JavaScript-quote escaping: to produce `\"` in the JavaScript output (escaped double-quote inside a JS string), the Python source must have `\\"` (two backslashes + double-quote). A single backslash `\"` in Python `'...'` is interpreted as an unrecognized escape — Python 3.12+ warns, and the result is just `"` (the backslash is consumed).

**Practical rule:** When you need the JS to see `\"`, use `\\"` in the Python source. When you need the JS to see a newline, use `\n` in the Python source.

### Implementation Pattern

```python
# Path-specific injection — must be INSIDE the `if "text/html"` and path-conditional blocks
if '/biometric/register_kunjungan' in path or '/register_kunjungan' in path:
    camera_script = (
        '<script>\n'
        '/* SWF cleanup — remove Flash embeds so Ruffle does not interfere */\n'
        'var _html5_swf_cleanup=function(){try{var x=document.querySelectorAll("embed,object");'
        'for(var i=0;i<x.length;i++){if(x[i].type&&x[i].type.indexOf("flash")>=0)x[i].remove()}'
        'if(window.RufflePlayer&&window.RufflePlayer.stop)window.RufflePlayer.stop()}catch(e){}};\n'
        '_html5_swf_cleanup();\n'
        '/* HTML5 Camera Override */\n'
        'if(typeof window._html5_override_loaded==="undefined"){\n'
        'window._html5_override_loaded=true;\n'
        'window._orig_camera_on=register_accordion_foto_camera_on;\n'
        'window._orig_camera_off=register_accordion_foto_camera_off;\n'
        '}\n'
        '...\n'
        '</script>\n'
    )
    # THE if/elif/else MUST be inside the path-condition block!
    if '</body>' in html:
        html = html.replace('</body>', camera_script + '</body>')
    elif '</html>' in html:
        html = html.replace('</html>', camera_script + '</html>')
    else:
        html += camera_script
```

> See the SKILL.md for the complete override JavaScript and Section 1e for the full escaping chain analysis.

### Key Design Decisions

1. **Inject before `</body>`, not `</head>`** — script must override function definitions that the page already declared.
2. **Preserve original functions** — `_orig_camera_on/off` saved for potential fallback.
3. **Use same variable names** — `_enrolled_foto_images_base64[foto_type]` is the same variable the form submit JS reads.
4. **Camera switching via rebind** — replace `jQuery.scriptcam.changeCamera()` with `document.off().on()` binding that stops old stream and starts new one with selected `deviceId`.
5. **Canvas at 320×240** — matches the Flash SWF dimensions exactly.
6. **SWF cleanup at top** — runs before any camera code to remove lingering Flash embeds and stop Ruffle.

### Gotchas from Implementation

#### Gunicorn reload (Docker volume)
- Volume mount `.\\ss_api:/app` + `reload=True` in gunicorn → changes to `views.py` auto-picked up.
- Syntax errors log to `docker logs api --tail 20`.
- No container restart needed.

#### Python indentation
- EVERY line must have consistent indentation. Single wrong space causes `IndentationError`.
- Empty lines must either have proper indent or be truly blank (no whitespace).
- The `if '</body>'` injection block MUST be inside the path-condition block, not after it, or you get `UnboundLocalError`.

#### JS comments in Python strings
- Use `//` for JS comments, NOT `#` (which causes JS syntax error).

#### Browser permission timing
- `getUserMedia()` is async. Don't enable the capture button before the stream flows.
- Use `{video: true, audio: false}` to request only video.
- The `with` statement works in non-strict-mode pages (like legacy SDP). Use explicit assignment if the page uses `"use strict"`.
