# TheBigBos Windows Installer
# Run: powershell -c "irm https://raw.githubusercontent.com/ragungnoviandri/thebigbos/main/install.ps1 | iex"

param(
    [string]$InstallDir = "$env:USERPROFILE\.local\share\thebigbos",
    [string]$ConfigDir = "$env:USERPROFILE\.config\thebigbos",
    [string]$PythonVersion = "3.11.9",
    [string]$RepoUrl = "https://github.com/ragungnoviandri/thebigbos.git",
    [switch]$NoPath = $false,
    [switch]$SkipPython = $false
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  TheBigBos Installer" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check prerequisites
Write-Host "[1/7] Checking prerequisites..." -ForegroundColor Yellow

$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Host "  ERROR: Git not found. Install from https://git-scm.com" -ForegroundColor Red
    exit 1
}
Write-Host "  Git: $($git.Source)" -ForegroundColor Green

# 2. Create directories
Write-Host "[2/7] Creating directories..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "$InstallDir\repo" | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\python" | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\bin" | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\versions" | Out-Null
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
New-Item -ItemType Directory -Force -Path "$ConfigDir\skills" | Out-Null
New-Item -ItemType Directory -Force -Path "$ConfigDir\agents" | Out-Null
New-Item -ItemType Directory -Force -Path "$ConfigDir\tools" | Out-Null
Write-Host "  Install: $InstallDir" -ForegroundColor Green
Write-Host "  Config:  $ConfigDir" -ForegroundColor Green

# 3. Clone repository
Write-Host "[3/7] Cloning repository..." -ForegroundColor Yellow
if (Test-Path "$InstallDir\repo\.git") {
    Write-Host "  Repo exists, pulling latest..." -ForegroundColor Yellow
    Push-Location "$InstallDir\repo"
    git pull origin main 2>&1 | Out-Null
    Pop-Location
} else {
    git clone $RepoUrl "$InstallDir\repo" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: Failed to clone repository" -ForegroundColor Red
        exit 1
    }
}
Write-Host "  Repository ready" -ForegroundColor Green

# 4. Bundle Python (embeddable)
Write-Host "[4/7] Setting up Python..." -ForegroundColor Yellow
$pythonExe = "$InstallDir\python\python.exe"
if (-not $SkipPython -and -not (Test-Path $pythonExe)) {
    $pyUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    $pyZip = "$env:TEMP\python-embed.zip"
    Write-Host "  Downloading Python $PythonVersion..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $pyUrl -OutFile $pyZip
    Expand-Archive -Path $pyZip -DestinationPath "$InstallDir\python" -Force
    Remove-Item $pyZip
    Write-Host "  Python ready" -ForegroundColor Green
} elseif (Test-Path $pythonExe) {
    Write-Host "  Python already bundled" -ForegroundColor Green
} else {
    Write-Host "  Using system Python (--SkipPython)" -ForegroundColor Yellow
    $pythonExe = "python"
}

# 5. Create venv + install
Write-Host "[5/7] Creating virtual environment..." -ForegroundColor Yellow
$venvDir = "$InstallDir\venv"
if (Test-Path $venvDir) {
    Write-Host "  Venv exists, updating..." -ForegroundColor Yellow
} else {
    & $pythonExe -m venv $venvDir 2>&1 | Out-Null
}
$pipExe = "$venvDir\Scripts\pip.exe"
& $pipExe install -e "$InstallDir\repo" --quiet
Write-Host "  Dependencies installed" -ForegroundColor Green

# 6. Create wrapper script
Write-Host "[6/7] Creating wrapper..." -ForegroundColor Yellow
# Create wrapper using venv executable directly
@"
@echo off
"$InstallDir\venv\Scripts\thebigbos.exe" %*
"@ | Set-Content -Path "$InstallDir\bin\thebigbos.bat"
Set-Content -Path "$InstallDir\bin\thebigbos.ps1" -Value "& `"$InstallDir\venv\Scripts\python.exe`" -m thebigbos @args"

# Symlink to user bin
$userBin = "$env:USERPROFILE\.local\bin"
New-Item -ItemType Directory -Force -Path $userBin | Out-Null
Copy-Item -Force "$InstallDir\bin\thebigbos.bat" "$userBin\thebigbos.bat"
Copy-Item -Force "$InstallDir\bin\thebigbos.ps1" "$userBin\thebigbos.ps1"
Write-Host "  Wrapper: $InstallDir\bin\thebigbos.bat" -ForegroundColor Green

# 7. PATH
if (-not $NoPath) {
    Write-Host "[7/7] Adding to PATH..." -ForegroundColor Yellow
    $currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if ($currentPath -notlike "*$userBin*") {
        [Environment]::SetEnvironmentVariable("PATH", "$currentPath;$userBin", "User")
        Write-Host "  Added $userBin to PATH (permanent)" -ForegroundColor Green
    } else {
        Write-Host "  Already in PATH" -ForegroundColor Green
    }
    # Also set for current session so no restart needed
    $env:Path = "$env:Path;$userBin"
    Write-Host "  PATH updated for current session" -ForegroundColor Green
}

# Default config
$configFile = "$ConfigDir\config.json"
if (-not (Test-Path $configFile)) {
    Copy-Item "$InstallDir\repo\thebigbos.json" $configFile
    Write-Host "  Created default config: $configFile" -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  TheBigBos installed!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Restart your terminal, then run:" -ForegroundColor White
Write-Host "    thebigbos setup" -ForegroundColor Cyan
Write-Host "    thebigbos" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Manual path: $userBin\thebigbos.bat" -ForegroundColor DimGray
