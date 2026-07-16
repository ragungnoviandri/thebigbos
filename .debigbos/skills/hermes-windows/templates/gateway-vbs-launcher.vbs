' Hermes Gateway VBS Launcher — runs pythonw.exe with env vars, hidden window, + log
' Customize HERMES_HOME and paths before use

Dim WshShell, env, hermesHome, logFile
Set WshShell = CreateObject("Wscript.Shell")
Set env = WshShell.Environment("PROCESS")

hermesHome = "C:\Users\ragun\AppData\Local\hermes"         ' ← EDIT if different
logFile    = hermesHome & "\gateway.log"                    ' ← where stdout/stderr goes

' Environment variables that the Gateway CLI expects
env("HERMES_HOME")           = hermesHome
env("PYTHONIOENCODING")      = "utf-8"
env("HERMES_GATEWAY_DETACHED") = "1"

WshShell.CurrentDirectory = hermesHome

' WindowStyle=0 (hidden), bWaitOnReturn=False (fire-and-forget)
WshShell.Run "cmd /c """ & hermesHome _
    & "\hermes-agent\venv\Scripts\pythonw.exe"" -m hermes_cli.main gateway run >> """ _
    & logFile & """ 2>&1", 0, False
