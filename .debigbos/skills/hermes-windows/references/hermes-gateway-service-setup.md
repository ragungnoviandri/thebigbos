# Hermes Gateway Service Setup (Concrete Example — June 2026)

User: ragun, Windows 11, Hermes at `C:\Users\ragun\AppData\Local\hermes`.

## Context

Setting up `Hermes_Gateway.cmd` (`gateway run`) as an NSSM Windows service for zero-window background operation.

## Files

| File | Purpose |
|------|---------|
| `C:\Users\ragun\AppData\Local\hermes\gateway-service\Hermes_Gateway.cmd` | Original batch launcher (uses `pythonw.exe`) |
| `C:\Users\ragun\AppData\Local\hermes\gateway-service\gateway-service.ps1` | NSSM wrapper — sets env vars, runs gateway |
| `C:\Users\ragun\AppData\Local\hermes\gateway-service\setup-gateway-service.ps1` | One-shot setup script (install + configure + start) |
| `C:\Users\ragun\AppData\Local\hermes\logs\gateway-stdout.log` | NSSM stdout |
| `C:\Users\ragun\AppData\Local\hermes\logs\gateway-stderr.log` | NSSM stderr |

## Wrapper Script (`gateway-service.ps1`)

```powershell
$env:HERMES_HOME            = 'C:\Users\ragun\AppData\Local\hermes'
$env:PYTHONIOENCODING       = 'utf-8'
$env:HERMES_GATEWAY_DETACHED = '1'
$env:VIRTUAL_ENV            = 'C:\Users\ragun\AppData\Local\hermes\hermes-agent\venv'

$pythonw = 'C:\Users\ragun\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe'
$gateway = '-m', 'hermes_cli.main', 'gateway', 'run'

Write-Host "Starting Hermes Gateway..."
& $pythonw $gateway
```

Note: `pythonw.exe` is used (no console window) — the original `.cmd` already used it.

## NSSM Service Setup

```powershell
nssm install HermesGateway powershell.exe
nssm set HermesGateway AppParameters "-NoProfile -ExecutionPolicy Bypass -File `"C:\Users\ragun\AppData\Local\hermes\gateway-service\gateway-service.ps1`""
nssm set HermesGateway AppDirectory "C:\Users\ragun\AppData\Local\hermes\gateway-service"
nssm set HermesGateway DisplayName "Hermes Gateway"
nssm set HermesGateway Start SERVICE_AUTO_START
nssm set HermesGateway AppNoConsole 1
nssm set HermesGateway AppStdout "C:\Users\ragun\AppData\Local\hermes\logs\gateway-stdout.log"
nssm set HermesGateway AppStderr "C:\Users\ragun\AppData\Local\hermes\logs\gateway-stderr.log"
nssm set HermesGateway AppRotateFiles 1
nssm set HermesGateway AppRotateBytes 5242880
nssm set HermesGateway AppThrottle 5000

nssm start HermesGateway
```

## Verification

```powershell
Get-Service HermesGateway | Format-Table Name,Status,StartType
nssm status HermesGateway
```

## Key differences from WebUI service

- **No `HERMES_WEBUI_PYTHON` needed** — gateway runs via `pythonw.exe` directly, not through a `start.ps1` that does PATH discovery
- **Working directory** — `gateway-service\` not `webui\`
- **Log files** — `gateway-stdout.log` / `gateway-stderr.log`
