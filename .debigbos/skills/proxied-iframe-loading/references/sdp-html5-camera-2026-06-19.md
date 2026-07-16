# SDP HTML5 Camera Injection — Session Detail (2026-06-19)

## Goal
Replace Adobe Flash ScriptCam (swfobject + scriptcam.swf) with HTML5 getUserMedia-based camera on a Django-proxied legacy SDP biometric page (`/biometric/register_kunjungan`).

## Target Page Structure
- Page URL (proxy): `/sdp-proxy/biometric/register_kunjungan`
- Original SDP: `http://172.27.225.9/sdp/biometrickunjungan/register_kunjungan`
- Flash camera uses three overridable JS functions:
  - `register_accordion_foto_camera_on()` — initializes ScriptCam SWF
  - `register_accordion_foto_camera_off()` — stops camera
  - `register_accordion_foto_camera_capture()` — gets base64 frame
- Camera container: `<div id="register_dialog_foto_webcam_container">`
- Photo data variable: `_enrolled_foto_images_base64[foto_type]`
- Photo thumbnail: `#register_accordion_foto_list_{foto_type}`
- Camera dropdown: `#register_accordion_foto_webcam_id`
- Trigger button: `#register_accordion_foto_btn_scan`
- Page also loads: `swfobject.js`, `scriptcam.min.js` (jQuery ScriptCam plugin)

## The Three Override Functions

### camera_on() → getUserMedia
```javascript
register_accordion_foto_camera_on = function() {
    if(window._html5_stream) {
        window._html5_stream.getTracks().forEach(function(t){t.stop()});
        window._html5_stream = null;
    }
    _html5_start_camera(null);
    _html5_populate_cameras();
};
```

### camera_off() → stop tracks
```javascript
register_accordion_foto_camera_off = function() {
    if(window._html5_stream) {
        window._html5_stream.getTracks().forEach(function(t){t.stop()});
        window._html5_stream = null;
    }
    if(window._html5_video && window._html5_video.remove) window._html5_video.remove();
    window._html5_video = null;
};
```

### camera_capture() → canvas to base64
```javascript
register_accordion_foto_camera_capture = function() {
    var ft = jQuery("#register_accordion_foto_type").val();
    if(!window._html5_video || !window._html5_canvas) return;
    window._html5_canvas.getContext("2d").drawImage(window._html5_video, 0, 0, 320, 240);
    var b64 = window._html5_canvas.toDataURL("image/jpeg", 0.85).split(",")[1];
    _enrolled_foto_images_url[ft] = null;
    _enrolled_foto_images_base64[ft] = b64;
    jQuery("#register_accordion_foto_list_"+ft).attr("src", "data:image/jpg;base64,"+b64);
};
```

## SWF/Ruffle Cleanup
Added at the very start of the camera injection script to prevent Ruffle from loading/erroring:
```javascript
var _html5_swf_cleanup = function() {
    try {
        var x = document.querySelectorAll("embed,object");
        for(var i=0; i<x.length; i++) {
            if(x[i].type && x[i].type.indexOf("flash") >= 0) x[i].remove();
        }
        if(window.RufflePlayer && window.RufflePlayer.stop) window.RufflePlayer.stop();
    } catch(e) {}
};
_html5_swf_cleanup();
```

## Escaping Chain Debugging Record

### The Bug
JS error: `Uncaught SyntaxError: missing ) after argument list (at register_kunjungan:3878:14)`

### Root Cause
JavaScript string had unescaped double quotes:
```javascript
c.html("<p style="color:red;...">")  // " after style= closes the JS string!
```

### Why It Happened
The Python source had `\"` (one backslash + double-quote):
```python
'  c.html("<p style=\"color:red;...\">"\\n'
```

But Python recognizes `\"` as a valid escape that produces just `"` (silently consuming the backslash).

### The Fix
Use `\\"` (two backslashes + double-quote) in the Python source:
```python
'  c.html("<p style=\\"color:red;...\\">"\\n'
```

But from `execute_code`'s triple-quoted string, use 4 backslashes:
```python
"""'  c.html("<p style=\\\\"color:red;...\\\\">"\\n'"""
```

### Verification Method
Use `exec()` to evaluate the camera_script Python code, then check with `repr()`:
```python
ns = {}
exec("camera_script = ('...')", ns)
cs = ns["camera_script"]
print(repr(cs[:200]))  # verify \" is present
```

And check raw file bytes:
```python
with open("views.py", "rb") as f:
    raw = f.read()
c_idx = raw.find(b'c.html')
line_bytes = raw[raw.rfind(b'\n',0,c_idx)+1:raw.find(b'\n',c_idx)]
# Need 5c 22 (not just 22) for escaped quotes
```

## Line Number Calculation
When injecting ~92 lines of camera script at the original `</body>` line:
```
Error line 3878 - original body line 3844 = offset 34 (0-indexed)
→ camera_script.split("\\n")[34] = the c.html line
```

This confirmed the error was at the `c.html()` call with bad escaping.

## Full Injected Camera Script
The final script (92 lines before `</body>`):
1. `<script>` (opening tag)
2. SWF cleanup function + execution
3. Override guard (`_html5_override_loaded` flag)
4. Variable initializations (stream, video, canvas, devices)
5. `_html5_start_camera(deviceId)` — creates video/canvas, starts getUserMedia
6. `_html5_populate_cameras()` — enumerates devices, fills dropdown
7. `register_accordion_foto_camera_on` override
8. `register_accordion_foto_camera_off` override
9. `register_accordion_foto_camera_capture` override
10. Camera selection change handler (jQuery chain)
11. `</script>` (closing tag)

## Injection Order
In `proxy_full()` views.py:
1. Ruffle injection (at `</head>`)
2. Camera injection (at `</body>`, inside `if '/biometric/'` block)
3. Nav injection (at new `</body>` — replaces the one from step 2)

Important: The `if '</body>' in html:` for camera injection MUST be INSIDE the `if '/biometric/'` block to avoid `UnboundLocalError: cannot access local variable 'camera_script'`.
