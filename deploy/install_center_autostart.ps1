$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$TaskName = 'CampusRiskCenter-GitHub'
$Starter = Join-Path $PSScriptRoot 'start_center_windows.ps1'
$Python = Join-Path $Root 'private\.venv\Scripts\python.exe'
$Config = Join-Path $Root 'private\center_config.json'
$Ced = Join-Path $Root 'assets\orangepi_ced\ced-mini\model.onnx'
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this PowerShell window as Administrator; no task was registered.'
}
foreach ($Required in @($Starter, $Python, $Config, $Ced)) {
    if (-not (Test-Path -LiteralPath $Required)) { throw "Missing center prerequisite: $Required" }
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw "Existing task preserved: $TaskName"
}
if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {
    throw 'TCP 8765 is already in use. Stop the manually started center before installing autostart.'
}
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Starter + '"') `
    -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Account = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Principal $Account | Out-Null
Start-ScheduledTask -TaskName $TaskName
for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $Health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/healthz' -TimeoutSec 1
        if ($Health.ok) { Write-Output "CENTER_AUTOSTART_READY task=$TaskName"; exit 0 }
    } catch {}
}
throw "Task $TaskName was registered but center did not become healthy; inspect private\center_data\center.log. Data and task were preserved."
