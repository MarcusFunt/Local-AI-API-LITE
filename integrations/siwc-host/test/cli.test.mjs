import assert from 'node:assert/strict';
import test from 'node:test';
import { runCli } from '../src/cli.mjs';

function client(overrides = {}) {
  return {
    getSession: async () => ({ status: 'disconnected', sharing: false }),
    signIn: async () => ({ status: 'connected', sharing: false }),
    listModels: async () => [],
    streamResponse: async () => ({ text: '' }),
    ...overrides,
  };
}

test('unknown or extra CLI arguments never construct a client', async () => {
  let constructed = 0;
  const lines = [];
  const code = await runCli({
    argv: ['connect', '--force'],
    createClient: () => { constructed += 1; return client(); },
    write: (line) => lines.push(line),
  });

  assert.equal(code, 2);
  assert.equal(constructed, 0);
  assert.match(lines[0], /status\|connect\|models\|smoke/);
});

test('status command does not call sign-in', async () => {
  let signIns = 0;
  const lines = [];
  const code = await runCli({
    argv: ['status'],
    createClient: () => client({ signIn: async () => { signIns += 1; throw new Error('unexpected'); } }),
    write: (line) => lines.push(line),
  });

  assert.equal(code, 0);
  assert.equal(signIns, 0);
  assert.match(lines.join('\n'), /"planUsage":"unavailable"/);
});

test('connect command reports cancellation without calling sign-in', async () => {
  let signIns = 0;
  const lines = [];
  const code = await runCli({
    argv: ['connect'],
    createClient: () => client({ signIn: async () => { signIns += 1; throw new Error('must not sign in'); } }),
    confirm: async () => false,
    write: (line) => lines.push(line),
  });

  assert.equal(code, 0);
  assert.equal(signIns, 0);
  assert.match(lines.join('\n'), /"state":"cancelled"/);
});
