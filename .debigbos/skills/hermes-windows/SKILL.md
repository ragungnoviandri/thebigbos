---
name: hermes-windows
description: "Configure, run, and troubleshoot Hermes Agent on Windows — gateway service startup, timezone, scheduled tasks, and platform-specific quirks."
version: 1.0.0
author: Hermes Agent
platforms: [windows]
metadata:
  hermes:
    tags: [hermes, windows, gateway, scheduled-task, configuration]
    related_skills: [hermes-agent]
---

# Hermes Agent on Windows

Windows-specific guidance for running Hermes Agent, especially the messaging gateway as a background service.

## Timezone

Hermes expects IANA timezone format (e.g., `Asia/Jakarta`, `America/New_York`), **not** bare city names (`Jakarta`, `New_York`).

```bash
# ✅ Correct
hermes config set timezone "Asia/Jakarta"

# ❌ Wrong — triggers: 'No time zone found with key Jakarta'
hermes config set timezone "Jakarta"
```

Verify:
```bash
grep timezone ~/.hermes/config.yaml
```

## Gateway Allowlist

By default, the gateway denies all unauthorized users. You'll see:
```
WARNING gateway.run: No user allowlists configured. All unauthorized users will be denied.
```

**For development** (open access):
```bash
# In ~/.hermes/.env:
GATEWAY_ALLOW_ALL_USERS=true
```

**For production** (restrict to specific users):
```bash
TELEGRAM_ALLOWED_USERS=123456789,987654321
DISCORD_ALLOWED_USERS=user_id_here
# etc.
```

## Running Gateway Without a CMD/Console Window

When the gateway runs as a Scheduled Task on Windows, the `.cmd` wrapper creates a visible console window. To hide it:

### Method: VBScript Launcher + pythonw.exe

**Step 1 — Create a VBScript that sets env vars and launches pythonw.exe:**

See `templates/gateway-vbs-launcher.vbs` for the template. Customize paths to match `HERMES_HOME`.

Key points:
- Use `WshShell.Environment("PROCESS")` to set env vars (HERMES_HOME, PYTHONIOENCODING, HERMES_GATEWAY_DETACHED)
- `WshShell.Run(cmd, 0, False)` — the `0` means **hidden window**
- `pythonw.exe` (not `python.exe`) — no console window by default

**Step 2 — Update the .cmd wrapper to launch VBScript with `start /min /b`:**

```batch
@echo off
rem Launch gateway silently via VBScript (no cmd window)
start /min /b wscript.exe "C:\path\to\gateway-service\Hermes_Gateway_hidden.vbs"
exit /b
```

The `start /min /b` minimizes the temporary CMD window, and it exits immediately after spawning `wscript.exe`.

### Why this works

```
Scheduled Task → Hermes_Gateway.cmd → start /min /b (minimized)
  → wscript.exe (no window) → VBScript → pythonw.exe (no window) → gateway runs
```

Three layers of window suppression:
1. `.cmd` wrapper uses `start /min /b` — minimised, exits fast
2. `wscript.exe` — never creates a console window
3. `pythonw.exe` — Python's windowless executable

## Scheduled Task Management via MSYS/Git Bash

On Windows, `schtasks` uses `/` as a flag prefix. In git-bash/MSYS, forward slashes get mangled by path translation. Use **double forward slash** `//` instead:

```bash
# ✅ In git-bash (MSYS):
schtasks //query //tn "Hermes_Gateway" //v //fo list
schtasks //change //tn "Hermes_Gateway" //tr "..."
schtasks //delete //tn "Hermes_Gateway" //f

# ❌ Wrong in git-bash:
schtasks /query /tn "Hermes_Gateway"    # → path translation breaks it
```

## Hiding PowerShell/.cmd Windows for Scheduled Tasks

On Windows, Scheduled Tasks running `.cmd` files or `powershell.exe` create a visible console window at login. To suppress it completely, use a VBScript launcher as an intermediary.

### Task type patterns

**Gateway (pythonw.exe) — use VBScript launcher:**
```vbs
Set WshShell = CreateObject("Wscript.Shell")
Dim env: Set env = WshShell.Environment("PROCESS")
env("HERMES_HOME") = "C:\Users\user\AppData\Local\hermes"
env("PYTHONIOENCODING") = "utf-8"
env("HERMES_GATEWAY_DETACHED") = "1"
WshShell.CurrentDirectory = env("HERMES_HOME")
WshShell.Run "cmd /c """ & env("HERMES_HOME") & "\hermes-agent\venv\Scripts\pythonw.exe"" -m hermes_cli.main gateway run >> """ & env("HERMES_HOME") & "\gateway.log"" 2>&1", 0, False
```

**PowerShell script (HermesWebUI, etc.) — VBScript launcher:**
```vbs
Set WshShell = CreateObject("Wscript.Shell")
Dim dir: dir = "C:\Users\user\AppData\Local\hermes\webui"
Dim log: log = dir & "\webui.log"
WshShell.CurrentDirectory = dir
WshShell.Run "powershell.exe -ExecutionPolicy Bypass -File """ & dir & "\start.ps1"" >> """ & log & """ 2>&1", 0, False
```

The `0` in `WshShell.Run(cmd, 0, False)` = WindowStyle Hidden.

### Updating the task action

Change the task to run `wscript.exe` (which never creates a console) instead of `.cmd` or `powershell`:

```powershell
$action = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument "C:\path\to\gateway-service\TaskName_hidden.vbs"
```

### Organizing tasks under \Hermes\ folder

Powershell folder, use the \ prefix:

```powershell
Register-ScheduledTask -TaskName "\Hermes\Hermes_Gateway" ...
Register-ScheduledTask -TaskName "\Hermes\HermesWebUIAutoStart" ...
```

Delete old root-level tasks with elevated PowerShell:
```powershell
Unregister-ScheduledTask -TaskName "\OldTask" -Confirm:$false
```

## Windows Service Setup (sc.exe)

Alternative to Scheduled Tasks — run Hermes WebUI (or any Hermes component) as a native Windows service. Useful when you want system-level auto-start with service-manager integration (dependency ordering, automatic restart, event-log integration).

### Service vs Scheduled Task

| | Scheduled Task | Windows Service |
|-|---------------|----------------|
| User context | Interactive user or system | Service account or specific user |
| Environment | Full user profile | Session 0 (no desktop) |
| Window | Visible unless hidden via VBScript | No window by default |
| Management | Task Scheduler | `sc.exe` or Services.msc |
| Dependencies | Not natively supported | Yes (`DEPENDENCIES=`) |
| Auto-restart | Task scheduler retry settings | Service recovery options |

### ⚠️ ERROR 1053 — The Critical Limitation of `sc.exe` for Scripts

**PowerShell scripts (.ps1) and batch files (.cmd/.bat) CANNOT run as native Windows services via `sc.exe` without a wrapper.** The Service Control Manager (SCM) expects every service process to call `StartServiceCtrlDispatcher()` within 30 seconds (configurable, can extend to ~2 minutes). PowerShell, Python, batch files, and most scripting runtimes do NOT call this function.

The result: **error 1053** — "The service did not respond to the start or control request in a timely fashion."

```
[SC] StartService FAILED 1053: The service did not respond to the
start or control request in a timely fashion.

Event 7009: A timeout was reached (120000 milliseconds) while waiting
for the HermesWebUI service to connect.
```

**This applies regardless of:**
- Whether the service runs as LocalSystem or a specific user
- Whether `-NoProfile` is set
- Whether env vars are correctly configured
- Whether the script itself runs fine when launched interactively

**The SCM kills the process after the timeout.** The script never actually runs to completion — it's terminated before `server.py` even finishes importing.

**Therefore:** The entire `sc.exe` approach in this section is useful for managing service *registration and configuration*, but you **must** use a wrapper tool to actually *run* PowerShell/Python scripts as services. See the **NSSM** section below for the recommended solution.

### Creating / Updating a Service

```bash
sc.exe create HermesWebUI binPath= "..." start= auto
sc.exe config HermesWebUI binPath= "..." obj= ".\username" password= "password"
```

### Service Binary Path (PowerShell Scripts)

For a `start.ps1`-style script:

```batch
# ❌ Bare PowerShell — loads user profile, can hang in Session 0
sc.exe config HermesWebUI binPath= "powershell.exe /c \"C:\path\to\start.ps1\""

# ✅ With -NoProfile -ExecutionPolicy Bypass (recommended)
sc.exe config HermesWebUI binPath= "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"C:\path\to\start.ps1\""
```

**`-NoProfile` is essential.** User PowerShell profiles often contain interactive setup code (`Set-PSReadLineOption`, prompt functions, module imports) that hangs or errors in Session 0 where services run. `-ExecutionPolicy Bypass` ensures the script runs regardless of system policy.

### Service Account: LocalSystem vs Specific User

**Never run as LocalSystem** when the script reads user-profile environment variables (`$env:USERPROFILE`, `$env:LOCALAPPDATA`). LocalSystem's profile lives at `C:\Windows\System32\config\systemprofile`, **not** at `C:\Users\<your-user>`.

This breaks Hermes scripts (e.g. `start.ps1`) because they use `$env:USERPROFILE` and `$env:LOCALAPPDATA` to find:
- The Hermes Agent installation directory
- State / data directories
- Config files

**Preferred approach — switch to the real user account:**

```bash
sc.exe config HermesWebUI obj= ".\ragun" password= "your_password"
```

### Workaround: LocalSystem + Inline Env Vars

When switching to a user account fails (error 1069 — logon failure), you can keep the service as **LocalSystem** but force the correct paths via environment variables in the binary path. This avoids storing a user password in the service configuration.

**How it works:** `start.ps1` checks `HERMES_WEBUI_AGENT_DIR` first (line 100); if set and valid, it skips the auto-discovery that fails under LocalSystem.

```batch
sc.exe config HermesWebUI obj= "LocalSystem" password= "" ^
    binPath= "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
\"$env:HERMES_HOME='C:\Users\ragun\AppData\Local\hermes'; ^
$env:HERMES_WEBUI_STATE_DIR='C:\Users\ragun\AppData\Local\hermes\webui'; ^
$env:HERMES_WEBUI_AGENT_DIR='C:\Users\ragun\AppData\Local\hermes\hermes-agent'; ^
& 'C:\Users\ragun\AppData\Local\hermes\webui\start.ps1'\""
```

The key env vars to set:
- `HERMES_WEBUI_AGENT_DIR` — tells the script exactly where the agent lives (bypasses all auto-discovery)
- `HERMES_HOME` — the agent's data root (normally `~/.hermes` or `%LOCALAPPDATA%\hermes`)
- `HERMES_WEBUI_STATE_DIR` — where the WebUI stores its state

**When to use this workaround:**
- The user account lacks the **"Log on as a service"** right (SeServiceLogonRight)
- The user's password keeps being rejected (Microsoft account vs local account mismatch, blank password, or password expires)
- You want to avoid storing plain-text credentials in the service config
- The paths are simple enough to hardcode

> **Note:** Only `HERMES_WEBUI_AGENT_DIR` is critical — `start.ps1` checks it before any path auto-discovery. The others are convenience overrides for consistency.

### NSSM — Recommended for Running Scripts as Services

**NSSM (Non-Sucking Service Manager)** is the standard tool for wrapping scripts (PowerShell, Python, batch) as real Windows services. It handles `StartServiceCtrlDispatcher()` internally, so error 1053 does not apply.

Available via winget:
```bash
winget install NSSM.NSSM
```

NSSM installs to one of several locations depending on how it was installed:

| Source | Binary location |
|--------|----------------|
| **Official installer** | `%ProgramFiles%\NSSM\win64\nssm.exe` |
| **winget** (no restart) | `%LOCALAPPDATA%\Microsoft\WinGet\Links\nssm.exe` (PATH alias) |
| **winget** (after restart) | Anywhere in `PATH` — just run `nssm` |
| **winget cache** (fallback) | `%LOCALAPPDATA%\Microsoft\WinGet\Packages\NSSM.NSSM_Microsoft.Winget.Source_8wekyb3d8bbwe\nssm-<version>\win64\nssm.exe` |

**Trap — winget PATH doesn't take effect in the current PowerShell session.** After `winget install NSSM.NSSM`, `Get-Command nssm.exe` returns nothing until the shell is restarted. Use the helper script at `scripts/find-nssm.ps1` to locate the binary in-session, or pass the known winget path directly:

#### Creating a service

```powershell
# Install — specify the executable, then set parameters separately
nssm install HermesWebUI "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

# Configure
nssm set HermesWebUI AppParameters "-NoProfile -ExecutionPolicy Bypass -File `"C:\path\to\start.ps1`""
nssm set HermesWebUI AppDirectory "C:\path\to\working\dir"
nssm set HermesWebUI DisplayName "Hermes WebUI"
nssm set HermesWebUI Start SERVICE_AUTO_START   # Not AUTO_START — NSSM rejects the short form
nssm set HermesWebUI AppNoConsole 1          # No window (runs in Session 0)
nssm set HermesWebUI AppStdout "C:\path\to\logs\stdout.log"
nssm set HermesWebUI AppStderr "C:\path\to\logs\stderr.log"
nssm set HermesWebUI AppRotateFiles 1        # Rotate logs
nssm set HermesWebUI AppRotateBytes 5242880  # 5MB per log
nssm set HermesWebUI AppThrottle 5000        # Auto-restart delay on crash (ms)
```

#### Managing the service

```powershell
nssm start HermesWebUI
nssm stop HermesWebUI
nssm restart HermesWebUI
nssm status HermesWebUI    # Shows detailed status
```

Standard `sc.exe` and `Get-Service` also work alongside NSSM:
```bash
sc.exe query HermesWebUI
Get-Service HermesWebUI | Format-Table Name,Status,StartType
```

#### Wrapper script pattern (cleanest for env vars)

When the script needs environment variables that differ between interactive and service contexts (e.g., running as LocalSystem but needing user-profile paths), create a thin wrapper `.ps1` that sets env vars first, then call that wrapper from NSSM.

**Concrete scripts on disk** — the full automated NSSM setup scripts created during the June 2026 session live at:
- `%HERMES_HOME%\webui\setup-nssm-service.ps1` — WebUI: removes old service, creates NSSM service, starts, configures logs
- `%HERMES_HOME%\gateway-service\setup-gateway-service.ps1` — Gateway: same pattern for Hermes_Gateway.cmd

Both are drop-in reusable for this machine; copy-and-adapt path-references for other users.

**Critical: `HERMES_WEBUI_PYTHON` trap for LocalSystem**

`start.ps1` checks for Python via `Get-Command python3, python, py` (line 85) **before** checking the agent venv fallback at `$AgentDir\venv\Scripts\python.exe` (line 133). For LocalSystem, `Get-Command` fails because its `PATH` doesn't include `C:\Users\...` entries, AND the script exits with `Write-Error` before reaching the venv fallback.

**Fix:** Always set `HERMES_WEBUI_PYTHON` in the wrapper script:

```powershell
# In wrapper ps1 (start-service.ps1)
$env:HERMES_WEBUI_PYTHON = 'C:\Users\ragun\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe'
```

This bypasses the `Get-Command` PATH check entirely and jumps straight to the venv Python.

```powershell
# start-service.ps1 (wrapper)
$env:HERMES_WEBUI_PYTHON       = 'C:\Users\ragun\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe'
$env:HERMES_WEBUI_AGENT_DIR    = 'C:\Users\ragun\AppData\Local\hermes\hermes-agent'
$env:HERMES_HOME               = 'C:\Users\ragun\AppData\Local\hermes'
$env:HERMES_WEBUI_STATE_DIR    = 'C:\Users\ragun\AppData\Local\hermes\webui'

& 'C:\Users\ragun\AppData\Local\hermes\webui\start.ps1'
```

Then point NSSM at the wrapper — avoids quoting hell in binary paths:

```powershell
nssm install HermesWebUI powershell.exe
nssm set HermesWebUI AppParameters "-NoProfile -ExecutionPolicy Bypass -File `"C:\Users\ragun\AppData\Local\hermes\webui\start-service.ps1`""
```

#### Why NSSM works where sc.exe fails

| | `sc.exe` + naked script | NSSM |
|-|------------------------|------|
| `StartServiceCtrlDispatcher` | ❌ Script doesn't call it | ✅ NSSM handles it |
| Error 1053 timeout | ❌ Script killed before ready | ✅ No timeout |
| Logging | None built-in | ✅ Stdout/stderr to files |
| Auto-restart | Manual recovery config | ✅ `AppThrottle` |
| Window suppression | N/A (session 0 kills on timeout) | ✅ `AppNoConsole 1` |

### Service Lifecycle

```bash
# Query status
sc.exe query HermesWebUI          # Current state (RUNNING / STOPPED)
sc.exe qc HermesWebUI             # Query configuration (binary path, account, start type)

# Start / Stop
sc.exe start HermesWebUI
sc.exe stop HermesWebUI

# Delete
sc.exe delete HermesWebUI
```

All `sc.exe` service management commands require **Administrator privileges**.

### Service Config Syntax

```bash
sc.exe config <service> binPath= <path>    # ⚠️ Space after = is REQUIRED
sc.exe config <service> obj= <account>     # ".\username" or "NT AUTHORITY\LocalService"
sc.exe config <service> password= <pw>     # Omit for built-in accounts
sc.exe config <service> start= auto|demand|disabled
sc.exe config <service> depend= <dep1>/<dep2>   # Service dependencies
```

### Diagnostics

After starting the service, check:

```bash
# 1. Service state
sc.exe query HermesWebUI

# 2. Script logs (if the script writes any)
# 3. Event Viewer → Windows Logs → System → Service Control Manager
```

Common failure modes:
- **Error 1053 — service did not respond in a timely fashion** — The script doesn't call `StartServiceCtrlDispatcher()`. Bare `sc.exe` cannot run PowerShell/Python scripts directly; use **NSSM** (see section above) or a Scheduled Task instead.
- **Service starts then immediately stops** — the script crashed. Check Event Viewer for PowerShell / Python errors. Common causes: missing `-NoProfile`, paths resolved wrong under LocalSystem, or Python exited.
- **`sc.exe start` returns error 1069 "logon failure"** — the service cannot authenticate as the configured user. **Two possible causes:**
  1. Wrong password — re-run `sc.exe config` with the correct password
  2. Missing **"Log on as a service"** right (SeServiceLogonRight) — `sc.exe config` auto-grants this, but it can fail silently if the user doesn't have the privilege to assign rights. Run `secedit /export /cfg C:\sec.cfg` (Admin) and check the `SeServiceLogonRight` line for the user's SID. If missing, switch to the **LocalSystem + Inline Env Vars** workaround instead.
  > **Tip:** If you're sure the password is correct, assume it's the SeServiceLogonRight issue and use the LocalSystem + env var workaround above.
- **sc.exe returns "Access is denied"** — the terminal is not elevated. Run as Administrator.
- **Start-Service throws "Cannot open service"** — same root cause: non-admin context.

### Pitfalls

- **Access denied**: You need an **elevated** (Administrator) terminal — non-admin terminals cannot manage services.
- **Space after `=` in `sc.exe`**: The syntax is `param= value` with a mandatory space. `param=value` (no space) silently fails.
- **`-NoProfile` requirement**: Without it, PowerShell in Session 0 loads user profile scripts, which often contain interactive code that hangs the service startup.
- **LocalSystem env trap**: When the service runs as LocalSystem, `$env:USERPROFILE` points to `C:\Windows\System32\config\systemprofile`, not the real user's home directory. Hermes scripts that look for `~/.hermes`, `$env:LOCALAPPDATA`, or `$env:USERPROFILE` will fail to find the user's config.

## Key Windows Paths

| Config | Path |
|--------|------|
| Gateway `.cmd` | `%HERMES_HOME%\gateway-service\Hermes_Gateway.cmd` |
| Gateway VBS (custom) | `%HERMES_HOME%\gateway-service\Hermes_Gateway_hidden.vbs` |
| `pythonw.exe` | `%HERMES_HOME%\hermes-agent\venv\Scripts\pythonw.exe` |
| Config YAML | `%HERMES_HOME%\config.yaml` |
| Env secrets | `%HERMES_HOME%\.env` |

## PowerShell Scheduled Task Management (vs schtasks CLI)

When `schtasks //change` or `//delete` returns "Access is denied" from git-bash, use PowerShell cmdlets instead — they handle permission elevation more gracefully.

### Basic operations

```powershell
# List tasks (filtered)
Get-ScheduledTask -TaskName "Hermes*" | Format-List TaskName, State, TaskPath

# Stop + delete
Stop-ScheduledTask -TaskName "Hermes_Gateway"
Unregister-ScheduledTask -TaskName "Hermes_Gateway" -Confirm:$false
```

### Creating a task from scratch (e.g., after moving a project)

```powershell
$action = New-ScheduledTaskAction -Execute "powershell" `
    -Argument "C:\path\to\script.ps1 > C:\path\to\log.log" `
    -WorkingDirectory "C:\path\to\dir"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "ragun" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "\Hermes\TaskName" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
Start-ScheduledTask -TaskName "\Hermes\TaskName"
```

### Running PowerShell scripts elevated

From git-bash, run a `.ps1` script as Administrator:

```bash
powershell.exe -Command "Start-Process powershell.exe -ArgumentList '-ExecutionPolicy Bypass -File \"C:\\path\\to\\script.ps1\"' -Verb RunAs -Wait"
```

The `-Wait` flag makes git-bash block until elevation completes; remove it for fire-and-forget.

### When to use each approach

| Situation | Tool |
|-----------|------|
| Quick query / existing task info | `schtasks //query //tn "..." //v //fo list` |
| Creating new task or complex changes | PowerShell cmdlets via `.ps1` script |
| Action fails with "Access is denied" from git-bash | PowerShell with `-Verb RunAs` |
| Changing just the action path/args | `schtasks //change //tn "..." //tr "...new action..."` (may prompt for password) |

## Folder Copy Gotcha (MSYS/Git-Bash)

When copying a directory **to a non-existent destination**, `cp -r` creates a nested folder instead of merging contents:

```bash
# ❌ If ~/hermes/webui/ doesn't exist yet:
cp -r /c/myProjects/hermes-webui ~/hermes/webui/
# Result: ~/hermes/webui/hermes-webui/ (nested!)

# ✅ Fix: create dest first, then copy contents:
mkdir -p ~/hermes/webui && cp -r /c/myProjects/hermes-webui/* ~/hermes/webui/
# Or fix after: mv ~/hermes/webui/hermes-webui/* ~/hermes/webui/ && rmdir ~/hermes/webui/hermes-webui
```

This is because `cp` interprets `dest/` as "create `dest` if it doesn't exist, then place the source inside" — creating a nesting level. MSYS `cp` behaves the same as GNU `cp` here.

## Diagnostics: "Task shows Last Result = 0 but service isn't running"

A common head-scratcher: the scheduled task says `Status: Ready` and `Last Result: 0` (success), but the gateway (or WebUI) is not actually running.

### Why this happens

The task runs `wscript.exe` which runs the `.vbs` file. `wscript.exe` exits immediately after calling `WshShell.Run()` — it **does not** wait for the subprocess. So `Last Result: 0` only means wscript.exe launched, NOT that `pythonw.exe` / `powershell.exe` started successfully.

### Diagnostic checklist

1. **Check for the actual process**:
   ```bash
   ps -W | grep -i "pythonw\|powershell.*start"
   # No output → process never started
   ```

2. **Check log file existence** — if gateway.log doesn't exist, the pythonw.exe command never ran:
   ```bash
   ls -la /c/Users/ragun/AppData/Local/hermes/gateway.log
   # "No such file" → gateway process failed before opening log handle
   ```

3. **Run the VBS manually** to test the chain directly:
   ```bash
   wscript.exe "C:\Users\ragun\AppData\Local\hermes\gateway-service\Hermes_Gateway_hidden.vbs"
   ```
   If this fails but the scheduled task had `Last Result: 0`, the issue is likely **env context** (LogonType, working directory, or user permissions in the task vs interactive).

4. **Common VBScript-in-PowerShell mistake**: Users trying to debug VBS lines directly in PowerShell see:
   ```
   WshShell.CurrentDirectory : The term 'WshShell.CurrentDirectory' is not recognized as the name of a cmdlet
   ```
   This is normal — `WshShell` is a VBScript COM object, not a PowerShell cmdlet. Test VBS code by running the `.vbs` file with `wscript.exe`, not by pasting its lines into PowerShell.

## Pitfalls

- **Error 1053 — `sc.exe` can't run scripts directly**: PowerShell, Python, batch files don't call `StartServiceCtrlDispatcher`, so the SCM kills them after ~2 minutes. Use **NSSM** (see section above) instead.
- **PowerShell services need `-NoProfile`** — user PowerShell profiles with interactive code (`Set-PSReadLineOption`, prompt functions) hang in Session 0. Always include `-NoProfile -ExecutionPolicy Bypass` in the service binary path.
- **Service running as LocalSystem breaks path resolution** — `$env:USERPROFILE` points to `C:\Windows\System32\config\systemprofile`, not the real user's home. Hermes scripts that look for `~/.hermes` will fail. Switch the service to run as the real user account, or use the LocalSystem + Inline Env Vars workaround.
- **Error 1069 "logon failure" can mean two things** — either the password is wrong, **or** the user lacks the "Log on as a service" right (SeServiceLogonRight). `sc.exe config` attempts to auto-grant this right, but it can silently fail. Don't assume it's always a password issue.
- **Service management requires admin** — `sc.exe` and `Start-Service` fail with "Access is denied" from non-elevated terminals.
- **`sc.exe` syntax: space after `=`** — `binPath=`, `obj=`, `password=` all need a space after the `=`. `param=value` (no space) silently fails.
- **Scheduled task needs restart** after config changes (`hermes gateway restart`)
- **`schtasks /change /tr` asks for password** when you change the task action — this is a Windows security behavior
- **The VBScript paths are absolute** — if `HERMES_HOME` changes, update the VBScript
- **`pythonw.exe` must exist** — it's part of the venv. If missing, re-run `hermes setup`
- **Use `hermes config set`** for timezone and other config values — do NOT edit config.yaml directly (the agent tools block it for security)
- **Restart gateway** after `.env` changes to pick up new env vars
- **`schtasks //query` tasks under a folder**: use the full path with double backslash -- `schtasks //query //tn "\\Hermes\\TaskName" //v //fo list` -- or it won't find them at root level
- **`.vbs` file association hijack**: If `.vbs` files open in PowerShell by default (instead of wscript.exe), the VBScript gets parsed as PowerShell, throwing errors like `'WshShell.CurrentDirectory' is not recognized as the name of a cmdlet`. Check with `assoc .vbs` and `ftype VBSFile` -- should be `%SystemRoot%\System32\WScript.exe` or `CScript.exe`. If wrong, fix with: `ftype VBSFile=%SystemRoot%\System32\WScript.exe "%1" %*` (admin). Test by running `wscript.exe path\to\script.vbs` directly.

## Related References

- `references/dashboard-dev-mode.md` — Hermes Agent Dashboard dev mode (Vite + React admin panel at `web/`)
- `references/maintenance-update.md` — Checking for updates, NSSM service restart, fork recovery, and troubleshooting
