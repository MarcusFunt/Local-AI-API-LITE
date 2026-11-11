const SMOKE_PROMPT = 'Say exactly: SIWC smoke test complete.';
const SMOKE_INSTRUCTIONS = 'Reply with exactly the requested text.';
const VALID_SESSION_STATES = new Set(['disconnected', 'connecting', 'connected', 'reauth_required']);

function safeDisplay(value, maxLength = 200) {
  if (typeof value !== 'string' || value.length > maxLength || /[\u0000-\u001f]/.test(value)) return undefined;
  return value;
}

function safeErrorCode(error) {
  const code = error && typeof error === 'object' ? error.code : undefined;
  return typeof code === 'string' && /^[a-z][a-z0-9_]{0,63}$/i.test(code) ? code : 'request_failed';
}

export function publicSession(session) {
  const status = VALID_SESSION_STATES.has(session?.status) ? session.status : 'disconnected';
  const sharing = status === 'connected' && session?.sharing === true;
  return {
    status,
    planUsage: status !== 'connected' ? 'unavailable' : sharing ? 'authorized' : 'identity_only',
    inferenceReady: sharing,
    ...(safeDisplay(session?.profileLabel) ? { profileLabel: safeDisplay(session.profileLabel) } : {}),
    ...(session?.error ? { errorCode: safeErrorCode(session.error) } : {}),
  };
}

function publish(emit, event) {
  emit(event);
  return event;
}

function safeFailure(command, error, emit) {
  return publish(emit, { command, state: 'error', category: safeErrorCode(error) });
}

export function createCommandRunner({ client, confirm = async () => false, emit = () => {} }) {
  if (!client || typeof client.getSession !== 'function' || typeof client.signIn !== 'function' ||
      typeof client.listModels !== 'function' || typeof client.streamResponse !== 'function') {
    throw new TypeError('A local SIWC client is required.');
  }

  return {
    async status() {
      try {
        return publish(emit, { command: 'status', session: publicSession(await client.getSession()) });
      } catch (error) {
        return safeFailure('status', error, emit);
      }
    },

    async connect() {
      let accepted;
      try {
        accepted = await confirm('Open the system browser to connect ChatGPT? The request will ask for identity and may offer plan token sharing. No prompt or conversation will be sent.');
      } catch (error) {
        return safeFailure('connect', error, emit);
      }
      if (accepted !== true) return publish(emit, { command: 'connect', state: 'cancelled' });
      try {
        return publish(emit, { command: 'connect', session: publicSession(await client.signIn({ reconsent: true })) });
      } catch (error) {
        return safeFailure('connect', error, emit);
      }
    },

    async models() {
      let session;
      try {
        session = publicSession(await client.getSession());
      } catch (error) {
        return safeFailure('models', error, emit);
      }
      if (!session.inferenceReady) {
        const state = session.status === 'connected' ? 'plan_usage_not_granted' : 'connect_required';
        return publish(emit, { command: 'models', state, session });
      }

      let accepted;
      try {
        accepted = await confirm('This queries the available model catalog for the connected ChatGPT account. No prompt will be sent. Continue?');
      } catch (error) {
        return safeFailure('models', error, emit);
      }
      if (accepted !== true) return publish(emit, { command: 'models', state: 'cancelled', session });

      try {
        const available = await client.listModels();
        const models = Array.isArray(available) ? available.flatMap((entry) => {
          const slug = safeDisplay(entry?.slug);
          const displayName = safeDisplay(entry?.displayName);
          return slug && displayName ? [{ slug, displayName }] : [];
        }) : [];
        return publish(emit, {
          command: 'models',
          state: models.length ? 'listed' : 'no_models',
          session,
          models,
        });
      } catch (error) {
        return safeFailure('models', error, emit);
      }
    },

    async smoke() {
      let session;
      try {
        session = publicSession(await client.getSession());
      } catch (error) {
        return safeFailure('smoke', error, emit);
      }
      if (!session.inferenceReady) {
        const state = session.status === 'connected' ? 'plan_usage_not_granted' : 'connect_required';
        return publish(emit, { command: 'smoke', state, session });
      }

      let accepted;
      try {
        accepted = await confirm('This sends one synthetic prompt to OpenAI using your ChatGPT plan. No conversation history is included. Continue?');
      } catch (error) {
        return safeFailure('smoke', error, emit);
      }
      if (accepted !== true) return publish(emit, { command: 'smoke', state: 'cancelled', session });

      let model;
      try {
        const models = await client.listModels();
        model = Array.isArray(models) ? models.find((entry) =>
          typeof entry?.slug === 'string' && entry.slug.length > 0 &&
          typeof entry?.displayName === 'string' && entry.displayName.length > 0) : undefined;
      } catch (error) {
        return safeFailure('smoke', error, emit);
      }
      if (!model) return publish(emit, { command: 'smoke', state: 'no_models', session });

      try {
        const result = await client.streamResponse({
          model: model.slug,
          input: SMOKE_PROMPT,
          instructions: SMOKE_INSTRUCTIONS,
        });
        if (typeof result?.text !== 'string' || !result.text.trim()) {
          return publish(emit, { command: 'smoke', state: 'empty_response', model: { slug: model.slug, displayName: model.displayName } });
        }
        if (result.text.length > 64_000) {
          return publish(emit, { command: 'smoke', state: 'oversized_response', model: { slug: model.slug, displayName: model.displayName } });
        }
        return publish(emit, {
          command: 'smoke',
          state: 'completed',
          model: { slug: model.slug, displayName: model.displayName },
          text: result.text,
        });
      } catch (error) {
        return safeFailure('smoke', error, emit);
      }
    },
  };
}
