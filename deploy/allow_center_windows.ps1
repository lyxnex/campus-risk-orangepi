param(
    [Parameter(Mandatory = $true)][string]$CenterAddress,
    [Parameter(Mandatory = $true)][string[]]$BoardAddress
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = (Resolve-Path (Join-Path $Root 'private\.venv\Scripts\python.exe')).Path
$Name = 'CampusRiskCenter-8765-boards'
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this PowerShell window as Administrator; no firewall change was made.'
}
if (-not (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $CenterAddress -ErrorAction SilentlyContinue)) {
    throw 'The requested center IPv4 address is not assigned to this PC.'
}
foreach ($Ip in $BoardAddress) {
    $Parsed = $null
    if (-not [Net.IPAddress]::TryParse($Ip, [ref]$Parsed) -or $Parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        throw "Invalid board IPv4 address: $Ip"
    }
}
$Existing = Get-NetFirewallRule -Name $Name -ErrorAction SilentlyContinue
if ($Existing) {
    throw "Firewall rule $Name already exists; inspect it before changing board addresses."
}
New-NetFirewallRule -Name $Name -DisplayName 'Campus risk center - selected Orange Pi boards' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 `
    -LocalAddress $CenterAddress -RemoteAddress $BoardAddress -Program $Python -Profile Any | Out-Null
Write-Output ('CENTER_FIREWALL_READY center=' + $CenterAddress + ' boards=' + ($BoardAddress -join ','))
