# Gateway + WebUI Scheduled Task Setup (Concrete Example)

This reference captures a real setup from the 2026-06-17 session.
User: ragun, Windows 11, Hermes at `C:\Users\ragun\AppData\Local\hermes`.

## State Before

- `\Hermes_Gateway` (root folder) — runs `Hermes_Gateway.cmd` → `pythonw.exe -m hermes_cli.main gateway run`
- `\HermesWebUIAutoStart` (root folder) — runs `powershell C:\myProjects\hermes-webui\start.ps1 > C:\myProjects\hermes-webUI.log`
- Old WebUI project dir: `C:\myProjects\hermes-webui`
- Both tasks show a console window on login (the `.cmd` and `powershell` windows)

## Steps Taken

### 1. Create VBScript Launchers

**Gateway** — `hermes\gateway-service\Hermes_Gateway_hidden.vbs`:
```vbs
Set WshShell = CreateObject("Wscript.Shell")
Dim env, logFile, hermesHome
Set env = WshShell.Environment("PROCESS")
hermesHome = "C:\Users\ragun\AppData\Local\hermes"
logFile = hermesHome & "\gateway.log"
env("HERMES_HOME") = hermesHome
env("PYTHONIOENCODING") = "utf-8"
env("HERMES_GATEWAY_DETACHED") = "1"
WshShell.CurrentDirectory = hermesHome
WshShell.Run "cmd /c """ & hermesHome & "\hermes-agent\venv\Scripts\pythonw.exe"" -m hermes_cli.main gateway run >> """ & logFile & """ 2>&1", 0, False
```

**WebUI** — `hermes\gateway-service\HermesWebUI_hidden.vbs`:
```vbs
Set WshShell = CreateObject("Wscript.Shell")
Dim webuiDir, logFile
webuiDir = "C:\Users\ragun\AppData\Local\hermes\webui"
logFile = webuiDir & "\webui.log"
WshShell.CurrentDirectory = webuiDir
WshShell.Run "powershell.exe -ExecutionPolicy Bypass -File """ & webuiDir & "\start.ps1"" >> """ & logFile & """ 2>&1", 0, False
```

### 2. Migrate WebUI Project Folder

```bash
# Copy (caution: cp to non-existent dest creates nesting!)
cp -r /c/myProjects/hermes-webui "$HOME/AppData/Local/hermes/webui"
# Fix nested folder:
cd "$HOME/AppData/Local/hermes/webui"
mv hermes-webui/* .
mv hermes-webui/.* . 2>/dev/null
rmdir hermes-webui

# Delete old dir (was busy — used PowerShell):
powershell.exe -Command "Remove-Item 'C:\myProjects\hermes-webui' -Recurse -Force -ErrorAction Stop"
```

### 3. Update Scheduled Tasks (Elevated PowerShell)

Created `update_tasks.ps1`:
```powershell
# Cleanup old root tasks
Unregister-ScheduledTask -TaskName "\Hermes_Gateway" -Confirm:$false
# (HermesWebUIAutoStart was already moved to \Hermes\ by a prior step)

# Create / update both under \Hermes\
$gwAction = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument "C:\Users\ragun\AppData\Local\hermes\gateway-service\Hermes_Gateway_hidden.vbs"
$gwTrigger = New-ScheduledTaskTrigger -AtLogOn
$gwPrincipal = New-ScheduledTaskPrincipal -UserId "ragun" -LogonType Interactive -RunLevel Limited
$gwSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "\Hermes\Hermes_Gateway" -Action $gwAction -Trigger $gwTrigger -Principal $gwPrincipal -Settings $gwSettings -Force

$webuiAction = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument "C:\Users\ragun\AppData\Local\hermes\gateway-service\HermesWebUI_hidden.vbs"
Register-ScheduledTask -TaskName "\Hermes\HermesWebUIAutoStart" -Action $webuiAction -Trigger $gwTrigger -Principal $gwPrincipal -Settings $gwSettings -Force

Start-ScheduledTask -TaskName "\Hermes\Hermes_Gateway"
Start-ScheduledTask -TaskName "\Hermes\HermesWebUIAutoStart"
```

Execute from git-bash:
```bash
powershell.exe -Command "Start-Process powershell.exe -ArgumentList '-ExecutionPolicy Bypass -File \"C:\path\to\update_tasks.ps1\"' -Verb RunAs -Wait"
```

### 4. Verify

```bash
schtasks //query //fo list //v //tn "\Hermes\Hermes_Gateway"
schtasks //query //fo list //v //tn "\Hermes\HermesWebUIAutoStart"
```

Both should show:
- `TaskPath: \Hermes\`
- `Task To Run: wscript.exe C:\...\xxx_hidden.vbs`
- `Status: Running`

## Result

| Before | After |
|--------|-------|
| CMD / PowerShell windows on login | No windows — all hidden ✅ |
| Root folder clutter | Both under `\Hermes\` ✅ |
| No logs (gateway) | `gateway.log` + `webui.log` ✅ |
| Project at `C:\myProjects` | `hermes\webui` under AppData ✅ |

## Log File Locations

- Gateway: `%HERMES_HOME%\gateway.log`
- WebUI: `%HERMES_HOME%\webui\webui.log`

## Troubleshooting

- **"Access is denied" on task delete/create**: Run via elevated PowerShell, not git-bash
- **VBScript doesn't launch**: Check paths are absolute and use double quotes properly
- **Gateway log empty**: pythonw.exe suppresses stdout; logs come from stderr or internal gateway logging. If truly empty, redirect might not capture `pythonw.exe` output — accepted trade-off for hidden execution
- **schtasks // flags broken**: In git-bash, use `//query`, `//delete` (double slash) to avoid MSYS path translation
