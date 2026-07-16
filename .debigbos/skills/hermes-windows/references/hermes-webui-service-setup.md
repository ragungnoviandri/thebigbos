# Hermes WebUI Service Setup (Concrete Example — June 2026)

User: ragun, Windows 11, Hermes at `C:\Users\ragun\AppData\Local\hermes`.

## Context

Setting up Hermes WebUI (`start.ps1`) as a native Windows service (not Scheduled Task) for zero-window background operation.

## Existing Service

A `HermesWebUI` service already existed from a prior installation:

```
SERVICE_NAME: HermesWebUI
TYPE        : WIN32_OWN_PROCESS
STATE       : STOPPED
BINARY_PATH : powershell.exe /c "C:\Users\ragun\AppData\Local\hermes\webui\start.ps1"
START_TYPE  : AUTO_START
SERVICE_ACCT: LocalSystem
```

## Attempt: Switch to User Account

Ran from elevated PowerShell:

```powershell
sc.exe config HermesWebUI binPath= "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"C:\Users\ragun\AppData\Local\hermes\webui\start.ps1\""
sc.exe config HermesWebUI obj= ".\ragun" password= "<password>"
sc.exe start HermesWebUI
```

**Result:** Error 1069 — "logon failure" / "user name or password is incorrect"

### Diagnostics

Event Viewer (Event ID 7038):
```
The HermesWebUI service was unable to log on as .\ragun
with the currently configured password due to the following error:
The user name or password is incorrect.
```

Account info verified:
- Name: `ragun` (local account on `LEGION-PRO-5-6I`)
- FullName: Rahmat Agung Noviandri
- PasswordRequired: True, PasswordChangeable: True
- In Administrators group: Yes
- `Password last set`: 06/06/2026

## Root Cause

Error 1069 can mean **either** a wrong password **or** missing "Log on as a service" right (SeServiceLogonRight). The event log reports "user name or password is incorrect" for both cases — it does not distinguish.

On Windows 11 Home, `secpol.msc` is not available, making SeServiceLogonRight diagnosis harder.

## Recommended Fix: LocalSystem + Inline Env Vars

Since the service account switch failed and the user doesn't want to store credentials, the recommended approach is to keep `LocalSystem` but set the required env vars inline in the binary path.

The script (`start.ps1`) checks `HERMES_WEBUI_AGENT_DIR` at line 100 — if it's set and valid, all user-profile-based auto-discovery is skipped.

```batch
sc.exe config HermesWebUI obj= "LocalSystem" password= "" ^
    binPath= "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
\"$env:HERMES_HOME='C:\Users\ragun\AppData\Local\hermes'; ^
$env:HERMES_WEBUI_STATE_DIR='C:\Users\ragun\AppData\Local\hermes\webui'; ^
$env:HERMES_WEBUI_AGENT_DIR='C:\Users\ragun\AppData\Local\hermes\hermes-agent'; ^
& 'C:\Users\ragun\AppData\Local\hermes\webui\start.ps1'\""
```

Then start:
```bash
sc.exe start HermesWebUI
```

## ⚠️ RESULT: Error 1053

Even with the correct binary path and LocalSystem account, the service **failed to start** with error 1053:

```
[SC] StartService FAILED 1053: The service did not respond to the
start or control request in a timely fashion.

Event 7009: A timeout was reached (120000 milliseconds) while waiting
for the HermesWebUI service to connect.
```

**Root cause:** The Service Control Manager waits for the process to call `StartServiceCtrlDispatcher()`. PowerShell does not call this function. The SCM killed the `powershell.exe` process after 120 seconds, before `server.py` could finish importing.

**This is not fixable by any `sc.exe` configuration.** The only way to run a PowerShell/Python/batch script as a native Windows service is with a wrapper tool like **NSSM**.

## Resolution: NSSM

Installed NSSM via winget:
```powershell
winget install NSSM.NSSM
```

Created a wrapper script `start-service.ps1` to set env vars (avoiding quoting hell in NSSM binary paths):

```powershell
# start-service.ps1
$env:HERMES_WEBUI_AGENT_DIR = 'C:\Users\ragun\AppData\Local\hermes\hermes-agent'
$env:HERMES_HOME            = 'C:\Users\ragun\AppData\Local\hermes'
$env:HERMES_WEBUI_STATE_DIR = 'C:\Users\ragun\AppData\Local\hermes\webui'
& 'C:\Users\ragun\AppData\Local\hermes\webui\start.ps1'
```

Created the NSSM service:
```powershell
# Remove the old sc.exe-based service first
sc.exe delete HermesWebUI

# Install with NSSM
nssm install HermesWebUI powershell.exe
nssm set HermesWebUI AppParameters "-NoProfile -ExecutionPolicy Bypass -File `"C:\Users\ragun\AppData\Local\hermes\webui\start-service.ps1`""
nssm set HermesWebUI AppDirectory "C:\Users\ragun\AppData\Local\hermes\webui"
nssm set HermesWebUI DisplayName "Hermes WebUI"
nssm set HermesWebUI Start SERVICE_AUTO_START
nssm set HermesWebUI AppNoConsole 1
nssm set HermesWebUI AppStdout "C:\Users\ragun\AppData\Local\hermes\logs\webui-stdout.log"
nssm set HermesWebUI AppStderr "C:\Users\ragun\AppData\Local\hermes\logs\webui-stderr.log"
nssm set HermesWebUI AppRotateFiles 1
nssm set HermesWebUI AppRotateBytes 5242880
nssm set HermesWebUI AppThrottle 5000

# Start
nssm start HermesWebUI
```

### NSSM verification

```powershell
nssm status HermesWebUI
Get-Service HermesWebUI | Format-Table Name,Status,StartType
sc.exe query HermesWebUI
```

### NSSM management commands

```powershell
nssm stop HermesWebUI
nssm restart HermesWebUI
nssm start HermesWebUI
```

## Service Credential Reality

- `sc.exe config obj=` auto-grants SeServiceLogonRight, but requires the caller (elevated admin) to have the `SeSecurityPrivilege` to assign user rights — not all admin contexts have this.
- Using `LocalSystem` avoids credential storage entirely (no password needed).
- The env var override approach is preferred when the Hermes installation paths are well-known and stable.
