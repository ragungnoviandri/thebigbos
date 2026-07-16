# Hermes Agent Dashboard (Development Mode)

The dashboard at `hermes-agent/web/` is a Vite + React admin panel for managing config, API keys, sessions, cron, plugins, profiles, and more. It can run in two modes.

## Mode Comparison

| | Built (production) | Dev (Vite HMR) |
|---|---|---|
| Command | `hermes dashboard --skip-build` | Python backend + `npm run dev` |
| URL | `http://127.0.0.1:9119` | `http://localhost:5173` (Vite) + `:9119` (API proxy) |
| Desktop check | Shows "Desktop boot failed" if no Electron IPC | Works in any browser |
| Live reload | No | Yes |

## Running in Dev Mode (browser-friendly)

The built dashboard (`hermes dashboard`) checks for Electron IPC and shows a "Desktop boot failed" setup screen when accessed standalone in a browser. The Vite dev server does NOT have this check, so it's the recommended way to preview the dashboard outside the desktop app.

**Step 1 — Start the Python backend API server:**

```powershell
# From hermes-agent root:
cd C:\Users\ragun\AppData\Local\hermes\hermes-agent
hermes dashboard --skip-build --no-open --port 9119
```

The `--skip-build` flag skips npm install/build (uses the pre-built `hermes_cli/web_dist/`).

**Step 2 — Start the Vite dev server:**

```powershell
# In a separate terminal:
cd C:\Users\ragun\AppData\Local\hermes\hermes-agent\web
npm install   # only first time
npm run dev
```

The Vite dev server proxies `/api/*` requests to `http://127.0.0.1:9119` (configured in `vite.config.ts`).

**Step 3 — Open the dashboard:**

Open `http://localhost:5173` (not `127.0.0.1:5173` — Vite may refuse connections on the loopback alias).

## Dashboard Sections

| Nav item | Function |
|----------|----------|
| CHAT | Agent chat interface |
| SESSIONS | Session stats + history |
| FILES | File browser |
| MODELS | Model/provider config |
| LOGS | Agent & gateway logs |
| CRON | Scheduled job management |
| SKILLS | Skill browser |
| PLUGINS | Plugin management |
| MCP | MCP server config |
| CHANNELS | Messaging platform status |
| WEBHOOKS | Webhook subscriptions |
| PAIRING | DM authorization |
| PROFILES | Profile switcher |
| CONFIG | Config editor |
| KEYS | API key management |
| SYSTEM | System status & gateway controls |

## Production Use

For always-on chat interface use **Hermes WebUI** (separate repo, NSSM service on port 8787). The dashboard is primarily a desktop-app admin panel and dev tool.
