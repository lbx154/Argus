#!/usr/bin/env node
// `argus`: the web cockpit, where projects are started, talked to and followed. The work of its
// projects runs in this process.
import { randomBytes } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { callAgent } from './agent/call.js';
import { serve } from './serve/index.js';
import { Store } from './store.js';
import { BACKENDS, type Backend } from './types.js';

const USAGE = `Usage: argus [--host H] [--port N] [--backend codex|claude|copilot|pi] [--model M] [--web DIR]
Serves the web cockpit on 127.0.0.1, or on --host (0.0.0.0: every network), at the address it prints.
ARGUS_WEB_TOKEN: the token the cockpit asks for, a new one each start when unset. ARGUS_HOME: the state root (~/.argus).
`;

async function main(): Promise<void> {
  const { values } = parseArgs({ options: {
    host: { type: 'string', default: '127.0.0.1' }, port: { type: 'string', default: '8799' },
    backend: { type: 'string', default: 'codex' }, model: { type: 'string' }, web: { type: 'string' }, help: { type: 'boolean', short: 'h' },
  } });
  if (values.help) {
    process.stdout.write(USAGE);
    return;
  }
  const backend = values.backend as Backend;
  if (!BACKENDS.includes(backend)) throw new Error(`--backend must be one of ${BACKENDS.join(', ')}`);
  const store = new Store();
  const release = store.own();
  // Always a token: the roles' sandbox shares this machine's network, so the cockpit's address
  // alone must not let a role, or a page the operator visits, start work.
  const token = process.env.ARGUS_WEB_TOKEN || randomBytes(16).toString('hex');
  const served = await serve({
    store, callAgent, backend, model: values.model ?? null, host: values.host, port: Number(values.port), token,
    web: resolve(values.web ?? fileURLToPath(new URL('../web', import.meta.url))),
  }).catch((error: unknown) => { release(); throw error; });
  process.stdout.write(`Argus is at ${served.url}/?token=${encodeURIComponent(token)}\n`);
  const stop = (): void => { void served.close().finally(release); };
  process.once('SIGINT', stop).once('SIGTERM', stop);
}

main().catch(error => { process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`); process.exitCode = 1; });
