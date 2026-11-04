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
  param([string]$Root, [string]$RegistryPath)
  $output = & $pwshPath -NoProfile -File $auditPath -RepoRoot $Root -RegistryPath $RegistryPath -SkipMachineInventory 2>&1
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
