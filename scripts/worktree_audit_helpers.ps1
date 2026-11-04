if (-not ("LiteAuditNative" -as [type])) {
  Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class LiteAuditNative
{
    [StructLayout(LayoutKind.Sequential)]
    public struct FileTime
    {
        public uint Low;
        public uint High;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct ByHandleFileInformation
    {
        public uint FileAttributes;
        public FileTime CreationTime;
        public FileTime LastAccessTime;
        public FileTime LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(
        string fileName, uint desiredAccess, uint shareMode, IntPtr securityAttributes,
        uint creationDisposition, uint flagsAndAttributes, IntPtr templateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool GetFileInformationByHandle(
        SafeFileHandle handle, out ByHandleFileInformation information);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandleW(
        SafeFileHandle handle, System.Text.StringBuilder path, uint length, uint flags);

    public static SafeFileHandle OpenFileNoFollow(string path)
    {
        const uint GenericRead = 0x80000000;
        const uint ShareRead = 0x00000001;
        const uint ShareWrite = 0x00000002;
        const uint OpenExisting = 3;
        const uint OpenReparsePoint = 0x00200000;
        const uint SequentialScan = 0x08000000;
        return CreateFileW(path, GenericRead, ShareRead | ShareWrite, IntPtr.Zero,
            OpenExisting, OpenReparsePoint | SequentialScan, IntPtr.Zero);
    }

    public static SafeFileHandle OpenDirectoryNoFollow(string path)
    {
        const uint FileReadAttributes = 0x00000080;
        const uint ShareRead = 0x00000001;
        const uint ShareWrite = 0x00000002;
        const uint ShareDelete = 0x00000004;
        const uint OpenExisting = 3;
        const uint OpenReparsePoint = 0x00200000;
        const uint BackupSemantics = 0x02000000;
        return CreateFileW(path, FileReadAttributes, ShareRead | ShareWrite | ShareDelete,
            IntPtr.Zero, OpenExisting, OpenReparsePoint | BackupSemantics, IntPtr.Zero);
    }

    public static string GetFinalPath(SafeFileHandle handle)
    {
        var buffer = new System.Text.StringBuilder(32768);
        uint length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0);
        if (length == 0 || length >= buffer.Capacity)
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        return buffer.ToString();
    }
}
'@
}

$script:GitStatusOutputLimitBytes = 16MB
$script:StrictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)

function ConvertFrom-GitStatusPorcelainZ {
  [CmdletBinding()]
  param([AllowEmptyCollection()][byte[]]$Bytes)

  $rows = [System.Collections.Generic.List[object]]::new()
  $offset = 0
  while ($offset -lt $Bytes.Length) {
    $terminator = [Array]::IndexOf($Bytes, [byte]0, $offset)
    if ($terminator -lt 0) { throw "Git status returned an unterminated NUL record" }
    if ($terminator - $offset -lt 4) { throw "Git status returned a malformed status record" }
    if ($Bytes[$offset + 2] -ne 0x20) { throw "Git status record is missing its status/path separator" }

    $x = [char]$Bytes[$offset]
    $y = [char]$Bytes[$offset + 1]
    $validCodes = @(' ', 'M', 'T', 'A', 'D', 'R', 'C', 'U', '?', '!')
    if ($x -cnotin $validCodes -or $y -cnotin $validCodes -or
        ($x -eq ' ' -and $y -eq ' ') -or
        (($x -eq '?' -or $y -eq '?') -and ($x -ne '?' -or $y -ne '?')) -or
        (($x -eq '!' -or $y -eq '!') -and ($x -ne '!' -or $y -ne '!')) -or
        ($x -eq '!' -and $y -ne '!') -or
        ((($x -in @('R', 'C')) -and ($y -in @('R', 'C'))) -or
          ($x -eq 'R' -and $y -eq 'C') -or ($x -eq 'C' -and $y -eq 'R'))) {
      throw "Git status returned an invalid XY status code"
    }

    $pathLength = $terminator - $offset - 3
    if ($pathLength -le 0) { throw "Git status returned an empty path" }
    $pathBytes = [byte[]]::new($pathLength)
    [Array]::Copy($Bytes, $offset + 3, $pathBytes, 0, $pathLength)
    try { $path = $script:StrictUtf8.GetString($pathBytes) }
    catch { throw "Git status path was not valid UTF-8" }
    if ([string]::IsNullOrEmpty($path)) { throw "Git status returned an empty decoded path" }

    $code = [string]::Concat($x, $y)
    $offset = $terminator + 1
    $originalPath = $null
    if ($x -in @('R', 'C') -or $y -in @('R', 'C')) {
      if ($offset -ge $Bytes.Length) { throw "Git rename/copy record is missing its original path" }
      $originalTerminator = [Array]::IndexOf($Bytes, [byte]0, $offset)
      if ($originalTerminator -lt 0) { throw "Git rename/copy record has an unterminated original path" }
      if ($originalTerminator -eq $offset) { throw "Git rename/copy record has an empty original path" }
      $originalBytes = [byte[]]::new($originalTerminator - $offset)
      [Array]::Copy($Bytes, $offset, $originalBytes, 0, $originalBytes.Length)
      try { $originalPath = $script:StrictUtf8.GetString($originalBytes) }
      catch { throw "Git rename/copy original path was not valid UTF-8" }
      $offset = $originalTerminator + 1
    }

    $rows.Add([pscustomobject]@{
      Code = $code
      Path = $path
      OriginalPath = $originalPath
      Paths = if ($null -eq $originalPath) { @($path) } else { @($path, $originalPath) }
    })
  }
  return $rows.ToArray()
}

function Get-GitStatusPorcelainRows {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$Root,
    [string]$GitExecutable,
    [ValidateRange(1, 67108864)][int]$MaxOutputBytes = $script:GitStatusOutputLimitBytes
  )

  if ([string]::IsNullOrWhiteSpace($GitExecutable)) {
    $GitExecutable = (Get-Command git -ErrorAction Stop | Select-Object -First 1).Source
  }
  $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $GitExecutable
  $startInfo.WorkingDirectory = $Root
  $startInfo.UseShellExecute = $false
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  foreach ($argument in @('-C', $Root, '--no-optional-locks', 'status', '--porcelain=v1', '-z', '-uall')) {
    $startInfo.ArgumentList.Add($argument)
  }

  $process = [System.Diagnostics.Process]::new()
  $process.StartInfo = $startInfo
  $buffer = [byte[]]::new(65536)
  $output = [System.IO.MemoryStream]::new()
  try {
    if (!$process.Start()) { throw "Could not start git status" }
    $errorTask = $process.StandardError.ReadToEndAsync()
    while (($read = $process.StandardOutput.BaseStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
      if (($output.Length + $read) -gt $MaxOutputBytes) {
        try { $process.Kill($true) } catch { }
        try { $process.WaitForExit() } catch { }
        throw "Git status output exceeded the $MaxOutputBytes byte limit"
      }
      $output.Write($buffer, 0, $read)
    }
    $process.WaitForExit()
    $null = $errorTask.GetAwaiter().GetResult()
    if ($process.ExitCode -ne 0) { throw "git status failed with exit code $($process.ExitCode)" }
    return ConvertFrom-GitStatusPorcelainZ -Bytes $output.ToArray()
  } finally {
    $output.Dispose()
    $process.Dispose()
  }
}

function ConvertTo-NormalizedFinalHandlePath {
  param([Parameter(Mandatory = $true)][string]$Path)
  if ($Path.StartsWith('\\?\UNC\', [System.StringComparison]::OrdinalIgnoreCase)) {
    return '\\' + $Path.Substring(8)
  }
  if ($Path.StartsWith('\\?\', [System.StringComparison]::OrdinalIgnoreCase)) {
    return $Path.Substring(4)
  }
  return $Path
}

function Test-SecretLikeUntrackedFile {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$WorktreePath,
    [Parameter(Mandatory = $true)][string]$RelativePath,
    [scriptblock]$BeforeOpen
  )

  $stream = $null
  $handle = $null
  try {
    if ([string]::IsNullOrWhiteSpace($RelativePath) -or $RelativePath.IndexOf([char]0) -ge 0 -or
        [System.IO.Path]::IsPathRooted($RelativePath)) { return $null }
    $segments = $RelativePath.Split([char[]]@('/', '\'), [System.StringSplitOptions]::None)
    if ($segments.Count -eq 0 -or @($segments | Where-Object { $_ -eq '' -or $_ -eq '.' -or $_ -eq '..' -or $_.Contains(':') }).Count -gt 0) {
      return $null
    }

    $root = [System.IO.Path]::GetFullPath($WorktreePath).TrimEnd('\', '/')
    if ([string]::IsNullOrWhiteSpace($root)) { return $null }
    $rootAttributes = [System.IO.File]::GetAttributes($root)
    if (($rootAttributes -band [System.IO.FileAttributes]::Directory) -eq 0 -or
        ($rootAttributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return $null }
    $candidate = $root
    for ($index = 0; $index -lt $segments.Count; $index++) {
      $candidate = [System.IO.Path]::Combine($candidate, $segments[$index])
      $candidate = [System.IO.Path]::GetFullPath($candidate)
      $rootPrefix = $root.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
      if (!$candidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) { return $null }
      $attributes = [System.IO.File]::GetAttributes($candidate)
      if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return $null }
      if ($index -lt ($segments.Count - 1) -and ($attributes -band [System.IO.FileAttributes]::Directory) -eq 0) { return $null }
      if ($index -eq ($segments.Count - 1) -and ($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { return $null }
    }

    if ($RelativePath -match '(?i)(^|[\\/])\.env($|\.(?!example$))|(^|[\\/])[^\\/]*(secret|credential|token|password|private[-_]?key|vault)[^\\/]*$') {
      return $true
    }
    if ($BeforeOpen) { & $BeforeOpen $candidate }

    $rootHandle = [LiteAuditNative]::OpenDirectoryNoFollow($root)
    if ($rootHandle.IsInvalid) { return $null }
    try {
      $rootInfo = [LiteAuditNative+ByHandleFileInformation]::new()
      if (![LiteAuditNative]::GetFileInformationByHandle($rootHandle, [ref]$rootInfo) -or
          ($rootInfo.FileAttributes -band 0x400) -ne 0) { return $null }
      $canonicalRoot = ConvertTo-NormalizedFinalHandlePath ([LiteAuditNative]::GetFinalPath($rootHandle))
    } finally {
      $rootHandle.Dispose()
    }

    $handle = [LiteAuditNative]::OpenFileNoFollow($candidate)
    if ($handle.IsInvalid) { return $null }
    $fileInfo = [LiteAuditNative+ByHandleFileInformation]::new()
    if (![LiteAuditNative]::GetFileInformationByHandle($handle, [ref]$fileInfo) -or
        ($fileInfo.FileAttributes -band 0x10) -ne 0 -or
        ($fileInfo.FileAttributes -band 0x400) -ne 0) { return $null }
    $canonicalFile = ConvertTo-NormalizedFinalHandlePath ([LiteAuditNative]::GetFinalPath($handle))
    $canonicalRootPrefix = $canonicalRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (!$canonicalFile.StartsWith($canonicalRootPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        !$canonicalFile.Equals($candidate, [System.StringComparison]::OrdinalIgnoreCase)) { return $null }
    $fileLength = ([int64]$fileInfo.FileSizeHigh * 4294967296L) + [int64]$fileInfo.FileSizeLow
    if ($fileLength -gt 2MB) { return $null }

    $stream = [System.IO.FileStream]::new($handle, [System.IO.FileAccess]::Read)
    $handle = $null
    $contentBytes = [System.IO.MemoryStream]::new()
    try {
      $buffer = [byte[]]::new(65536)
      while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
        if (($contentBytes.Length + $read) -gt 2MB) { return $null }
        $contentBytes.Write($buffer, 0, $read)
      }
      try { $content = $script:StrictUtf8.GetString($contentBytes.ToArray()) }
      catch { return $null }
    } finally {
      $contentBytes.Dispose()
    }
    return ($content -match '(?im)(OPENAI_API_KEY|HUGGINGFACE_HUB_TOKEN|HF_TOKEN|API[_-]?KEY|ACCESS[_-]?TOKEN|CLIENT[_-]?SECRET|PASSWORD|BEARER)\s*[:=]\s*["'']?(?!<|REDACTED|YOUR_|PLACEHOLDER)[A-Za-z0-9._/-]{16,}' -or
      $content -match '(?i)\b(?:sk-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,})\b')
  } catch {
    return $null
  } finally {
    if ($stream) { $stream.Dispose() }
    if ($handle) { $handle.Dispose() }
  }
}
