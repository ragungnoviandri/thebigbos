# deBigBos Windows Installer
# Run: powershell -c "irm https://raw.githubusercontent.com/ragungnoviandri/deBigBos/main/install.ps1 | iex"

param(
    [string]$InstallDir = "$env:USERPROFILE\.local\share\deBigBos",
    [string]$ConfigDir = "$env:USERPROFILE\.config\deBigBos",
    [string]$PythonVersion = "3.11.9",
    [string]$RepoUrl = "https://github.com/ragungnoviandri/deBigBos.git",
    [string]$LocalRepo = "",
    [switch]$NoPath = $false,
    [switch]$SkipPython = $false
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  deBigBos Installer" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check prerequisites
Write-Host "[1/7] Checking prerequisites..." -ForegroundColor Yellow

if ($LocalRepo) {
    if (-not (Test-Path -LiteralPath $LocalRepo)) {
        Write-Host "  ERROR: LocalRepo not found: $LocalRepo" -ForegroundColor Red
        exit 1
    }
    Write-Host "  Local: $LocalRepo" -ForegroundColor Green
} else {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Write-Host "  ERROR: Git not found. Install from https://git-scm.com" -ForegroundColor Red
        exit 1
    }
    Write-Host "  Git: $($git.Source)" -ForegroundColor Green
}

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

# 3. Get repository
if ($LocalRepo) {
    $repoPath = $LocalRepo
    Write-Host "[3/7] Using local repository..." -ForegroundColor Yellow
    Write-Host "  Path: $repoPath" -ForegroundColor Green
} else {
    Write-Host "[3/7] Cloning repository..." -ForegroundColor Yellow
    $repoPath = "$InstallDir\repo"
    if (Test-Path "$repoPath\.git") {
        Write-Host "  Repo exists, pulling latest..." -ForegroundColor Yellow
        Push-Location $repoPath
        git pull origin main 2>&1 | Out-Null
        Pop-Location
    } else {
        git clone $RepoUrl $repoPath 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: Failed to clone repository" -ForegroundColor Red
            exit 1
        }
    }
    Write-Host "  Repository ready" -ForegroundColor Green
}

# 4. Set up Python
Write-Host "[4/7] Setting up Python..." -ForegroundColor Yellow
if ($LocalRepo -or $SkipPython) {
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $pythonExe) {
        Write-Host "  No system Python found, downloading..." -ForegroundColor Yellow
        $pythonExe = "$InstallDir\python\python.exe"
        $pyUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
        $pyZip = "$env:TEMP\python-embed.zip"
        Invoke-WebRequest -Uri $pyUrl -OutFile $pyZip
        Expand-Archive -Path $pyZip -DestinationPath "$InstallDir\python" -Force
        Remove-Item $pyZip
        Write-Host "  Python ready (bundled)" -ForegroundColor Green
    } else {
        Write-Host "  System Python: $pythonExe" -ForegroundColor Green
    }
} else {
    $pythonExe = "$InstallDir\python\python.exe"
    if (-not (Test-Path $pythonExe)) {
        $pyUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
        $pyZip = "$env:TEMP\python-embed.zip"
        Write-Host "  Downloading Python $PythonVersion..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $pyUrl -OutFile $pyZip
        Expand-Archive -Path $pyZip -DestinationPath "$InstallDir\python" -Force
        Remove-Item $pyZip
        Write-Host "  Python ready" -ForegroundColor Green
    } else {
        Write-Host "  Python already bundled" -ForegroundColor Green
    }
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
& $pipExe install -e $repoPath --quiet
Write-Host "  Dependencies installed" -ForegroundColor Green

# 6. Create wrapper script
Write-Host "[6/7] Creating wrapper..." -ForegroundColor Yellow
# Create wrapper using venv executable directly
@"
@echo off
"$InstallDir\venv\Scripts\deBigBos.exe" %*
"@ | Set-Content -Path "$InstallDir\bin\deBigBos.bat"
Set-Content -Path "$InstallDir\bin\deBigBos.ps1" -Value "& `"$InstallDir\venv\Scripts\deBigBos.exe`" @args"

# Symlink to user bin
$userBin = "$env:USERPROFILE\.local\bin"
New-Item -ItemType Directory -Force -Path $userBin | Out-Null
Copy-Item -Force "$InstallDir\bin\deBigBos.bat" "$userBin\deBigBos.bat"
Copy-Item -Force "$InstallDir\bin\deBigBos.ps1" "$userBin\deBigBos.ps1"
Write-Host "  Wrapper: $InstallDir\bin\deBigBos.bat" -ForegroundColor Green

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
    Copy-Item "$repoPath\deBigBos.json" $configFile
    Write-Host "  Created default config: $configFile" -ForegroundColor Green
}

# Copy bundled skills to global config
if (Test-Path "$repoPath\.debigbos\skills") {
    $skillCount = 0
    Get-ChildItem -Path "$repoPath\.debigbos\skills" -Directory | ForEach-Object {
        $dest = "$ConfigDir\skills\$($_.Name)"
        if (-not (Test-Path $dest)) {
            Copy-Item -Path $_.FullName -Destination $dest -Recurse -Force
            $skillCount++
        }
    }
    Write-Host "  Installed $skillCount skills to $ConfigDir\skills" -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  deBigBos installed!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Restart your terminal, then run:" -ForegroundColor White
Write-Host "    deBigBos setup" -ForegroundColor Cyan
Write-Host "    deBigBos" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Manual path: $userBin\deBigBos.bat" -ForegroundColor DarkGray
