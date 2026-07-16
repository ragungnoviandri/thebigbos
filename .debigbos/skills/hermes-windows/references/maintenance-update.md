# Hermes Service Maintenance & Update (Concrete — June 2026)

User: ragun, Windows 11, NSSM services for HermesWebUI + HermesGateway.

## Checking for Updates

### Hermes Agent CLI

```powershell
# Check
hermes update --check

# If behind, install:
hermes update
```

**Trap: fork doesn't exist anymore.** If `hermes update` fails with:

```
✗ Failed to fetch updates from origin.
  remote: Repository not found.
```

It means the `origin` remote points to a deleted/renamed fork. Fix by switching `origin` to point at the upstream directly, then remove the now-redundant `upstream` remote.

### Hermes Agent (branch: `main`)

```powershell
cd C:\Users\ragun\AppData\Local\hermes\hermes-agent

# Step 1 — point origin to upstream directly
git remote set-url origin https://github.com/NousResearch/hermes-agent.git

# Step 2 — remove the redundant upstream remote
git remote remove upstream

# Verify
git remote -v
# Should show only: origin → NousResearch/hermes-agent.git

# Now `hermes update` works again
hermes update
```

### WebUI (branch: `master`)

Same pattern — apply to the webui repo too:

```powershell
cd C:\Users\ragun\AppData\Local\hermes\webui

# Change origin from your fork to the upstream project
git remote set-url origin https://github.com/nesquena/hermes-webui.git
git remote remove upstream

# Pull latest
git pull origin master
```

| | Hermes Agent | WebUI |
|---|---|---|
| Upstream | `NousResearch/hermes-agent` | `nesquena/hermes-webui` |
| Default branch | `main` | `master` |

> **Why `set-url` instead of `remove+add`?** `git remote set-url origin <url>` preserves branch tracking and is a single operation. Only use `remove && add` when switching to a completely different remote (e.g. SSH instead of HTTPS).

### WebUI check

```powershell
cd C:\Users\ragun\AppData\Local\hermes\webui

# Check what's available
git fetch upstream

# See how far behind
git rev-list --left-right --count HEAD...upstream/master
# Output: 0   N    → N = commits behind

# Apply updates
git pull upstream master
```

**Trap: `--dry-run` doesn't update remote tracking refs.** If you run `git fetch --dry-run` first (to see what's available without actually fetching), the remote-tracking branches (`upstream/master`) are NOT updated. Subsequent `git rev-list --count HEAD...upstream/master` shows `0  0` even when new commits exist, because `upstream/master` still points to the old commit you already have. Always use a real `git fetch upstream` (no `--dry-run`) before comparing commit counts.

## Restarting Services After Update

```powershell
C:\Users\ragun\AppData\Local\Microsoft\WinGet\Links\nssm.exe restart HermesWebUI
C:\Users\ragun\AppData\Local\Microsoft\WinGet\Links\nssm.exe restart HermesGateway
```

Or from Services.msc (services.msc → find HermesWebUI / HermesGateway → Restart).

## Verifying Both Services

```powershell
Get-Service HermesWebUI, HermesGateway | Format-Table Name, Status, StartType
```

Expected output:

```
Name           Status  StartType
----           ------  ---------
HermesGateway  Running Automatic
HermesWebUI    Running Automatic
```

## Traps & Recovery

### "Marked for deletion" — SCM stalls

When you `sc.exe delete` a service and immediately try to recreate it, the SCM may respond:

```
The specified service has been marked for deletion.
```

This means the previous delete hasn't finalized internally. The SCM holds the service name in a pending-delete state and refuses any operation (create, config, query).

**Recovery options (in order of preference):**

1. **Wait 30–120 seconds** — the SCM finalizes deletion asynchronously. Use a loop to wait:
   ```powershell
   while ((sc.exe query HermesWebUI 2>&1 | Out-String) -notmatch 'FAILED 1060') {
       Write-Host "Waiting for deletion to finalize..."
       Start-Sleep -Seconds 5
   }
   ```

2. **Reboot** — always works, guaranteed to clear the marker.

3. **Use a different service name temporarily** — NSSM creates the new service with a different SCM name and you can rename it later (not recommended).

**Prevention:** Before creating a new NSSM service, always clean up first:

```powershell
& sc.exe stop "HermesWebUI" 2>$null | Out-Null
Start-Sleep -Seconds 3
& sc.exe delete "HermesWebUI" 2>$null | Out-Null
Start-Sleep -Seconds 5

# Verify it's gone
$q = & sc.exe query "HermesWebUI" 2>&1 | Out-String
if ($q -match 'FAILED 1060') { Write-Host "Clean — safe to create" }
```

### NSSM silent failure (error 1053 not possible)

NSSM never produces error 1053 because it handles `StartServiceCtrlDispatcher` internally. If NSSM creates the service but it stays in SERVICE_PAUSED or SERVICE_STOPPED, check:

```powershell
# Check logs
Get-Content C:\Users\ragun\AppData\Local\hermes\logs\webui-stderr.log -Tail 20
Get-Content C:\Users\ragun\AppData\Local\hermes\logs\webui-stdout.log -Tail 20

# Or for gateway
Get-Content C:\Users\ragun\AppData\Local\hermes\logs\gateway-stderr.log -Tail 20
```

Common causes:
- `HERMES_WEBUI_PYTHON` not set (start.ps1 checks PATH first, fails before venv fallback for LocalSystem)
- Wrapper script path wrong (backslash escaping in NSSM parameters)
- Python virtual environment missing

## Hermes NSSM Commands Cheatsheet

```powershell
# NSSM path (from winget install)
$nssm = "C:\Users\ragun\AppData\Local\Microsoft\WinGet\Links\nssm.exe"

# Start / Stop / Restart / Status
& $nssm start HermesWebUI
& $nssm stop HermesWebUI
& $nssm restart HermesWebUI
& $nssm status HermesWebUI

# Config read
& $nssm get HermesWebUI AppParameters
& $nssm get HermesWebUI AppDirectory
& $nssm get HermesWebUI AppStdout

# Windows native equivalents
Get-Service HermesWebUI | Format-List *
sc.exe query HermesWebUI
sc.exe qc HermesWebUI
```
