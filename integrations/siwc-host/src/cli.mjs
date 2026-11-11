import { stdin, stdout } from 'node:process';
import { createInterface } from 'node:readline/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createCommandRunner } from './commands.mjs';
import { createLocalChatGPTClient } from './client.mjs';

const USAGE = 'Usage: npm start -- <status|connect|models|smoke>';

export async function runCli({
  argv = process.argv.slice(2),
  createClient = createLocalChatGPTClient,
  confirm = confirmInTerminal,
  write = (line) => stdout.write(`${line}\n`),
} = {}) {
  const command = argv[0];
  if (!['status', 'connect', 'models', 'smoke'].includes(command) || argv.length !== 1) {
    write(USAGE);
    return 2;
  }

  try {
    const runner = createCommandRunner({
      client: await createClient(),
      confirm,
      emit: (event) => write(JSON.stringify(event)),
    });
    const result = await runner[command]();
    return ['error', 'empty_response', 'oversized_response', 'no_models'].includes(result.state) ? 1 : 0;
  } catch {
    write(JSON.stringify({ command, state: 'error', category: 'host_unavailable' }));
    return 1;
  }
}

async function confirmInTerminal(message) {
  if (!stdin.isTTY || !stdout.isTTY) return false;
  const terminal = createInterface({ input: stdin, output: stdout });
  try {
    const answer = await terminal.question(`${message} [y/N] `);
    return /^y(?:es)?$/i.test(answer.trim());
  } finally {
    terminal.close();
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  process.exitCode = await runCli();
}
