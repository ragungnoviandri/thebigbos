---
name: hermes-webui-maintenance
description: "Update Hermes WebUI from upstream, handle custom files outside repo, manage NSSM service on Windows."
version: 1.0.0
author: Hermes Agent
platforms: [windows]
metadata:
  hermes:
    tags: [hermes, webui, nssm, windows, service, git]
    related_skills: [github-repo-management, hermes-agent]
---

# Hermes WebUI Maintenance

Update the Hermes WebUI from upstream, handle custom wrapper scripts safely (outside git repo), and restart the NSSM Windows service.

## Prerequisites

- WebUI cloned at `%LOCALAPPDATA%\hermes\webui`
- NSSM service `HermesWebUI` already installed (see `references/nssm-service-setup.md`)

## Quick Update Flow

```bash
# 1. Go to webui dir
cd /c/Users/ragun/AppData/Local/hermes/webui

# 2. Fetch latest
git fetch origin

# 3. Check how far behind
git rev-list --left-right --count HEAD...origin/master

# 4. Check for local modifications (custom files!)
git status --short

# 5. Reset local changes — BUT warn user custom files will be deleted!
git checkout -- .
```

## ⚠️ CRITICAL: Custom Files Outside Repo

**The `start-service.ps1` and similar wrapper files are custom — NOT part of the upstream repo.** They get deleted by `git clean -fd`, breaking the NSSM service.

**Rule:** Keep all custom service wrapper scripts in `gateway-service\` directory:

```
%LOCALAPPDATA%\hermes\
├── gateway-service\          ← Custom scripts live here (safe from git clean)
│   ├── gateway-service.ps1   ← HermesGateway wrapper
│   ├── webui-service.ps1     ← HermesWebUI wrapper (env vars + call start.ps1)
│   └── setup-gateway-service.ps1
├── webui\                    ← Git repo (gets cleaned)
│   ├── start.ps1             ← Part of upstream repo (safe)
│   └── start-service.ps1     ← ❌ CUSTOM — will be deleted!
└── logs\
```

### webui-service.ps1 Template

**⚠️ CRITICAL: Must set `HERMES_WEBUI_PYTHON`.** When running as LocalSystem, the `PATH` env var doesn't include user Python installs, so `start.ps1`'s `Get-Command python3, python, py` fails and the script exits with `Write-Error` before reaching the venv fallback. `HERMES_WEBUI_PYTHON` bypasses the PATH check entirely.

```powershell
<#
.SYNOPSIS
    Hermes WebUI service wrapper - sets env vars then launches start.ps1
    Used by HermesWebUI Windows service (running as LocalSystem)
    Lives outside webui/ repo to survive git clean
#>

$env:HERMES_WEBUI_AGENT_DIR  = 'C:\Users\ragun\AppData\Local\hermes\hermes-agent'
$env:HERMES_HOME             = 'C:\Users\ragun\AppData\Local\hermes'
$env:HERMES_WEBUI_STATE_DIR  = 'C:\Users\ragun\AppData\Local\hermes\webui'
$env:HERMES_WEBUI_PYTHON     = 'C:\Users\ragun\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe'
$env:VIRTUAL_ENV             = 'C:\Users\ragun\AppData\Local\hermes\hermes-agent\venv'

& 'C:\Users\ragun\AppData\Local\hermes\webui\start.ps1'
```

## Update Steps

1. **Fetch** — `git fetch origin`
2. **Check commits behind** — `git rev-list --left-right --count HEAD...origin/master`
3. **Show changelog highlights** — `git log --oneline HEAD..origin/master`
4. **Ask user** before pulling
5. **Discard local changes** — `git checkout -- .` (⚠️ warn about custom files)
6. **Clean untracked** — `git clean -fd` (⚠️ user MUST approve — deletes files)
7. **Pull** — `git pull origin master`
8. **Recreate custom files** if they were deleted
9. **Update NSSM service path** if script location changed
10. **Restart service** — `nssm restart HermesWebUI` (admin required)

## NSSM Service Commands

```powershell
# View current config (registry)
reg query "HKLM\SYSTEM\CurrentControlSet\Services\HermesWebUI\Parameters"

# Update AppParameters to new script path
nssm set HermesWebUI AppParameters "-NoProfile -ExecutionPolicy Bypass -File `"C:\Users\ragun\AppData\Local\hermes\gateway-service\webui-service.ps1`""

# Service lifecycle
nssm start HermesWebUI
nssm stop HermesWebUI
nssm restart HermesWebUI
nssm status HermesWebUI
nssm dump HermesWebUI
```

## Diagnostics: Service Won't Start

### Service is PAUSED

If `nssm start HermesWebUI` returns `Unexpected status SERVICE_PAUSED in response to START control`:

```powershell
# 1. Stop first
nssm stop HermesWebUI

# 2. Then start
nssm start HermesWebUI

# Or use sc.exe
sc.exe continue HermesWebUI   # Resume from paused
sc.exe stop HermesWebUI       # Then full stop
sc.exe start HermesWebUI      # Then fresh start
```

The PAUSED state happens when NSSM is in an inconsistent state (e.g., service was manually paused, or a previous start attempt left it mid-cycle). NSSM's `restart` can also trigger it — always `stop` then `start` explicitly.

### Service Starts Then Stops

Check `%LOCALAPPDATA%\hermes\logs\webui-stderr.log`:

```
Python 3 is required to run server.py (set HERMES_WEBUI_PYTHON or add python to PATH).
```

**Root cause:** Service runs as LocalSystem, which doesn't have user-level PATH entries for Python. The `start.ps1` script checks `Get-Command python3, python, py` (line ~85) before reaching the venv fallback (line ~133), and exits early with `Write-Error`.

**Fix:** Add `$env:HERMES_WEBUI_PYTHON` to the wrapper script (see Template above). This bypasses the `Get-Command` PATH check entirely and jumps straight to the venv Python.

### Service Shows "Running" but WebUI Not Accessible

Check the stdout log for actual bootstrap output:
```bash
tail -50 /c/Users/ragun/AppData/Local/hermes/logs/webui-stdout.log
```

If the log is 0 bytes or very small, the script may be failing before Python starts. Common causes:
- `HERMES_WEBUI_PYTHON` not set or pointing to non-existent file
- Python import error in bootstrap.py (check full log rotation files)
- Port conflict (8787 already in use)

## Pitfalls

- **`git clean -fd` deletes untracked files** — `start-service.ps1`, `settings.json`, `sessions/`, `logs/`, etc. Always `git status --short` first to show user what'll be lost.
- **CRLF warnings** — `LF will be replaced by CRLF` on Windows. Cosmetic, ignore.
- **Service restart needs admin** — the `nssm` command requires Administrator prompt. Use `Start-Process powershell -Verb RunAs` or tell user to run in admin shell.
- **NSSM "Can't open service"** — means you're not admin. The `nssm dump`, `nssm get`, `nssm set` commands all require elevation.
- **`nssm restart` can produce SERVICE_PAUSED** — prefer explicit `nssm stop` then `nssm start` instead of `nssm restart`.
- **`-Verb RunAs` output doesn't propagate** — `Start-Process -Verb RunAs` launches an elevated window whose stdout/stderr doesn't return to the calling process. Use it only for fire-and-forget commands, or capture output by redirecting to a log file in the elevated script.
- **`reg query` works without admin** — but `nssm set` requires admin. Great for quick config checks from non-elevated terminals.
