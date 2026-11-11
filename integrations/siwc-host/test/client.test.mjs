import assert from 'node:assert/strict';
import test from 'node:test';
import { makeHostConfig } from '../src/client.mjs';

test('host configuration uses a stable app identity and user-local storage', () => {
  const encryption = { id: 'test.dpapi', isAvailable() {}, encrypt() {}, decrypt() {} };
  const config = makeHostConfig({
    platform: 'win32',
    env: { LOCALAPPDATA: 'C:\\Users\\Test\\AppData\\Local' },
    homeDirectory: 'C:\\Users\\Test',
    credentialEncryption: encryption,
  });

  assert.equal(config.appName, 'Local AI API LITE');
  assert.equal(config.appId, 'local-ai-api-lite');
  assert.equal(config.redirectPort, 0);
  assert.equal(config.storageDir, 'C:\\Users\\Test\\AppData\\Local\\Local-AI-API-LITE\\siwc');
  assert.equal(config.credentialEncryption, encryption);
  assert.equal(config.sendHostId, true);
});

test('host configuration rejects unsupported platforms before constructing SDK state', () => {
  assert.throws(() => makeHostConfig({ platform: 'linux' }), /Windows only/);
});
