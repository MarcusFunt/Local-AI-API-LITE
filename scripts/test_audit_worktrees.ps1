[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$auditPath = Join-Path $PSScriptRoot "audit_worktrees.ps1"
$pwshPath = (Get-Command pwsh -ErrorAction Stop).Source
$tempRoot = Join-Path $env:TEMP ("lite-worktree-audit-test-" + [guid]::NewGuid().ToString("N"))
$resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
$resolvedTempBase = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar

if (!(Test-Path -LiteralPath $auditPath -PathType Leaf)) {
  throw "Audit script not found: $auditPath"
}
if (!$resolvedTemp.StartsWith($resolvedTempBase, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to use a fixture outside the temporary directory: $resolvedTemp"
}

function Invoke-GitChecked {
  param([string]$Root, [string[]]$GitArguments)
  $output = & git -C $Root @GitArguments 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "git $($GitArguments -join ' ') failed in fixture: $($output -join ' ')"
  }
  return @($output)
}

function Write-FixtureRegistry {
  param([string]$Path, [string]$Root, [string]$Branch, [object[]]$ExtraWorktrees = @())
  $entries = @(
    [pscustomobject]@{
      path = [System.IO.Path]::GetFullPath($Root)
      branch = $Branch
      purpose = "disposable audit fixture"
      status = "active-integration"
      docker_dependency = "none"
      preserve_non_ancestor_history = $false
      review_ticket = "fixture"
    }
  ) + $ExtraWorktrees
  $registry = [pscustomobject]@{
    schema_version = 1
    repository = "MarcusFunt/Local-AI-API-LITE"
    canonical_root = [System.IO.Path]::GetFullPath($Root)
    canonical_branch = "main"
    active_branch = $Branch
    active_integration_owner = "disposable test harness"
    worktrees = $entries
    scheduled_tasks = @()
  }
  [System.IO.File]::WriteAllText($Path, ($registry | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))
}

function Invoke-AuditFixture {
  param([string]$Root, [string]$RegistryPath, [switch]$MachineInventory)
  if ($MachineInventory) {
    $output = & $pwshPath -NoProfile -File $auditPath -RepoRoot $Root -RegistryPath $RegistryPath 2>&1
  } else {
    $output = & $pwshPath -NoProfile -File $auditPath -RepoRoot $Root -RegistryPath $RegistryPath -SkipMachineInventory 2>&1
  }
  $code = $LASTEXITCODE
  [pscustomobject]@{ ExitCode = $code; Output = ($output -join "`n") }
}

try {
  New-Item -ItemType Directory -Path $resolvedTemp | Out-Null
  $seed = Join-Path $resolvedTemp "seed"
  $origin = Join-Path $resolvedTemp "origin.git"
  $repo = Join-Path $resolvedTemp "repo"
  $registryPath = Join-Path $resolvedTemp "worktrees.json"
  New-Item -ItemType Directory -Path $seed | Out-Null

  & git init --quiet --initial-branch=main $seed
  if ($LASTEXITCODE -ne 0) { throw "git init failed for seed" }
  & git -C $seed config user.name "Audit Fixture"
  & git -C $seed config user.email "audit-fixture@example.invalid"
  Set-Content -LiteralPath (Join-Path $seed "README.md") -Value "fixture`n" -NoNewline
  Set-Content -LiteralPath (Join-Path $seed "app.py") -Value "VALUE = 'main'`n" -NoNewline
  Invoke-GitChecked $seed @("add", "README.md", "app.py") | Out-Null
  Invoke-GitChecked $seed @("commit", "--quiet", "-m", "fixture base") | Out-Null

  & git init --quiet --bare $origin
  if ($LASTEXITCODE -ne 0) { throw "git init --bare failed for origin" }
  & git --git-dir=$origin symbolic-ref HEAD refs/heads/main
  if ($LASTEXITCODE -ne 0) { throw "setting fixture origin HEAD failed" }
  Invoke-GitChecked $seed @("remote", "add", "origin", $origin) | Out-Null
  Invoke-GitChecked $seed @("push", "--quiet", "origin", "main") | Out-Null
  & git clone --quiet --branch main $origin $repo
  if ($LASTEXITCODE -ne 0) { throw "cloning fixture origin failed" }
  & git -C $repo config user.name "Audit Fixture"
  & git -C $repo config user.email "audit-fixture@example.invalid"

  Write-FixtureRegistry $registryPath $repo "main"
  $clean = Invoke-AuditFixture $repo $registryPath
  if ($clean.ExitCode -ne 0 -or $clean.Output -notmatch "AUDIT_RESULT: PASS") {
    throw "Clean fixture did not pass:`n$($clean.Output)"
  }
  Write-Output "PASS: clean fixture"

  $secret = "sk-fixture-secret-value-never-print-this"
  Set-Content -LiteralPath (Join-Path $repo "fixture-secret.env") -Value "OPENAI_API_KEY=$secret" -NoNewline
  $staleEntry = [pscustomobject]@{
    path = Join-Path $resolvedTemp "missing-worktree"
    branch = "codex/stale-fixture"
    purpose = "deliberately stale registry entry"
    status = "active"
    docker_dependency = "unknown"
    preserve_non_ancestor_history = $false
    review_ticket = "fixture"
  }
  Write-FixtureRegistry $registryPath $repo "main" @($staleEntry)
  $stale = Invoke-AuditFixture $repo $registryPath
  if ($stale.ExitCode -eq 0 -or $stale.Output -notmatch "REGISTRY_DRIFT" -or $stale.Output -notmatch "SENSITIVE_UNTRACKED") {
    throw "Stale/secret fixture was not rejected:`n$($stale.Output)"
  }
  if ($stale.Output.Contains($secret)) {
    throw "Audit leaked the fixture secret value"
  }
  Write-Output "PASS: stale registry is nonzero and secret value is redacted"
  Remove-Item -LiteralPath (Join-Path $repo "fixture-secret.env")

  Write-FixtureRegistry $registryPath $repo "main"
  $largeFile = Join-Path $repo "fixture-large.bin"
  [System.IO.File]::WriteAllBytes($largeFile, [byte[]]::new(2MB + 1))
  $largeScan = Invoke-AuditFixture $repo $registryPath
  if ($largeScan.ExitCode -eq 0 -or $largeScan.Output -notmatch "SECRET_SCAN_UNKNOWN" -or $largeScan.Output -notmatch "CRITICAL_DRIFT") {
    throw "Large untracked file was treated as safe instead of unknown:`n$($largeScan.Output)"
  }
  Write-Output "PASS: large untracked files fail closed for secret scanning"
  Remove-Item -LiteralPath $largeFile

  $lockedFile = Join-Path $repo "fixture-locked.txt"
  Set-Content -LiteralPath $lockedFile -Value "fixture file that cannot be opened by the audit child" -NoNewline
  $lockedStream = [System.IO.File]::Open($lockedFile, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
  try {
    $lockedScan = Invoke-AuditFixture $repo $registryPath
    if ($lockedScan.ExitCode -eq 0 -or $lockedScan.Output -notmatch "SECRET_SCAN_UNKNOWN" -or $lockedScan.Output -notmatch "CRITICAL_DRIFT") {
      throw "Unreadable untracked file was treated as safe instead of unknown:`n$($lockedScan.Output)"
    }
    Write-Output "PASS: unreadable untracked files fail closed for secret scanning"
  } finally {
    $lockedStream.Dispose()
    Remove-Item -LiteralPath $lockedFile -ErrorAction SilentlyContinue
  }

  Invoke-GitChecked $repo @("switch", "--quiet", "-c", "codex/audit-fixture") | Out-Null
  Set-Content -LiteralPath (Join-Path $repo "app.py") -Value "VALUE = 'feature'`n" -NoNewline
  Invoke-GitChecked $repo @("add", "app.py") | Out-Null
  Invoke-GitChecked $repo @("commit", "--quiet", "-m", "fixture divergent implementation") | Out-Null
  Write-FixtureRegistry $registryPath $repo "codex/audit-fixture"
  $divergent = Invoke-AuditFixture $repo $registryPath
  if ($divergent.ExitCode -ne 0 -or $divergent.Output -notmatch "MANUAL_REVIEW_REQUIRED") {
    throw "Different blob fixture did not require manual review:`n$($divergent.Output)"
  }
  Write-Output "PASS: different source blobs require MANUAL_REVIEW_REQUIRED"

  Invoke-GitChecked $repo @("switch", "--quiet", "-c", "codex/audit-unknown-ancestry") | Out-Null
  Set-Content -LiteralPath (Join-Path $repo "app.py") -Value "VALUE = 'broken ancestry'`n" -NoNewline
  Invoke-GitChecked $repo @("add", "app.py") | Out-Null
  Invoke-GitChecked $repo @("commit", "--quiet", "-m", "fixture missing commit object") | Out-Null
  $brokenSha = (Invoke-GitChecked $repo @("rev-parse", "HEAD") | Select-Object -First 1).Trim()
  $brokenObjectPath = Join-Path (Join-Path (Join-Path $repo ".git\objects") $brokenSha.Substring(0, 2)) $brokenSha.Substring(2)
  if (!(Test-Path -LiteralPath $brokenObjectPath -PathType Leaf)) {
    throw "Expected the fixture commit to be a disposable loose object"
  }
  Invoke-GitChecked $repo @("switch", "--quiet", "main") | Out-Null
  (Get-Item -LiteralPath $brokenObjectPath).IsReadOnly = $false
  Remove-Item -LiteralPath $brokenObjectPath
  Write-FixtureRegistry $registryPath $repo "main"
  $unknownAncestry = Invoke-AuditFixture $repo $registryPath
  if ($unknownAncestry.ExitCode -eq 0 -or $unknownAncestry.Output -notmatch "ANCESTRY_UNKNOWN" -or $unknownAncestry.Output -notmatch "CRITICAL_DRIFT") {
    throw "Unknown ancestry was not treated as critical:`n$($unknownAncestry.Output)"
  }
  Write-Output "PASS: ancestry errors fail closed"

  Invoke-GitChecked $repo @("update-ref", "-d", "refs/heads/codex/audit-unknown-ancestry") | Out-Null
  Write-FixtureRegistry $registryPath $repo "main"
  $dockerStubRoot = Join-Path $resolvedTemp "docker-stub"
  New-Item -ItemType Directory -Path $dockerStubRoot | Out-Null
  $dockerStub = @'
@echo off
if "%LITE_AUDIT_DOCKER_FIXTURE_MODE%"=="ps-error" exit /b 1
if "%1"=="ps" (
  echo fixture-container-id
  exit /b 0
)
if "%1"=="inspect" exit /b 1
exit /b 1
'@
  Set-Content -LiteralPath (Join-Path $dockerStubRoot "docker.cmd") -Value $dockerStub -Encoding Ascii
  $savedPath = $env:PATH
  $savedDockerMode = $env:LITE_AUDIT_DOCKER_FIXTURE_MODE
  try {
    $env:PATH = "$dockerStubRoot;$savedPath"
    $env:LITE_AUDIT_DOCKER_FIXTURE_MODE = "inspect-error"
    $inspectFailure = Invoke-AuditFixture $repo $registryPath -MachineInventory
    if ($inspectFailure.ExitCode -eq 0 -or $inspectFailure.Output -notmatch "DOCKER_INSPECT_UNKNOWN" -or $inspectFailure.Output -notmatch "CRITICAL_DRIFT") {
      throw "A failed container inspect was not treated as critical:`n$($inspectFailure.Output)"
    }
    Write-Output "PASS: failed container inspect is not silently skipped"

    $moduleRoot = Join-Path $resolvedTemp "fixture-modules"
    $taskModuleRoot = Join-Path $moduleRoot "ScheduledTasks"
    New-Item -ItemType Directory -Path $taskModuleRoot -Force | Out-Null
    $taskMarker = Join-Path $resolvedTemp "scheduled-task-mock-called.txt"
    $taskModule = @'
function Get-ScheduledTask {
  [CmdletBinding()]
  param()
  [System.IO.File]::WriteAllText($env:LITE_AUDIT_SCHEDULED_TASK_MARKER, "invoked")
  throw "fixture task inventory failure"
}
Export-ModuleMember -Function Get-ScheduledTask
'@
    Set-Content -LiteralPath (Join-Path $taskModuleRoot "ScheduledTasks.psm1") -Value $taskModule -Encoding utf8
    $taskManifest = @'
@{
  RootModule = 'ScheduledTasks.psm1'
  ModuleVersion = '1.0.0'
  GUID = 'd64a42d1-c966-47be-93c0-102e9d07b254'
  FunctionsToExport = @('Get-ScheduledTask')
}
'@
    Set-Content -LiteralPath (Join-Path $taskModuleRoot "ScheduledTasks.psd1") -Value $taskManifest -Encoding utf8
    $savedModulePath = $env:PSModulePath
    $savedTaskMarker = $env:LITE_AUDIT_SCHEDULED_TASK_MARKER
    try {
      $env:PSModulePath = "$moduleRoot;$savedModulePath"
      $env:LITE_AUDIT_SCHEDULED_TASK_MARKER = $taskMarker
      $env:LITE_AUDIT_DOCKER_FIXTURE_MODE = "ps-error"
      $inventoryFailure = Invoke-AuditFixture $repo $registryPath -MachineInventory
      if (!(Test-Path -LiteralPath $taskMarker -PathType Leaf)) {
        throw "The ScheduledTasks failure mock was not invoked"
      }
      if ($inventoryFailure.ExitCode -eq 0 -or $inventoryFailure.Output -notmatch "DOCKER_INVENTORY_UNKNOWN" -or $inventoryFailure.Output -notmatch "SCHEDULED_TASK_INVENTORY_UNKNOWN" -or $inventoryFailure.Output -notmatch "CRITICAL_DRIFT") {
        throw "Machine-inventory exceptions were not treated as critical:`n$($inventoryFailure.Output)"
      }
      Write-Output "PASS: Docker and Scheduled Task inventory exceptions fail closed"
    } finally {
      $env:PSModulePath = $savedModulePath
      if ($null -eq $savedTaskMarker) { Remove-Item Env:LITE_AUDIT_SCHEDULED_TASK_MARKER -ErrorAction SilentlyContinue } else { $env:LITE_AUDIT_SCHEDULED_TASK_MARKER = $savedTaskMarker }
    }
  } finally {
    $env:PATH = $savedPath
    if ($null -eq $savedDockerMode) { Remove-Item Env:LITE_AUDIT_DOCKER_FIXTURE_MODE -ErrorAction SilentlyContinue } else { $env:LITE_AUDIT_DOCKER_FIXTURE_MODE = $savedDockerMode }
  }
  Write-Output "All audit-worktree fixture checks passed."
}
finally {
  if (Test-Path -LiteralPath $resolvedTemp) {
    $check = [System.IO.Path]::GetFullPath($resolvedTemp)
    if (!$check.StartsWith($resolvedTempBase, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing cleanup outside the temporary directory: $check"
    }
    Remove-Item -LiteralPath $check -Recurse -Force
  }
}
