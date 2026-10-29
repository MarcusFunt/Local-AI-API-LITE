[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$TaskName = 'Local-AI-API-LITE Dashboard Controller'
$CurrentUser = (& whoami.exe).Trim()
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if (-not $Task) {
    Write-Host "The current-user dashboard task '$TaskName' is not installed."
    return
}

if (-not [string]::Equals($Task.Principal.UserId, $CurrentUser, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The task '$TaskName' does not belong to the current Windows user; it was left unchanged."
}

if ($Task.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $TaskName
}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host 'Removed the dashboard controller task. Local dashboard data and the DPAPI-protected vault were preserved.'
