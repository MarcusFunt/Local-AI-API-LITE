import { spawn } from 'node:child_process';
import { join } from 'node:path';

const MAX_OUTPUT_BYTES = 4 * 1024 * 1024;
const PROVIDER_ID = 'windows.dpapi.current-user.v1';
const POWERSHELL_SCRIPT = [
  "$ErrorActionPreference = 'Stop'",
  "$ProgressPreference = 'SilentlyContinue'",
  "Add-Type -AssemblyName System.Security",
  "$request = [Console]::In.ReadToEnd() | ConvertFrom-Json",
  "$inputBytes = [Convert]::FromBase64String([string]$request.payload)",
  "$entropy = [Text.Encoding]::UTF8.GetBytes('Local-AI-API-LITE/Sign-in-with-ChatGPT/v1')",
  "switch ([string]$request.mode) {",
  "  'encrypt' { $outputBytes = [Security.Cryptography.ProtectedData]::Protect($inputBytes, $entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser) }",
  "  'decrypt' { $outputBytes = [Security.Cryptography.ProtectedData]::Unprotect($inputBytes, $entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser) }",
  "  'probe' {",
  "    $protected = [Security.Cryptography.ProtectedData]::Protect($inputBytes, $entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)",
  "    $opened = [Security.Cryptography.ProtectedData]::Unprotect($protected, $entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)",
  "    if ([Convert]::ToBase64String($opened) -cne [Convert]::ToBase64String($inputBytes)) { throw 'DPAPI self-test failed' }",
  "    [Console]::Out.Write('available')",
  "    exit 0",
  "  }",
  "  default { throw 'Unsupported operation' }",
  "}",
  "[Console]::Out.Write([Convert]::ToBase64String($outputBytes))",
].join('\n');

function defaultPowerShellPath() {
  return join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
}

function decodeBase64(value) {
  if (typeof value !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    throw new Error('Credential protection failed.');
  }
  const decoded = Buffer.from(value, 'base64');
  if (decoded.toString('base64') !== value) throw new Error('Credential protection failed.');
  return decoded;
}

function runPowerShell({ executable, spawnProcess, timeoutMs }, mode, bytes) {
  if (process.platform !== 'win32') return Promise.reject(new Error('Credential protection failed.'));
  const payload = JSON.stringify({ mode, payload: Buffer.from(bytes).toString('base64') });
  return new Promise((resolve, reject) => {
    let stdout = Buffer.alloc(0);
    let settled = false;
    let timer;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(new Error('Credential protection failed.'));
      else resolve(value);
    };
    let child;
    try {
      child = spawnProcess(executable, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', POWERSHELL_SCRIPT], {
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'ignore'],
      });
    } catch {
      finish(new Error('spawn failed'));
      return;
    }
    timer = setTimeout(() => {
      child.kill();
      finish(new Error('timeout'));
    }, timeoutMs);
    child.stdout.on('data', (chunk) => {
      stdout = Buffer.concat([stdout, Buffer.from(chunk)]);
      if (stdout.length > MAX_OUTPUT_BYTES) {
        child.kill();
        finish(new Error('output too large'));
      }
    });
    child.once('error', () => finish(new Error('spawn failed')));
    child.once('close', (code) => {
      if (code !== 0) return finish(new Error('DPAPI failed'));
      finish(undefined, stdout.toString('utf8'));
    });
    child.stdin.once('error', () => finish(new Error('stdin failed')));
    child.stdin.end(payload);
  });
}

export function createWindowsDpapiCredentialEncryption({
  platform = process.platform,
  executable = defaultPowerShellPath(),
  spawnProcess = spawn,
  timeoutMs = 10_000,
} = {}) {
  const invoke = (mode, bytes) => {
    if (platform !== 'win32') return Promise.reject(new Error('Credential protection failed.'));
    return runPowerShell({ executable, spawnProcess, timeoutMs }, mode, bytes);
  };
  return {
    id: PROVIDER_ID,
    async isAvailable() {
      try {
        return await invoke('probe', Buffer.from('siwc-dpapi-probe', 'utf8')) === 'available';
      } catch {
        return false;
      }
    },
    async encrypt(plaintext) {
      if (typeof plaintext !== 'string') throw new Error('Credential protection failed.');
      return decodeBase64(await invoke('encrypt', Buffer.from(plaintext, 'utf8')));
    },
    async decrypt(ciphertext) {
      if (!(ciphertext instanceof Uint8Array)) throw new Error('Credential protection failed.');
      const plaintext = decodeBase64(await invoke('decrypt', ciphertext));
      try {
        return new TextDecoder('utf-8', { fatal: true }).decode(plaintext);
      } catch {
        throw new Error('Credential protection failed.');
      }
    },
  };
}
