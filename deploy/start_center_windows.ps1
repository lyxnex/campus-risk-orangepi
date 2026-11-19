$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root 'private\.venv\Scripts\python.exe'
$Config = Join-Path $Root 'private\center_config.json'
$Ced = Join-Path $Root 'assets\orangepi_ced\ced-mini\model.onnx'
foreach ($Required in @($Python, $Config, $Ced)) {
    if (-not (Test-Path -LiteralPath $Required)) { throw "Missing center prerequisite: $Required" }
}
& $Python (Join-Path $Root 'center\center.py') --config $Config
if ($LASTEXITCODE -ne 0) { throw "Center exited with code $LASTEXITCODE" }
