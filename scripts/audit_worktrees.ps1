[CmdletBinding()]
param(
  [string]$RepoRoot = (Join-Path $PSScriptRoot ".."),
  [Parameter(Mandatory = $true)][string]$RegistryPath,
  [string]$ReportPath,
  [switch]$SkipMachineInventory
)

$ErrorActionPreference = "Stop"
$criticalIssues = [System.Collections.Generic.List[string]]::new()
$report = [System.Collections.Generic.List[string]]::new()

function Invoke-GitText {
  param([string]$Root, [string[]]$GitArguments)
  $output = & git -C $Root @GitArguments 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    throw "git $($GitArguments -join ' ') failed with exit code $exitCode"
  }
  return (($output | ForEach-Object { [string]$_ }) -join "`n").TrimEnd("`r", "`n")
}

function Get-AbsolutePath {
  param([string]$Path)
  return [System.IO.Path]::GetFullPath($Path).TrimEnd("\", "/")
}

function Test-GitAncestor {
  param([string]$Root, [string]$Commit, [string]$Base)
  & git -C $Root merge-base --is-ancestor $Commit $Base 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { return $true }
  if ($LASTEXITCODE -eq 1) { return $false }
  return $null
}

function Get-PatchSha256 {
  param([string]$Root, [string]$Base, [string]$Head)
  $gitPath = (Get-Command git -ErrorAction Stop).Source
  $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $gitPath
  $startInfo.WorkingDirectory = $Root
  $startInfo.UseShellExecute = $false
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  foreach ($argument in @("diff", "--binary", "--no-ext-diff", $Base, $Head)) {
    $startInfo.ArgumentList.Add($argument)
  }
  $process = [System.Diagnostics.Process]::new()
  $process.StartInfo = $startInfo
  if (!$process.Start()) { throw "Could not start git diff for patch identity" }
  $errorTask = $process.StandardError.ReadToEndAsync()
  $hash = [System.Security.Cryptography.IncrementalHash]::CreateHash([System.Security.Cryptography.HashAlgorithmName]::SHA256)
  $buffer = [byte[]]::new(65536)
  while (($read = $process.StandardOutput.BaseStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
    $hash.AppendData($buffer, 0, $read)
  }
  $process.WaitForExit()
  $errorText = $errorTask.GetAwaiter().GetResult()
  if ($process.ExitCode -ne 0) { throw "Could not calculate patch identity: git diff exit $($process.ExitCode)" }
  $digest = $hash.GetHashAndReset()
  $hash.Dispose()
  $process.Dispose()
  return [Convert]::ToHexString($digest).ToLowerInvariant()
}

function Get-PorcelainPaths {
  param([string]$StatusText)
  $result = [System.Collections.Generic.List[object]]::new()
  foreach ($line in ($StatusText -split "`r?`n")) {
    if ($line.Length -lt 4) { continue }
    $code = $line.Substring(0, 2)
    $path = $line.Substring(3).Trim('"')
    $result.Add([pscustomobject]@{ Code = $code; Path = $path })
  }
  return $result.ToArray()
}

function Test-SecretLikeUntrackedFile {
  param([string]$WorktreePath, [string]$RelativePath)
  if ($RelativePath -match '(?i)(^|[\\/])\.env($|\.(?!example$))|(^|[\\/])[^\\/]*(secret|credential|token|password|private[-_]?key|vault)[^\\/]*$') {
    return $true
  }
  $candidate = Join-Path $WorktreePath $RelativePath
  if (!(Test-Path -LiteralPath $candidate -PathType Leaf)) { return $false }
  try {
    $item = Get-Item -LiteralPath $candidate -ErrorAction Stop
    if ($item.Length -gt 2MB) { return $null }
    $content = [System.IO.File]::ReadAllText($candidate)
    return $content -match '(?im)(OPENAI_API_KEY|HUGGINGFACE_HUB_TOKEN|HF_TOKEN|API[_-]?KEY|ACCESS[_-]?TOKEN|CLIENT[_-]?SECRET|PASSWORD|BEARER)\s*[:=]\s*["'']?(?!<|REDACTED|YOUR_|PLACEHOLDER)[A-Za-z0-9._/-]{16,}' -or
      $content -match '(?i)\b(?:sk-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,})\b'
  } catch {
    return $null
  }
}

function Add-ReportLine {
  param([string]$Line = "")
  $report.Add($Line)
}

$resolvedRepo = Get-AbsolutePath $RepoRoot
$resolvedRegistry = Get-AbsolutePath $RegistryPath
if (!(Test-Path -LiteralPath $resolvedRepo -PathType Container)) { throw "Repository root does not exist" }
if (!(Test-Path -LiteralPath $resolvedRegistry -PathType Leaf)) { throw "Registry file does not exist" }
$gitRoot = Invoke-GitText $resolvedRepo @("rev-parse", "--show-toplevel")
if ((Get-AbsolutePath $gitRoot) -ne $resolvedRepo) {
  throw "RepoRoot must be the repository top-level directory"
}

try {
  $registryText = Get-Content -LiteralPath $resolvedRegistry -Raw
  $schemaPath = Get-AbsolutePath (Join-Path $PSScriptRoot "..\docs\worktree-registry.schema.json")
  if (!(Test-Json -Json $registryText -SchemaFile $schemaPath)) {
    throw "Registry does not satisfy its JSON schema"
  }
  $registry = $registryText | ConvertFrom-Json
} catch {
  throw "Registry JSON is invalid or does not satisfy the checked-in schema"
}
if ($registry.schema_version -ne 1 -or $registry.repository -ne "MarcusFunt/Local-AI-API-LITE") {
  throw "Registry schema version or repository identity is invalid"
}
if ([string]::IsNullOrWhiteSpace([string]$registry.canonical_root) -or
    [string]::IsNullOrWhiteSpace([string]$registry.canonical_branch) -or
    $null -eq $registry.worktrees) {
  throw "Registry is missing required ownership fields"
}

$remoteMainSha = $null
$mainFreshness = "unknown"
try {
  $remoteLine = Invoke-GitText $resolvedRepo @("ls-remote", "--heads", "origin", "main")
  if ($remoteLine -match '^([0-9a-f]{40,64})\s+refs/heads/main$') {
    $remoteMainSha = $Matches[1].ToLowerInvariant()
    $mainFreshness = "read directly from origin with git ls-remote"
  }
} catch {
  $mainFreshness = "unknown; read-only origin lookup failed"
}
if (!$remoteMainSha) {
  try {
    $remoteMainSha = (Invoke-GitText $resolvedRepo @("rev-parse", "--verify", "origin/main^{commit}")).ToLowerInvariant()
    $mainFreshness = "local origin/main fallback; freshness unverified"
    $criticalIssues.Add("Fresh origin/main could not be read directly; ancestry remains unverified")
  } catch {
    $criticalIssues.Add("Fresh origin/main is unavailable; ancestry remains unverified")
  }
}
$mainObjectAvailable = $false
if ($remoteMainSha) {
  & git -C $resolvedRepo cat-file -e "$remoteMainSha^{commit}" 2>$null | Out-Null
  $mainObjectAvailable = $LASTEXITCODE -eq 0
}
if (!$mainObjectAvailable) {
  $criticalIssues.Add("Fresh origin/main commit object is not present locally; audit did not fetch or mutate Git state")
}

$canonicalRoot = Get-AbsolutePath ([string]$registry.canonical_root)
$currentBranch = Invoke-GitText $resolvedRepo @("branch", "--show-current")
$canonicalBranch = [string]$registry.canonical_branch
$activeBranch = [string]$registry.active_branch
if ([string]::IsNullOrWhiteSpace($activeBranch)) { $activeBranch = $canonicalBranch }
if ($resolvedRepo -ne $canonicalRoot) {
  $criticalIssues.Add("Invoked repository root differs from the registered canonical root")
}
if ($resolvedRepo -eq $canonicalRoot -and $currentBranch -ne $activeBranch) {
  $criticalIssues.Add("Canonical checkout is on an unexpected branch")
}

$worktreeText = Invoke-GitText $resolvedRepo @("worktree", "list", "--porcelain")
$worktrees = [System.Collections.Generic.List[object]]::new()
$currentWorktree = $null
foreach ($line in ($worktreeText -split "`r?`n")) {
  if ([string]::IsNullOrWhiteSpace($line)) { continue }
  if ($line.StartsWith("worktree ")) {
    if ($currentWorktree) { $worktrees.Add($currentWorktree) }
    $currentWorktree = [ordered]@{ Path = $line.Substring(9); Head = ""; Branch = "(detached)" }
  } elseif ($currentWorktree -and $line.StartsWith("HEAD ")) {
    $currentWorktree.Head = $line.Substring(5)
  } elseif ($currentWorktree -and $line.StartsWith("branch ")) {
    $currentWorktree.Branch = $line.Substring(7) -replace '^refs/heads/', ''
  } elseif ($currentWorktree -and $line -eq "detached") {
    $currentWorktree.Branch = "(detached)"
  }
}
if ($currentWorktree) { $worktrees.Add($currentWorktree) }

$registeredWorktrees = @($registry.worktrees)
$registeredByPath = @{}
foreach ($entry in $registeredWorktrees) {
  if (!$entry.path -or !$entry.branch) {
    $criticalIssues.Add("Registry contains a worktree entry without path or branch")
    continue
  }
  $registeredByPath[(Get-AbsolutePath ([string]$entry.path)).ToLowerInvariant()] = $entry
}
$observedByPath = @{}
foreach ($worktree in $worktrees) {
  $path = Get-AbsolutePath $worktree.Path
  $key = $path.ToLowerInvariant()
  $observedByPath[$key] = $worktree
  if (!$registeredByPath.ContainsKey($key)) {
    $criticalIssues.Add("REGISTRY_DRIFT: observed worktree is not registered: $path")
    continue
  }
  $entry = $registeredByPath[$key]
  if ($worktree.Branch -ne [string]$entry.branch) {
    $criticalIssues.Add("REGISTRY_DRIFT: branch mismatch at $path")
  }
}
foreach ($key in $registeredByPath.Keys) {
  if (!$observedByPath.ContainsKey($key)) {
    $criticalIssues.Add("REGISTRY_DRIFT: registered worktree is not present in Git: $([string]$registeredByPath[$key].path)")
  }
}

Add-ReportLine "# Read-only LITE worktree audit"
Add-ReportLine ""
Add-ReportLine "Generated: $([DateTimeOffset]::Now.ToString('o'))"
Add-ReportLine "Repository: $resolvedRepo"
Add-ReportLine "Current branch: $currentBranch"
Add-ReportLine "Fresh origin/main: $(if ($remoteMainSha) { $remoteMainSha } else { 'unknown' }) ($mainFreshness)"
Add-ReportLine "Canonical root/branch: $canonicalRoot / $canonicalBranch"
Add-ReportLine "Registered active branch: $activeBranch"
Add-ReportLine ""
Add-ReportLine "## Git worktrees"
foreach ($worktree in $worktrees) {
  $path = Get-AbsolutePath $worktree.Path
  try {
    $status = Invoke-GitText $path @("status", "--porcelain=v1", "-uall")
    $statusRows = @(Get-PorcelainPaths $status)
    $statusSummary = if ($statusRows.Count -eq 0) { "clean" } else { "dirty ($($statusRows.Count) path(s))" }
    Add-ReportLine "- $path — branch $($worktree.Branch); HEAD $($worktree.Head); $statusSummary"
    foreach ($row in $statusRows) {
      Add-ReportLine "  - $($row.Code) $($row.Path)"
      if ($row.Code -eq "??") {
        $secretScan = Test-SecretLikeUntrackedFile $path $row.Path
        if ($secretScan -eq $true) {
          $criticalIssues.Add("SENSITIVE_UNTRACKED: secret-like path or content detected at $($row.Path)")
        } elseif ($null -eq $secretScan) {
          Add-ReportLine "  - SECRET_SCAN_UNKNOWN: file could not be safely inspected; content was not printed."
          $criticalIssues.Add("SECRET_SCAN_UNKNOWN: untracked file could not be safely inspected at $($row.Path)")
        }
      }
    }
  } catch {
    Add-ReportLine "- $path — status unknown"
    $criticalIssues.Add("Worktree status could not be read")
  }
}
Add-ReportLine ""

$branchOutput = Invoke-GitText $resolvedRepo @("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/remotes")
$branchRows = @()
foreach ($line in ($branchOutput -split "`r?`n")) {
  if ($line -notmatch '^(refs/(?:heads|remotes)/\S+)\s+([0-9a-f]{40,64})$') { continue }
  $refName = $Matches[1]
  $sha = $Matches[2].ToLowerInvariant()
  if ($refName -match '(^|/)HEAD$') { continue }
  $shortName = $refName -replace '^refs/(heads|remotes)/', ''
  $relation = "UNKNOWN_FRESH_MAIN"
  $rawRows = ""
  $patchHash = "unknown"
  if ($mainObjectAvailable -and $remoteMainSha) {
    if ($sha -eq $remoteMainSha) {
      $relation = "FRESH_MAIN"
    } else {
      $ancestor = Test-GitAncestor $resolvedRepo $sha $remoteMainSha
      if ($ancestor -eq $true) {
        $relation = "ANCESTOR_OF_FRESH_MAIN"
      } elseif ($ancestor -eq $false) {
        $rawRows = Invoke-GitText $resolvedRepo @("diff", "--raw", "--abbrev=40", "--no-renames", "--no-ext-diff", $remoteMainSha, $sha)
        $patchHash = Get-PatchSha256 $resolvedRepo $remoteMainSha $sha
        if ([string]::IsNullOrWhiteSpace($rawRows)) {
          $relation = "TREE_IDENTICAL_BUT_HISTORY_DIVERGED"
        } else {
          $relation = "MANUAL_REVIEW_REQUIRED"
        }
      } else {
        $relation = "ANCESTRY_UNKNOWN"
        $criticalIssues.Add("ANCESTRY_UNKNOWN: Git could not determine whether $shortName is contained in fresh main")
      }
    }
  }
  $branchRows += [pscustomobject]@{ Name = $shortName; Sha = $sha; Relation = $relation; Raw = $rawRows; PatchHash = $patchHash }
}
Add-ReportLine "## Branch references versus fresh main"
foreach ($row in $branchRows) {
  Add-ReportLine "- $($row.Name) — $($row.Sha) — $($row.Relation)"
  if ($row.Relation -eq "MANUAL_REVIEW_REQUIRED" -or $row.Relation -eq "TREE_IDENTICAL_BUT_HISTORY_DIVERGED") {
    Add-ReportLine "  - Patch SHA-256: $($row.PatchHash)"
    if ([string]::IsNullOrWhiteSpace($row.Raw)) {
      Add-ReportLine "  - Tree identity: no changed paths versus fresh main"
    } else {
      Add-ReportLine "  - Changed paths and exact blob/mode identities versus fresh main (raw Git diff metadata only):"
      foreach ($rawLine in ($row.Raw -split "`r?`n")) {
        if (![string]::IsNullOrWhiteSpace($rawLine)) { Add-ReportLine "    - $rawLine" }
      }
    }
  }
}
$originHead = "unknown"
try { $originHead = Invoke-GitText $resolvedRepo @("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD") } catch { }
if ($originHead -and $originHead -ne "refs/remotes/origin/main") {
  Add-ReportLine "- STALE_REMOTE_HEAD: origin/HEAD points to $originHead; reconcile during deliberate Git cleanup."
}
$worktreeBranches = @($worktrees | ForEach-Object { $_.Branch })
$retirementCandidates = @($branchRows | Where-Object { $_.Relation -eq "ANCESTOR_OF_FRESH_MAIN" -and $_.Name -notin $worktreeBranches })
if ($retirementCandidates.Count -gt 0) {
  Add-ReportLine ""
  Add-ReportLine "Historical refs reachable from main and absent from active worktrees (manual review only; not deleted):"
  foreach ($row in $retirementCandidates) { Add-ReportLine "- $($row.Name) $($row.Sha)" }
}
Add-ReportLine ""

Add-ReportLine "## Docker Compose metadata"
if ($SkipMachineInventory) {
  Add-ReportLine "- UNKNOWN (fixture mode; machine inventory intentionally skipped)"
} else {
  try {
    $containerIds = @(& docker ps -aq 2>$null)
    if ($LASTEXITCODE -ne 0) { throw "Docker query failed" }
    $liteContainers = @()
    foreach ($id in $containerIds) {
      if ([string]::IsNullOrWhiteSpace([string]$id)) { continue }
      $json = & docker inspect ([string]$id) 2>$null
      if ($LASTEXITCODE -ne 0) {
        Add-ReportLine "- UNKNOWN: Docker could not inspect a discovered container; metadata is incomplete."
        $criticalIssues.Add("DOCKER_INSPECT_UNKNOWN: discovered container metadata could not be read")
        continue
      }
      $container = (($json -join "`n") | ConvertFrom-Json)[0]
      $labels = $container.Config.Labels
      $project = [string]$labels.'com.docker.compose.project'
      if ($project -notmatch '^local-ai-api-lite(?:-|$)') { continue }
      $mounts = @($container.Mounts | ForEach-Object { "$($_.Source) -> $($_.Destination)" }) -join "; "
      $aliases = @()
      foreach ($network in $container.NetworkSettings.Networks.PSObject.Properties) {
        $aliases += "$($network.Name):$($network.Value.Aliases -join ',')"
      }
      $composeFiles = [string]$labels.'com.docker.compose.project.config_files'
      $workingDirectory = [string]$labels.'com.docker.compose.project.working_dir'
      $service = [string]$labels.'com.docker.compose.service'
      $name = ([string]$container.Name).TrimStart('/')
      $state = [string]$container.State.Status
      $image = [string]$container.Config.Image
      $liteContainers += [pscustomobject]@{ Name=$name; Project=$project; Service=$service; State=$state; Image=$image; WorkingDirectory=$workingDirectory; ComposeFiles=$composeFiles; Mounts=$mounts; Aliases=($aliases -join "; ") }
    }
    if ($liteContainers.Count -eq 0) { Add-ReportLine "- No LITE Compose containers found." }
    foreach ($container in $liteContainers) {
      Add-ReportLine "- $($container.Name) — project $($container.Project); service $($container.Service); state $($container.State); image $($container.Image)"
      Add-ReportLine "  - working_dir: $($container.WorkingDirectory)"
      Add-ReportLine "  - config_files: $($container.ComposeFiles)"
      Add-ReportLine "  - mounts: $($container.Mounts)"
      Add-ReportLine "  - network aliases: $($container.Aliases)"
      foreach ($worktree in $worktrees) {
        $worktreePath = Get-AbsolutePath $worktree.Path
        if ($worktreePath -ne $canonicalRoot -and (($container.WorkingDirectory -and $container.WorkingDirectory.StartsWith($worktreePath, [System.StringComparison]::OrdinalIgnoreCase)) -or ($container.ComposeFiles -and $container.ComposeFiles.Contains($worktreePath, [System.StringComparison]::OrdinalIgnoreCase)))) {
          Add-ReportLine "  - WORKTREE_DEPENDENCY: deployment metadata references $worktreePath; manual retirement is blocked until reconciled."
          $entry = $registeredByPath[$worktreePath.ToLowerInvariant()]
          if ($entry -and [string]$entry.docker_dependency -in @("", "none", "unknown")) {
            $criticalIssues.Add("REGISTRY_DRIFT: Docker references a worktree whose registry has no declared dependency")
          }
        }
      }
    }
  } catch {
    Add-ReportLine "- UNKNOWN: Docker metadata could not be read."
    $criticalIssues.Add("DOCKER_INVENTORY_UNKNOWN: Docker metadata could not be read")
  }
}
Add-ReportLine ""

Add-ReportLine "## Scheduled Task metadata"
if ($SkipMachineInventory) {
  Add-ReportLine "- UNKNOWN (fixture mode; machine inventory intentionally skipped)"
} else {
  try {
    $scheduledTasks = @(Get-ScheduledTask -ErrorAction Stop)
    foreach ($expectedTask in @($registry.scheduled_tasks)) {
      $task = $scheduledTasks | Where-Object { $_.TaskName -eq [string]$expectedTask.name -and (!$expectedTask.task_path -or $_.TaskPath -eq [string]$expectedTask.task_path) } | Select-Object -First 1
      if (!$task) {
        Add-ReportLine "- REGISTRY_DRIFT: registered task not found: $($expectedTask.name)"
        $criticalIssues.Add("REGISTRY_DRIFT: registered Scheduled Task is absent")
        continue
      }
      foreach ($action in $task.Actions) {
        $execute = [string]$action.Execute
        $workingDirectory = [string]$action.WorkingDirectory
        Add-ReportLine "- $($task.TaskPath)$($task.TaskName) — state $($task.State); program $execute; working directory $workingDirectory"
        if ($expectedTask.executable -and $execute -ne [string]$expectedTask.executable) {
          $criticalIssues.Add("REGISTRY_DRIFT: Scheduled Task executable differs from registry")
        }
        if ($expectedTask.working_directory -and $workingDirectory -ne [string]$expectedTask.working_directory) {
          $criticalIssues.Add("REGISTRY_DRIFT: Scheduled Task working directory differs from registry")
        }
      }
    }
  } catch {
    Add-ReportLine "- UNKNOWN: Scheduled Task metadata could not be read."
    $criticalIssues.Add("SCHEDULED_TASK_INVENTORY_UNKNOWN: Scheduled Task metadata could not be read")
  }
}
Add-ReportLine ""

if ($criticalIssues.Count -gt 0) {
  Add-ReportLine "## Critical discrepancies"
  foreach ($issue in $criticalIssues) { Add-ReportLine "- $issue" }
  Add-ReportLine ""
  Add-ReportLine "AUDIT_RESULT: CRITICAL_DRIFT"
  $exitCode = 2
} elseif (@($branchRows | Where-Object { $_.Relation -eq "MANUAL_REVIEW_REQUIRED" }).Count -gt 0) {
  Add-ReportLine "AUDIT_RESULT: PASS_WITH_MANUAL_REVIEW"
  $exitCode = 0
} else {
  Add-ReportLine "AUDIT_RESULT: PASS"
  $exitCode = 0
}

$reportText = ($report -join "`r`n") + "`r`n"
if ($ReportPath) {
  $resolvedReport = Get-AbsolutePath $ReportPath
  if (Test-Path -LiteralPath $resolvedReport) { throw "Refusing to overwrite an existing audit report" }
  $parent = Split-Path -Parent $resolvedReport
  if (!(Test-Path -LiteralPath $parent -PathType Container)) { throw "Audit report directory does not exist" }
  [System.IO.File]::WriteAllText($resolvedReport, $reportText, [System.Text.UTF8Encoding]::new($false))
}
Write-Output $reportText
exit $exitCode
