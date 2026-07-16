' Hermes WebUI VBS Launcher — runs start.ps1 hidden via powershell.exe, + log
' Customize paths before use

Dim WshShell, webuiDir, logFile
Set WshShell = CreateObject("Wscript.Shell")

webuiDir = "C:\Users\ragun\AppData\Local\hermes\webui"     ' ← EDIT if different
logFile  = webuiDir & "\webui.log"                          ' ← where stdout/stderr goes

WshShell.CurrentDirectory = webuiDir

' WindowStyle=0 (hidden), bWaitOnReturn=False (fire-and-forget)
WshShell.Run "powershell.exe -ExecutionPolicy Bypass -File """ & webuiDir _
    & "\start.ps1"" >> """ & logFile & """ 2>&1", 0, False
