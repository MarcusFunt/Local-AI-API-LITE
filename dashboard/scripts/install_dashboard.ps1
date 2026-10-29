[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$TaskName = 'Local-AI-API-LITE Dashboard Controller'
. (Join-Path $PSScriptRoot 'task_identity.ps1')
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$FrontendDirectory = Join-Path $RepositoryRoot 'dashboard\frontend'
$BackendDirectory = Join-Path $RepositoryRoot 'dashboard\backend'

$Node = Get-Command npm.cmd -ErrorAction Stop
$NodeRuntime = Get-Command node.exe -ErrorAction Stop
$Python = Get-Command python -ErrorAction Stop
$CurrentUser = (& whoami.exe).Trim()
if (-not $CurrentUser) {
    throw 'Could not determine the current Windows user.'
}
$NodeVersionText = (& $NodeRuntime.Source --version).Trim().TrimStart('v')
if ([version]$NodeVersionText -lt [version]'22.12.0') {
    throw 'Node.js 22.12 or newer is required.'
}
$PythonVersionText = (& $Python.Source -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
if ([version]$PythonVersionText -lt [version]'3.11.0') {
    throw 'Python 3.11 or newer is required.'
}

Push-Location $FrontendDirectory
try {
    & $Node.Source ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
    & $Node.Source run build
    if ($LASTEXITCODE -ne 0) { throw 'The dashboard frontend build failed.' }
} finally {
    Pop-Location
}

$DashboardPython = Join-Path $BackendDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $DashboardPython -PathType Leaf)) {
    & $Python.Source -m venv (Join-Path $BackendDirectory '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Creating the dashboard Python environment failed.' }
}

Push-Location $BackendDirectory
try {
    & $DashboardPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Updating pip failed.' }
    & $DashboardPython -m pip install -e '.[gpu]'
    if ($LASTEXITCODE -ne 0) { throw 'Installing dashboard dependencies failed.' }

    Write-Host 'If this is the first installation, copy the one-time setup token below into the local dashboard.'
    & $DashboardPython -m app.bootstrap issue-token
    $TokenExitCode = $LASTEXITCODE
    if ($TokenExitCode -eq 2) {
        Write-Host 'An administrator is already configured; no setup token was issued.'
    } elseif ($TokenExitCode -ne 0) {
        throw 'Issuing the dashboard setup token failed.'
    }
} finally {
    Pop-Location
}

$Action = New-ScheduledTaskAction `
    -Execute $DashboardPython `
    -Argument '-m uvicorn app.main:app --host 127.0.0.1 --port 8765' `
    -WorkingDirectory $BackendDirectory
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask) {
    if (-not (Test-TaskPrincipalForCurrentUser -TaskUserId $ExistingTask.Principal.UserId)) {
        throw "The task '$TaskName' belongs to another Windows user; it was left unchanged."
    }
    if ($ExistingTask.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $TaskName
    }
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed the current-user logon task '$TaskName'."
Write-Host 'The controller is running at http://127.0.0.1:8765 and does not start inference.'
