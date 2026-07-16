<#
.SYNOPSIS
    Locate nssm.exe after winget install (before PATH refresh).
.DESCRIPTION
    After `winget install NSSM.NSSM`, the PATH isn't updated in the current
    session. This script finds nssm.exe by checking known winget install
    locations and the system PATH.
.EXAMPLE
    .\find-nssm.ps1
    # Output: FOUND: C:\Users\me\AppData\Local\Microsoft\WinGet\Links\nssm.exe
#>

$found = @()

# 1. PATH lookup (works after restart)
$pathExe = (Get-Command 'nssm.exe' -ErrorAction SilentlyContinue).Source
if ($pathExe) { $found += $pathExe }

# 2. WinGet Links alias (created by winget install, in PATH but invisible to current session)
$wingetLinks = "$env:LOCALAPPDATA\Microsoft\WinGet\Links\nssm.exe"
if (Test-Path $wingetLinks) { $found += $wingetLinks }

# 3. WinGet Packages cache (actual extracted binary)
$pkgRoot = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
if (Test-Path $pkgRoot) {
    $found += Get-ChildItem -Path $pkgRoot -Recurse -Filter 'win64\nssm.exe' -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
    $found += Get-ChildItem -Path $pkgRoot -Recurse -Filter 'win32\nssm.exe' -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
}

# 4. Program Files (official installer)
foreach ($pf in @("$env:ProgramFiles\NSSM", "${env:ProgramW6432}\NSSM")) {
    $exe = Join-Path $pf 'win64\nssm.exe'
    if (Test-Path $exe) { $found += $exe }
    $exe32 = Join-Path $pf 'win32\nssm.exe'
    if (Test-Path $exe32) { $found += $exe32 }
}

$found = $found | Select-Object -Unique

if ($found.Count -eq 0) {
    Write-Host "[ERROR] nssm.exe not found. Install with: winget install NSSM.NSSM" -ForegroundColor Red
    exit 1
}

Write-Host "NSSM found at:" -ForegroundColor Cyan
foreach ($f in $found) {
    Write-Host "  $f" -ForegroundColor Green
}

# Export to caller
$global:NSSM_PATH = $found[0]
Write-Host ""
Write-Host "Usage: & `$env:NSSM_PATH start HermesWebUI" -ForegroundColor Gray
