#!/usr/bin/env node
// `argus`: the web cockpit, where projects are started, talked to and followed. The work of its
// projects runs in this process.
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { callAgent } from './agent/call.js';
import { serve } from './serve/index.js';
import { Store } from './store.js';
import { BACKENDS, type Backend } from './types.js';

const USAGE = `Usage: argus [--port N] [--backend codex|claude|copilot|pi] [--model M] [--web DIR]
Serves the web cockpit on 127.0.0.1. ARGUS_WEB_TOKEN: require this bearer token. ARGUS_HOME: the state root (~/.argus).
`;

async function main(): Promise<void> {
  const { values } = parseArgs({ options: {
    port: { type: 'string', default: '8799' }, backend: { type: 'string', default: 'codex' }, model: { type: 'string' },
    web: { type: 'string' }, help: { type: 'boolean', short: 'h' },
  } });
  if (values.help) {
    process.stdout.write(USAGE);
    return;
  }
  const backend = values.backend as Backend;
  if (!BACKENDS.includes(backend)) throw new Error(`--backend must be one of ${BACKENDS.join(', ')}`);
  const store = new Store();
  const release = store.own();
  const token = process.env.ARGUS_WEB_TOKEN || undefined;
  const served = await serve({
    store, callAgent, backend, model: values.model ?? null, port: Number(values.port), ...(token ? { token } : {}),
    web: resolve(values.web ?? fileURLToPath(new URL('../web', import.meta.url))),
  }).catch((error: unknown) => { release(); throw error; });
  process.stdout.write(`Argus is at ${served.url}/${token ? `?token=${encodeURIComponent(token)}` : ''}\n`);
  const stop = (): void => { void served.close().finally(release); };
  process.once('SIGINT', stop).once('SIGTERM', stop);
}

main().catch(error => { process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`); process.exitCode = 1; });
