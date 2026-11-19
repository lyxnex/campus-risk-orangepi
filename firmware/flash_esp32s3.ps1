param(
    [Parameter(Mandatory = $true)][string]$Port,
    [Parameter(Mandatory = $true)][string]$ExpectedMac,
    [Parameter(Mandatory = $true)][string]$EsptoolPath
)
$ErrorActionPreference = 'Stop'
$Firmware = Join-Path $PSScriptRoot 'esp32s3'
$Manifest = Get-Content -LiteralPath (Join-Path $Firmware 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($ExpectedMac -notmatch '^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$') { throw 'ExpectedMac must be a complete ESP MAC address.' }
if ($Port -notmatch '^COM\d+$') { throw 'Port must be a Windows COM port such as COM10.' }
$Tool = (Get-Command $EsptoolPath -ErrorAction Stop).Source
foreach ($Entry in $Manifest.files) {
    $Path = Join-Path $Firmware $Entry.path
    if ((Get-Item -LiteralPath $Path).Length -ne $Entry.bytes -or
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Entry.sha256) {
        throw "Firmware file checksum mismatch: $($Entry.path)"
    }
}
$Identity = & $Tool --chip esp32s3 --port $Port --before default-reset --after hard-reset flash-id 2>&1
if ($LASTEXITCODE -ne 0) { throw 'Could not read ESP identity.' }
$IdentityText = $Identity -join "`n"
if ($IdentityText -notmatch [regex]::Escape($ExpectedMac) -or $IdentityText -notmatch 'Detected flash size: 8MB') {
    throw 'ESP MAC or 8 MB flash size does not match; no write attempted.'
}
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BackupDir = Join-Path $Root 'private\mic-backups'
New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
$Backup = Join-Path $BackupDir (($ExpectedMac -replace ':','').ToLowerInvariant() + '-before-rusbaut2.bin')
if (Test-Path -LiteralPath $Backup) { throw "Existing full backup preserved: $Backup" }
& $Tool --chip esp32s3 --port $Port --baud 921600 --before default-reset --after hard-reset read-flash 0 0x800000 $Backup
if ($LASTEXITCODE -ne 0 -or (Get-Item -LiteralPath $Backup).Length -ne 8388608) {
    throw 'Full 8 MB backup failed; no firmware write attempted.'
}
$Regions = @($Manifest.files | Where-Object { $_.address })
$Arguments = @('--chip','esp32s3','--port',$Port,'--baud','921600',
    '--before','default-reset','--after','hard-reset','write-flash',
    '--flash-mode','dio','--flash-freq','80m','--flash-size','8MB')
foreach ($Entry in $Regions) { $Arguments += $Entry.address; $Arguments += (Join-Path $Firmware $Entry.path) }
& $Tool @Arguments
if ($LASTEXITCODE -ne 0) { throw "ESP flash failed; backup preserved: $Backup" }
Write-Output "MIC_RUSBAUT2_FLASH_COMPLETE backup=$Backup"
