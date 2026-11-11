import assert from 'node:assert/strict';
import test from 'node:test';
import { createCommandRunner } from '../src/commands.mjs';

function clientFor(overrides = {}) {
  return {
    signIn: async () => ({ status: 'connected', sharing: false, profileLabel: 'Test account' }),
    getSession: async () => ({ status: 'disconnected', sharing: false }),
    listModels: async () => [],
    streamResponse: async () => ({ text: '' }),
    ...overrides,
  };
}

test('status distinguishes identity-only from plan-enabled and omits unexpected token fields', async () => {
  const emitted = [];
  const runner = createCommandRunner({
    client: clientFor({
      getSession: async () => ({
        status: 'connected',
        sharing: false,
        profileLabel: 'Personal account',
        identity: { name: 'Test User', email: 'test@example.invalid' },
        accessToken: 'access-token-secret',
        refreshToken: 'refresh-token-secret',
      }),
    }),
    emit: (event) => emitted.push(event),
  });

  const result = await runner.status();

  assert.equal(result.session.planUsage, 'identity_only');
  assert.equal(result.session.inferenceReady, false);
  assert.equal(result.session.profileLabel, 'Personal account');
  assert.doesNotMatch(JSON.stringify(emitted), /access-token-secret|refresh-token-secret/);
  assert.doesNotMatch(JSON.stringify(emitted), /test@example\.invalid|Test User/);
});

test('connect requires an explicit confirmation before starting OAuth', async () => {
  let signIns = 0;
  let warning = '';
  const runner = createCommandRunner({
    client: clientFor({ signIn: async () => { signIns += 1; return { status: 'connected', sharing: false }; } }),
    confirm: async (message) => { warning = message; return false; },
    emit: () => {},
  });

  const result = await runner.connect();

  assert.equal(result.state, 'cancelled');
  assert.match(warning, /token sharing/i);
  assert.equal(signIns, 0);
});

test('confirmed connect explicitly requests authorization consent', async () => {
  let signInOptions;
  const runner = createCommandRunner({
    client: clientFor({ signIn: async (options) => { signInOptions = options; return { status: 'connected', sharing: false }; } }),
    confirm: async () => true,
    emit: () => {},
  });

  const result = await runner.connect();

  assert.equal(result.session.planUsage, 'identity_only');
  assert.deepEqual(signInOptions, { reconsent: true });
});

test('smoke refuses an identity-only session before asking consent or discovering models', async () => {
  let confirmations = 0;
  let modelRequests = 0;
  let inferenceRequests = 0;
  const runner = createCommandRunner({
    client: clientFor({
      getSession: async () => ({ status: 'connected', sharing: false }),
      listModels: async () => { modelRequests += 1; return [{ slug: 'should-not-be-used', displayName: 'Test' }]; },
      streamResponse: async () => { inferenceRequests += 1; return { text: 'unexpected' }; },
    }),
    confirm: async () => { confirmations += 1; return true; },
    emit: () => {},
  });

  const result = await runner.smoke();

  assert.equal(result.state, 'plan_usage_not_granted');
  assert.equal(confirmations, 0);
  assert.equal(modelRequests, 0);
  assert.equal(inferenceRequests, 0);
});

test('model listing refuses an identity-only session before asking confirmation or calling the SDK', async () => {
  let confirmations = 0;
  let modelRequests = 0;
  const runner = createCommandRunner({
    client: clientFor({
      getSession: async () => ({ status: 'connected', sharing: false }),
      listModels: async () => { modelRequests += 1; return [{ slug: 'should-not-be-used', displayName: 'Test' }]; },
    }),
    confirm: async () => { confirmations += 1; return true; },
    emit: () => {},
  });

  const result = await runner.models();

  assert.equal(result.state, 'plan_usage_not_granted');
  assert.equal(confirmations, 0);
  assert.equal(modelRequests, 0);
});

test('model listing requires confirmation and omits unexpected provider fields', async () => {
  let modelRequests = 0;
  let confirmationText = '';
  const runner = createCommandRunner({
    client: clientFor({
      getSession: async () => ({ status: 'connected', sharing: true }),
      listModels: async () => {
        modelRequests += 1;
        return [{ slug: 'account-model-2', displayName: 'Listed model', internalToken: 'never-output' }];
      },
    }),
    confirm: async (message) => { confirmationText = message; return true; },
    emit: () => {},
  });

  const result = await runner.models();

  assert.equal(modelRequests, 1);
  assert.match(confirmationText, /model catalog/i);
  assert.equal(result.state, 'listed');
  assert.deepEqual(result.models, [{ slug: 'account-model-2', displayName: 'Listed model' }]);
});

test('smoke requires an explicit confirmation before using the discovered model', async () => {
  let modelRequests = 0;
  let inferenceRequests = 0;
  let confirmationText = '';
  const runner = createCommandRunner({
    client: clientFor({
      getSession: async () => ({ status: 'connected', sharing: true }),
      listModels: async () => { modelRequests += 1; return [{ slug: 'account-model-1', displayName: 'Available model' }]; },
      streamResponse: async (options) => { inferenceRequests += 1; return { text: `completed ${options.model}` }; },
    }),
    confirm: async (message) => { confirmationText = message; return false; },
    emit: () => {},
  });

  const result = await runner.smoke();

  assert.equal(result.state, 'cancelled');
  assert.match(confirmationText, /synthetic prompt/i);
  assert.equal(modelRequests, 0);
  assert.equal(inferenceRequests, 0);
});

test('confirmed smoke sends one synthetic prompt to a dynamically discovered model', async () => {
  const requests = [];
  const emitted = [];
  const runner = createCommandRunner({
    client: clientFor({
      getSession: async () => ({ status: 'connected', sharing: true }),
      listModels: async () => [{ slug: 'account-model-7', displayName: 'Visible model' }],
      streamResponse: async (options) => { requests.push(options); return { text: 'Synthetic check complete.' }; },
    }),
    confirm: async () => true,
    emit: (event) => emitted.push(event),
  });

  const result = await runner.smoke();

  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0], {
    model: 'account-model-7',
    input: 'Say exactly: SIWC smoke test complete.',
    instructions: 'Reply with exactly the requested text.',
  });
  assert.equal(result.state, 'completed');
  assert.equal(result.model.slug, 'account-model-7');
  assert.match(JSON.stringify(emitted), /Synthetic check complete/);
});

test('smoke never reports completion when the SDK returns empty text', async () => {
  const runner = createCommandRunner({
    client: clientFor({
      getSession: async () => ({ status: 'connected', sharing: true }),
      listModels: async () => [{ slug: 'account-model-7', displayName: 'Visible model' }],
      streamResponse: async () => ({ text: '   ' }),
    }),
    confirm: async () => true,
    emit: () => {},
  });

  const result = await runner.smoke();

  assert.equal(result.state, 'empty_response');
});

test('smoke errors do not echo exception messages that might contain credentials', async () => {
  const runner = createCommandRunner({
    client: clientFor({
      getSession: async () => ({ status: 'connected', sharing: true }),
      listModels: async () => [{ slug: 'account-model-7', displayName: 'Visible model' }],
      streamResponse: async () => { throw new Error('failed with access-token-secret'); },
    }),
    confirm: async () => true,
    emit: () => {},
  });

  const result = await runner.smoke();

  assert.equal(result.state, 'error');
  assert.doesNotMatch(JSON.stringify(result), /access-token-secret/);
});
