# NSSM HermesWebUI Service Setup

## Architecture

The HermesWebUI runs as a Windows service via **NSSM** (Non-Sucking Service Manager).

### Service Chain

```
NSSM (HermesWebUI service, LocalSystem)
  └── powershell.exe -File webui-service.ps1
        └── sets env vars:
              HERMES_WEBUI_AGENT_DIR  → agent install dir
              HERMES_HOME             → hermes data root
              HERMES_WEBUI_STATE_DIR  → webui state dir
              HERMES_WEBUI_PYTHON     → venv python.exe (CRITICAL for LocalSystem!)
              VIRTUAL_ENV             → venv dir
              └── calls start.ps1
                    └── python bootstrap.py (starts web server on port 8787)
```

### Why Two Wrapper Scripts?

- **`start.ps1`** — part of the upstream webui repo. Gets updated with git pulls.
- **`webui-service.ps1`** (in `gateway-service\`) — **custom wrapper**. Sets env vars for LocalSystem context (which doesn't have `ragun`'s user env vars), then calls `start.ps1`. Lives **outside** the repo so `git clean -fd` doesn't delete it.

## NSSM Configuration (Registry)

The NSSM config lives in the registry:

```
HKLM\SYSTEM\CurrentControlSet\Services\HermesWebUI\Parameters
    Application       → C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
    AppParameters     → -NoProfile -ExecutionPolicy Bypass -File "C:\...\gateway-service\webui-service.ps1"
    AppDirectory      → C:\Users\ragun\AppData\Local\hermes\webui
    AppNoConsole      → 1 (no window)
    AppStdout         → C:\Users\ragun\AppData\Local\hermes\logs\webui-stdout.log
    AppStderr         → C:\Users\ragun\AppData\Local\hermes\logs\webui-stderr.log
    AppRotateFiles    → 1
    AppRotateBytes    → 5242880 (5MB)
    AppThrottle       → 5000
```

## NSSM Installation

NSSM is installed via winget:

```powershell
winget install NSSM.NSSM --accept-source-agreements --accept-package-agreements
```

Installed at: `C:\Users\ragun\AppData\Local\Microsoft\WinGet\Links\nssm.exe`

## Common Commands

```powershell
# Status
nssm status HermesWebUI

# Dump full config
nssm dump HermesWebUI

# Edit parameters
nssm set HermesWebUI AppParameters "-NoProfile -ExecutionPolicy Bypass -File `"C:\...\gateway-service\webui-service.ps1`""
nssm set HermesWebUI AppDirectory "C:\...\webui"

# Service lifecycle — prefer explicit stop then start over restart!
nssm stop HermesWebUI
nssm start HermesWebUI
```

## Troubleshooting

- **NSSM "Can't open service!"** — Run PowerShell as Administrator.
- **Service starts but stops immediately** — Check logs at `%LOCALAPPDATA%\hermes\logs\webui-stderr.log`. Most common cause: `HERMES_WEBUI_PYTHON` not set (LocalSystem can't find Python in PATH). See main SKILL.md template.
- **`start-service.ps1` deleted** — Happens after `git clean -fd`. Recreate from the template in the main SKILL.md at `gateway-service\webui-service.ps1`.
- **"Access is denied"** — `reg query` to registry works without admin; `nssm set` requires admin.
- **SERVICE_PAUSED on restart** — NSSM's `restart` can produce `Unexpected status SERVICE_PAUSED`. Always use explicit `nssm stop` then `nssm start` instead.
- **`-Verb RunAs` output invisible** — `Start-Process -Verb RunAs` shows a UAC popup but stdout/stderr don't return to the caller. Log output inside the elevated script to a file if you need to read it.
- **Python not found after git pull** — Upstream changes to `start.ps1` or `bootstrap.py` can't affect LocalSystem PATH resolution. The fix is always in the wrapper's `HERMES_WEBUI_PYTHON` env var, never in the repo code.
