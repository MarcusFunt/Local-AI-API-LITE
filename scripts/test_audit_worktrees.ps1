[CmdletBinding()]
param()

# Use UTF-8 for nested pwsh process output even when launched by Windows PowerShell 5.1
# with a legacy console code page (such as IBM437). The Unicode path assertions
# must compare the actual names, not text decoded through the system code page.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$ErrorActionPreference = "Stop"
$auditPath = Join-Path $PSScriptRoot "audit_worktrees.ps1"
$helperPath = Join-Path $PSScriptRoot "worktree_audit_helpers.ps1"
. $helperPath
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
  param([string]$Root, [string]$RegistryPath, [switch]$MachineInventory, [string]$GitIndexFile)
  $savedIndex = $env:GIT_INDEX_FILE
  try {
    if ($GitIndexFile) { $env:GIT_INDEX_FILE = $GitIndexFile }
    if ($MachineInventory) {
      $output = & $pwshPath -NoProfile -File $auditPath -RepoRoot $Root -RegistryPath $RegistryPath 2>&1
    } else {
      $output = & $pwshPath -NoProfile -File $auditPath -RepoRoot $Root -RegistryPath $RegistryPath -SkipMachineInventory 2>&1
    }
    $code = $LASTEXITCODE
    return [pscustomobject]@{ ExitCode = $code; Output = ($output -join "`n") }
  } finally {
    if ($null -eq $savedIndex) { Remove-Item Env:GIT_INDEX_FILE -ErrorAction SilentlyContinue }
    else { $env:GIT_INDEX_FILE = $savedIndex }
  }
}

try {
  New-Item -ItemType Directory -Path $resolvedTemp | Out-Null
  $gitExecutable = (Get-Command git -ErrorAction Stop).Source
  $gitStartFailed = $false
  try {
    $null = @(Get-GitStatusPorcelainRows -Root $resolvedTemp -GitExecutable (Join-Path $resolvedTemp "missing-git.exe"))
  } catch { $gitStartFailed = $true }
  if (!$gitStartFailed) { throw "A missing Git executable was not treated as an audit failure" }
  Write-Output "PASS: Git process start failure is rejected"

  $gitStatusFailed = $false
  try { $null = @(Get-GitStatusPorcelainRows -Root $resolvedTemp -GitExecutable $gitExecutable) }
  catch { $gitStatusFailed = $true }
  if (!$gitStatusFailed) { throw "A nonzero Git status process was not treated as an audit failure" }
  Write-Output "PASS: nonzero Git status exit is rejected"

  $strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
  $losslessBytes = [System.Collections.Generic.List[byte]]::new()
  foreach ($record in @(
      "?? følsom.txt",
      "?? note with spaces and 'quote.txt",
      "??  leading and trailing  ",
      "?? control`tand`nline"
    )) {
    $losslessBytes.AddRange([System.Text.Encoding]::ASCII.GetBytes($record.Substring(0, 3)))
    $losslessBytes.AddRange($strictUtf8.GetBytes($record.Substring(3)))
    $losslessBytes.Add(0)
  }
  $losslessRows = @(ConvertFrom-GitStatusPorcelainZ -Bytes $losslessBytes.ToArray())
  if ($losslessRows.Count -ne 4 -or $losslessRows[0].Path -cne "følsom.txt" -or
      $losslessRows[1].Path -cne "note with spaces and 'quote.txt" -or
      $losslessRows[2].Path -cne " leading and trailing  " -or
      $losslessRows[3].Path -cne "control`tand`nline") {
    throw "Porcelain parser did not preserve Unicode, whitespace, quotes, or control bytes"
  }
  Write-Output "PASS: porcelain parser preserves Unicode, whitespace, quote-sensitive and control-byte paths"

  $renameBytes = [System.Collections.Generic.List[byte]]::new()
  $renameBytes.AddRange([System.Text.Encoding]::ASCII.GetBytes("R  renamed name.txt"))
  $renameBytes.Add(0)
  $renameBytes.AddRange([System.Text.Encoding]::UTF8.GetBytes("old name.txt"))
  $renameBytes.Add(0)
  $renameBytes.AddRange([System.Text.Encoding]::ASCII.GetBytes(" C copied.txt"))
  $renameBytes.Add(0)
  $renameBytes.AddRange([System.Text.Encoding]::UTF8.GetBytes("source path.txt"))
  $renameBytes.Add(0)
  $renameRows = @(ConvertFrom-GitStatusPorcelainZ -Bytes $renameBytes.ToArray())
  if ($renameRows.Count -ne 2 -or $renameRows[0].Path -cne "renamed name.txt" -or
      $renameRows[0].OriginalPath -cne "old name.txt" -or $renameRows[0].Paths.Count -ne 2 -or
      $renameRows[1].Path -cne "copied.txt" -or $renameRows[1].OriginalPath -cne "source path.txt") {
    throw "Rename/copy records were not parsed as destination plus original path"
  }
  Write-Output "PASS: rename and copy records preserve destination/source without phantom entries"

  $invalidStatusCases = @(
    [pscustomobject]@{ Name = "invalid UTF-8 path"; Bytes = [byte[]]@(0x3F, 0x3F, 0x20, 0xC3, 0x28, 0x00) },
    [pscustomobject]@{ Name = "unterminated record"; Bytes = [byte[]]@(0x3F, 0x3F, 0x20, 0x61) },
    [pscustomobject]@{ Name = "malformed XY header"; Bytes = [byte[]]@(0x41, 0x3F, 0x20, 0x61, 0x00) },
    [pscustomobject]@{ Name = "impossible blank XY status"; Bytes = [byte[]]@(0x20, 0x20, 0x20, 0x61, 0x00) },
    [pscustomobject]@{ Name = "missing rename source"; Bytes = [byte[]]@(0x52, 0x20, 0x20, 0x61, 0x00) },
    [pscustomobject]@{ Name = "extra ambiguous pathname"; Bytes = [byte[]]@(0x3F, 0x3F, 0x20, 0x61, 0x00, 0x62, 0x00) }
  )
  foreach ($invalidCase in $invalidStatusCases) {
    $rejected = $false
    try { $null = @(ConvertFrom-GitStatusPorcelainZ -Bytes $invalidCase.Bytes) }
    catch { $rejected = $true }
    if (!$rejected) { throw "Parser accepted $($invalidCase.Name)" }
    Write-Output "PASS: malformed porcelain rejected — $($invalidCase.Name)"
  }

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

  $brokenIndex = Join-Path $resolvedTemp "invalid-index"
  [System.IO.File]::WriteAllText($brokenIndex, "not a Git index")
  $statusFailure = Invoke-AuditFixture $repo $registryPath -GitIndexFile $brokenIndex
  if ($statusFailure.ExitCode -eq 0 -or $statusFailure.Output -notmatch "GIT_STATUS_UNKNOWN" -or
      $statusFailure.Output -notmatch "CRITICAL_DRIFT") {
    throw "A Git status failure was not reported as critical inventory drift:`n$($statusFailure.Output)"
  }
  Write-Output "PASS: Git status failure produces critical unknown inventory"

  $benignUnicodePath = Join-Path $repo "résumé and space's file.txt"
  Set-Content -LiteralPath $benignUnicodePath -Value "ordinary fixture text" -NoNewline
  $benignUnicodeAudit = Invoke-AuditFixture $repo $registryPath
  if ($benignUnicodeAudit.ExitCode -ne 0 -or $benignUnicodeAudit.Output -notmatch "AUDIT_RESULT: PASS" -or
      !$benignUnicodeAudit.Output.Contains("résumé and space's file.txt")) {
    throw "A benign Unicode/space path was not inventoried losslessly:`n$($benignUnicodeAudit.Output)"
  }
  $boundedStatusFailed = $false
  try { $null = @(Get-GitStatusPorcelainRows -Root $repo -MaxOutputBytes 1) }
  catch { $boundedStatusFailed = $true }
  if (!$boundedStatusFailed) { throw "Status output exceeding the configured limit was not rejected" }
  Write-Output "PASS: benign Unicode/space path is inventoried and status output is bounded"

  $vanishingPath = Join-Path $repo "vanishing-before-read.txt"
  Set-Content -LiteralPath $vanishingPath -Value "ordinary fixture text" -NoNewline
  $enumerated = @(Get-GitStatusPorcelainRows -Root $repo | Where-Object { $_.Path -ceq "vanishing-before-read.txt" })
  if ($enumerated.Count -ne 1) { throw "Vanishing-file fixture was not enumerated by Git" }
  $vanishResult = Test-SecretLikeUntrackedFile -WorktreePath $repo -RelativePath $enumerated[0].Path -BeforeOpen {
    param($candidatePath)
    Remove-Item -LiteralPath $candidatePath -Force
  }
  if ($null -ne $vanishResult) { throw "A listed file disappearing before inspection was treated as safe" }
  Write-Output "PASS: enumerated file disappearing before open returns SECRET_SCAN_UNKNOWN"

  $outsideRoot = Join-Path $resolvedTemp "outside-private-fixture"
  New-Item -ItemType Directory -Path $outsideRoot | Out-Null
  Set-Content -LiteralPath (Join-Path $outsideRoot "secret.txt") -Value "synthetic outside data" -NoNewline
  $pathUnsafe = Test-SecretLikeUntrackedFile -WorktreePath $repo -RelativePath "../outside-private-fixture/secret.txt"
  if ($null -ne $pathUnsafe) { throw "Path traversal was not rejected as unknown" }
  $nonregularDirectory = Join-Path $repo "fixture-directory"
  New-Item -ItemType Directory -Path $nonregularDirectory | Out-Null
  if ($null -ne (Test-SecretLikeUntrackedFile -WorktreePath $repo -RelativePath "fixture-directory")) {
    throw "A directory path was treated as an inspectable regular file"
  }
  $invalidContentPath = Join-Path $repo "invalid-utf8-content.bin"
  [System.IO.File]::WriteAllBytes($invalidContentPath, [byte[]]@(0xC3, 0x28))
  if ($null -ne (Test-SecretLikeUntrackedFile -WorktreePath $repo -RelativePath "invalid-utf8-content.bin")) {
    throw "Invalid UTF-8 content was silently treated as safe"
  }
  Write-Output "PASS: traversal, nonregular paths, and undecodable contents fail closed"
  Remove-Item -LiteralPath $invalidContentPath -Force
  Remove-Item -LiteralPath $nonregularDirectory -Force

  $junction = Join-Path $repo "fixture-junction"
  try {
    New-Item -ItemType Junction -Path $junction -Target $outsideRoot | Out-Null
    $junctionResult = Test-SecretLikeUntrackedFile -WorktreePath $repo -RelativePath "fixture-junction/secret.txt"
    if ($null -ne $junctionResult) { throw "A path through a reparse point was treated as safe" }
    Write-Output "PASS: reparse-point traversal fails closed"
  } finally {
    if (Test-Path -LiteralPath $junction) { Remove-Item -LiteralPath $junction -Force }
  }

  $unicodeSecret = "sk-unicode-fixture-secret-never-print-this"
  Set-Content -LiteralPath (Join-Path $repo "følsom.txt") -Value "OPENAI_API_KEY=$unicodeSecret" -NoNewline
  $unicodeSecretScan = Invoke-AuditFixture $repo $registryPath
  if ($unicodeSecretScan.ExitCode -eq 0 -or $unicodeSecretScan.Output -notmatch "SENSITIVE_UNTRACKED" -or $unicodeSecretScan.Output -notmatch "følsom.txt") {
    throw "A Unicode-named secret file was not rejected:`n$($unicodeSecretScan.Output)"
  }
  if ($unicodeSecretScan.Output.Contains($unicodeSecret)) {
    throw "Audit leaked the Unicode fixture secret value"
  }
  Write-Output "PASS: Unicode secret path is rejected and redacted"
  Remove-Item -LiteralPath (Join-Path $repo "følsom.txt")

  Set-Content -LiteralPath (Join-Path $repo "ascii.txt") -Value "OPENAI_API_KEY=$unicodeSecret" -NoNewline
  $asciiSecretScan = Invoke-AuditFixture $repo $registryPath
  if ($asciiSecretScan.ExitCode -eq 0 -or $asciiSecretScan.Output -notmatch "SENSITIVE_UNTRACKED" -or
      !$asciiSecretScan.Output.Contains("ascii.txt") -or $asciiSecretScan.Output.Contains($unicodeSecret)) {
    throw "The ASCII secret control was not rejected and redacted:`n$($asciiSecretScan.Output)"
  }
  Write-Output "PASS: ASCII secret control remains rejected and redacted"
  Remove-Item -LiteralPath (Join-Path $repo "ascii.txt")

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
