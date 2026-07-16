# SDP Proxy Iframe Loading Overlay — Session Details (2026-06-18)

## Context

Project: SmartServices (RUTAN Jakarta Pusat)
- Django backend at `ss_api/sdp_proxy/views.py` — full proxy (`proxy_full`) that rewrites SDP HTML
- React frontend at `ss_app/src/pages/antrian/` — iframe embedding SDP via `/sdp-proxy/` prefix
- SDP uses Refresh header (meta refresh) instead of 302 for login redirect

## The Fix Chain

### 1. Proxy injected script (views.py)

Injected before `</body>` in HTML responses. Detects:
- `form submit` — user clicks Login button
- `link click` — user navigates via anchor tag
- `beforeunload` — backup for unload-triggered navigations

**Code location:** `ss_api/sdp_proxy/views.py` lines 210-222 (after URL rewriting)

### 2. React overlay component (SdpIframe.js)

**File:** `ss_app/src/pages/antrian/SdpIframe.js`

Key state:
- `loading` (bool) — overlay visible/hidden
- `progress` (0-88%) — simulated progress
- `loadCount` (ref) — tracks onLoad parity for even/odd logic
- `timerRef` (ref) — simulation interval
- `hideRef` (ref) — safety timeout
- `navMsg` (ref) — true when postMessage triggered, prevents double progress restart

### 3. AntrianLoketPage integration

**File:** `ss_app/src/pages/antrian/AntrianLoketPage.js`
- Import `SdpIframe` from `'./SdpIframe'`
- Replace `<iframe src="...">` with `<SdpIframe />`

## Timeline of Fixes (user feedback → fix)

1. "g bisa tiap si iframe reload" → added even/odd onLoad counter
2. "bukan itu sih... pas halaman iframe load halaman setelah klik itu yg suka telat" → realized issue is subsequent navigations, not initial → injected postMessage via proxy
3. "msh telat... g bisa detect pas ngeload halaman ya di iframe...?" → proxy injection was the answer
4. "terlalu lama big... coba g usah nunggu" → removed 200ms iframe delay + 1.5s min overlay time
5. "kok spinnernya keluar 2x" → added navMsg ref to skip progress restart when postMessage already triggered

## Key Insight

The `beforeunload` event in the injected script does NOT fire for:
- `Refresh` header redirects (meta refresh) — handled by even/odd fallback
- Server-side 302 redirects — handled by even/odd fallback

So the even/odd fallback is NOT optional — it's the only mechanism that handles auto-redirects.

## Files Modified

| File | Change |
|------|--------|
| `ss_api/sdp_proxy/views.py` | Injected `<script>` before `</body>` for postMessage |
| `ss_app/src/pages/antrian/SdpIframe.js` | NEW — React component with overlay + message listener |
| `ss_app/src/pages/antrian/AntrianLoketPage.js` | Import SdpIframe, replace raw iframe |
