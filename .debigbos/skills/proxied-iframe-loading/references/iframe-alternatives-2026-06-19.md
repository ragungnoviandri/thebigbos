# Iframe Alternatives for Proxied Content

When embedding third-party/internal legacy content, consider these alternatives to iframes. Each has trade-offs for JS execution, navigation detection, and loading states.

## Comparison Table

| Method | Click-to-Load | End-Load | JS Runs? | Form Submit? | Best For |
|--------|---------------|----------|----------|-------------|----------|
| **A. Iframe + proxy inject** | ✅ postMessage | ✅ onLoad parity | ✅ Full | ✅ Full | Full legacy apps (SDP, Flash) |
| **B. HTMX (`hx-trigger`)** | ✅ native | ✅ (`htmx:afterSwap`) | ⚠️ Partial | ✅ (if forms use hx-*) | Content fragments, static pages |
| **C. Fetch + innerHTML** | ✅ onclick | ✅ Promise `.then()` | ❌ No script exec | ❌ No | Read-only content, API data |
| **D. Turbo Frames** | ✅ native | ✅ (`turbo:frame-load`) | ⚠️ Partial | ✅ (Turbo Drive) | Rails apps with Turbo |
| **E. Service Worker proxy** | ✅ can intercept | ✅ lifecycle events | ✅ Full | ✅ Full | Progressive Web Apps |
| **F. `<object>` / `<embed>`** | ❌ not exposed | ⚠️ onLoad | ✅ Full | ❌ No form interop | Legacy plugin content (PDF, etc.) |
| **G. Web Component + Shadow DOM** | ✅ custom event | ✅ callback | ❌ No script exec | ❌ No | Sandboxed static widgets |

## When to Use Each

### A. Iframe + Proxy (Recommended for full apps)
- The only solution that fully supports legacy JS apps (forms, redirects, Flash fallbacks)
- Requires proxy middleware (Django, nginx, etc.) to rewrite URLs and inject scripts
- Navigation detection via injected postMessage + even/odd onLoad fallback

### B. HTMX
- Ideal when proxying HTML *fragments* (not full pages)
- Built-in loading indicators: just add class `htmx-indicator`
- Trigger pattern: `hx-trigger="load"` or `hx-trigger="click"` with `hx-target`
- Pro: simple, no iframe cross-origin issues
- Con: embedded `<script>` tags in HTMX responses are NOT executed

### C. Fetch + innerHTML
- Simplest approach: fetch via proxy endpoint, set `element.innerHTML = html`
- Full control over loading states (start/finish/error)
- `<script>` tags in fetched HTML DO NOT execute when inserted via innerHTML
- Workaround: manually extract and eval scripts (security risk)
- Best for: API responses, static content, markdown rendering

### D. Turbo Frames (Hotwire)
- From Ruby on Rails, but works standalone
- Frame-scoped navigation with automatic loading states
- Requires the target page to have `<turbo-frame>` tags
- Not suitable for legacy apps with no Turbo integration

### E. Service Worker
- Most powerful but highest complexity
- Can intercept all fetch requests from a scope
- Show custom loading UI during navigation
- Can communicate with the page via postMessage
- Overkill for simple iframe loading; use only for full PWA experiences

### F. `<object>` / `<embed>`
- Similar to iframe but for plugins (PDF, Flash, Java)
- No built-in JS navigation detection
- Browser support varies for plugin content
- Not recommended for HTML content

### G. Web Component + Shadow DOM
- Create a custom `<proxied-content>` element
- Fetch content via proxy, render in Shadow DOM (style isolation)
- `<script>` tags inside shadow DOM DO execute in global scope
- But scripts attached to shadow DOM elements may break
- Best for: isolated widgets with style encapsulation

## Recommendation
For the SDP SmartServices case (full legacy app with forms, redirects, session, Flash), **Option A (Iframe + Proxy)** is the only workable solution. All other options fail because they cannot execute the page's JavaScript fully (form submit, Flash/ScriptCam, redirect handling).
