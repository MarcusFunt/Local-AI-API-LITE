import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { createChatGPT } from '@siwc/local';
import { createWindowsDpapiCredentialEncryption } from './dpapi-encryption.mjs';

export function makeHostConfig({
  platform = process.platform,
  env = process.env,
  homeDirectory = homedir(),
  credentialEncryption = createWindowsDpapiCredentialEncryption(),
} = {}) {
  if (platform !== 'win32') throw new Error('This local SIWC host is supported on Windows only.');
  const localAppData = typeof env.LOCALAPPDATA === 'string' && env.LOCALAPPDATA.trim()
    ? resolve(env.LOCALAPPDATA)
    : join(homeDirectory, 'AppData', 'Local');

  return {
    appName: 'Local AI API LITE',
    appId: 'local-ai-api-lite',
    redirectPort: 0,
    storageDir: join(localAppData, 'Local-AI-API-LITE', 'siwc'),
    credentialEncryption,
    sendHostId: true,
  };
}

export function createLocalChatGPTClient(options = {}) {
  const createClient = options.createClient ?? createChatGPT;
  return createClient(makeHostConfig(options));
}
