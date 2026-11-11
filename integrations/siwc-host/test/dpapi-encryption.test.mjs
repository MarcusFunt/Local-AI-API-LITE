import assert from 'node:assert/strict';
import test from 'node:test';
import { createWindowsDpapiCredentialEncryption } from '../src/dpapi-encryption.mjs';

test('Windows current-user DPAPI protects and recovers a synthetic credential without storing plaintext', {
  skip: process.platform !== 'win32',
}, async () => {
  const provider = createWindowsDpapiCredentialEncryption();
  const plaintext = 'synthetic-oauth-credential-for-storage-test';

  assert.equal(await provider.isAvailable(), true);
  const protectedBytes = await provider.encrypt(plaintext);
  assert.ok(protectedBytes instanceof Uint8Array);
  assert.notEqual(Buffer.from(protectedBytes).toString('utf8'), plaintext);
  assert.equal(await provider.decrypt(protectedBytes), plaintext);

  const damaged = Buffer.from(protectedBytes);
  damaged[damaged.length - 1] ^= 0x01;
  await assert.rejects(provider.decrypt(damaged), /Credential protection failed/);
});

test('DPAPI process startup failures are sanitized and do not echo synthetic secrets', {
  skip: process.platform !== 'win32',
}, async () => {
  const secret = 'synthetic-token-must-not-appear-in-errors';
  const provider = createWindowsDpapiCredentialEncryption({
    spawnProcess: () => { throw new Error(secret); },
  });

  await assert.rejects(provider.encrypt(secret), (error) => {
    assert.equal(error.message, 'Credential protection failed.');
    assert.doesNotMatch(error.message, /synthetic-token/);
    return true;
  });
});
