# SEPA Desk: install on a Windows home PC and start it at logon.
#   Right-click > Run with PowerShell, or:  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
# Idempotent: re-run after pulling new code.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$py = (Get-Command py -ErrorAction SilentlyContinue)
if (-not $py) { throw "Python 3.10+ not found. Install it from python.org (tick 'Add to PATH'), then re-run." }
if (-not (Test-Path .venv)) { py -3 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -q --upgrade pip
.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt

if (-not (Test-Path .env)) { .\.venv\Scripts\python.exe desk.py token }

# Restore today's work if a bundle was dropped next to the project (newest one wins).
$bundle = Get-ChildItem -Path $Root, (Split-Path -Parent $Root) -Filter "sepa-desk-*.zip" -ErrorAction SilentlyContinue |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($bundle) { Write-Host "Restoring data from $($bundle.FullName)"; .\.venv\Scripts\python.exe desk.py restore $bundle.FullName }

$action  = New-ScheduledTaskAction -Execute "$Root\.venv\Scripts\pythonw.exe" -Argument "desk.py serve" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
           -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "SEPA Desk" -Action $action -Trigger $trigger -Settings $set -Force | Out-Null
Stop-ScheduledTask -TaskName "SEPA Desk" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "SEPA Desk"

Write-Host ""
Write-Host "SEPA Desk is running at http://127.0.0.1:8765 and starts at every logon."
Write-Host "Remote access: install Tailscale, then run:  tailscale serve --bg 8765"
Write-Host "Keep the PC awake: Settings > System > Power > Sleep = Never (when plugged in)."
Write-Host "Research jobs need Claude Code: install it, run 'claude' once and sign in."
