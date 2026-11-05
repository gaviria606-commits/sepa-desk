# SEPA Desk: install on a Windows home PC and start it at logon.
#   Right-click > Run with PowerShell, or:  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
# Idempotent: re-run after pulling new code.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# $ErrorActionPreference does not cover native programs (python, pip, git): check their exit codes.
function Invoke-Native([string]$What, [scriptblock]$Cmd) {
  & $Cmd
  if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

if ($Root.Length -gt 60) {
  Write-Warning "Project path is $($Root.Length) characters ($Root). Long paths broke the clone and the venv before: prefer a short path such as $env:USERPROFILE\Documents\sepa-desk."
}
if ((Test-Path .git) -and (Get-Command git -ErrorAction SilentlyContinue)) { git config core.longpaths true }

if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw "Python 3.10+ not found. Install it from python.org (tick 'Add to PATH'), then re-run." }
if (-not (Test-Path .\.venv\Scripts\python.exe)) {
  Invoke-Native "Python version check" { py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" }
  Invoke-Native "Creating .venv" { py -3 -m venv .venv }
}
$venvPy = "$Root\.venv\Scripts\python.exe"
Invoke-Native "pip upgrade" { & $venvPy -m pip install -q --upgrade pip }
Invoke-Native "pip install -r requirements.txt" { & $venvPy -m pip install -q -r requirements.txt }

if (-not (Test-Path .env)) { Invoke-Native "Token creation" { & $venvPy desk.py token } }

# First install only: a bundle already restored here is skipped, so re-running never rolls data back.
Invoke-Native "Bundle restore" { & $venvPy desk.py restore --once }

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
  Write-Warning "'claude' is not on PATH: the scheduled research jobs will fail until Claude Code is installed and signed in."
}

$action  = New-ScheduledTaskAction -Execute "$Root\.venv\Scripts\pythonw.exe" -Argument "desk.py serve" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
           -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "SEPA Desk" -Action $action -Trigger $trigger -Settings $set -Force | Out-Null
Stop-ScheduledTask -TaskName "SEPA Desk" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2  # let the old server release port 8765
Start-ScheduledTask -TaskName "SEPA Desk"

$ok = $false
for ($i = 0; $i -lt 20 -and -not $ok; $i++) {
  Start-Sleep -Seconds 1
  try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8765/api/health | Out-Null; $ok = $true } catch {}
}
if (-not $ok) { throw "The desk did not answer on :8765 within 20 s. Check reports\server.log." }

Write-Host ""
Write-Host "SEPA Desk is running at http://127.0.0.1:8765 and starts at every logon (log: reports\server.log)."
Write-Host "It only runs while you are signed in: after a reboot (e.g. Windows Update), sign in once or the 10:15 job waits."
Write-Host "Remote access: install Tailscale, then run:  tailscale serve --bg 8765"
Write-Host "Keep the PC awake: Settings > System > Power > Sleep = Never (when plugged in)."
Write-Host "Research jobs need Claude Code: install it, run 'claude' once and sign in."
