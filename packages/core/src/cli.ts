#!/usr/bin/env node
// `argus`: the web cockpit, where projects are started, talked to and followed. The work of its
// projects runs in this process.
import { randomBytes } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { callAgent } from './agent/call.js';
import { notInstalled } from './agent/sandbox.js';
import { serve } from './serve/index.js';
import { add, catalog, installed, remove } from './packs.js';
import { Store } from './store.js';
import { BACKENDS, type Backend } from './types.js';

const USAGE = `Usage: argus [--host H] [--port N] [--preview-url URL] [--backend ${BACKENDS.join('|')}] [--model M] [--web DIR] [--version]
       argus skills [add|remove <name>]
Serves the web cockpit on 127.0.0.1, or on --host (0.0.0.0: every network), at the address it prints.
Local previews use <identifier>.localhost on the browser-facing cockpit port.
LAN/mobile previews fall back to port + 1 with shared preview storage.
--preview-url is an HTTP(S) origin template, e.g. https://js-trial1-*.argusbot.cn.
Its entire hostname namespace is static-only; the cockpit hostname must be outside it.
Forwarding must preserve Host and Origin.
The team works through the --backend CLI (codex unless chosen), which must be installed and signed in.
Work stops when this process exits; the next start resumes projects that were running. Closing the browser leaves work running.
ARGUS_WEB_TOKEN: the token the cockpit asks for; unset, the one kept in the state root. ARGUS_HOME: the state root (~/.argus).
`;

async function main(): Promise<void> {
  const { values, positionals } = parseArgs({ allowPositionals: true, options: {
    host: { type: 'string', default: '127.0.0.1' }, port: { type: 'string', default: '8799' },
    'preview-url': { type: 'string' },
    backend: { type: 'string', default: 'codex' }, model: { type: 'string' }, web: { type: 'string' }, help: { type: 'boolean', short: 'h' },
    version: { type: 'boolean', short: 'v' },
  } });
  if (values.help || values.version) {
    process.stdout.write(values.help ? USAGE : `${(JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')) as { version: string }).version}\n`);
    return;
  }
  if (positionals.length) {
    const store = new Store();
    const [command, action, name] = positionals;
    if (command !== 'skills' || positionals.length > 3) throw new Error(USAGE);
    if (!action) {
      for (const pack of await catalog()) {
        process.stdout.write(`${pack.name}\t${pack.version}\t${installed(store.home, pack.name) ?? '-'}\t${pack.description_zh ?? pack.description}\t${pack.needs.join(', ')}\n`);
      }
    } else if (name && action === 'add') await add(store.home, name);
    else if (name && action === 'remove') await remove(store.home, name);
    else throw new Error(USAGE);
    return;
  }
  const backend = values.backend as Backend;
  if (!BACKENDS.includes(backend)) throw new Error(`--backend must be one of ${BACKENDS.join(', ')}`);
  const store = new Store();
  const release = store.own();
  // Always a token: the roles' sandbox shares this machine's network, so the cockpit's address
  // alone must not let a role, or a page the operator visits, start work.
  const token = process.env.ARGUS_WEB_TOKEN || kept(join(store.home, 'web-token'));
  const served = await serve({
    store, callAgent, backend, model: values.model ?? null, host: values.host, port: Number(values.port), token,
    ...(values['preview-url'] !== undefined ? { previewUrl: values['preview-url'] } : {}),
    web: resolve(values.web ?? fileURLToPath(new URL('../web', import.meta.url))),
  }).catch((error: unknown) => { release(); throw error; });
  process.stdout.write(`Argus is at ${served.url}/?token=${encodeURIComponent(token)}\n`);
  process.stdout.write('Work stops when this process exits; the next start resumes projects that were running. Closing the browser leaves work running.\n');
  const missing = notInstalled(backend);
  if (missing) process.stdout.write(`${missing}, then sign in to it.\n`);
  const stop = (): void => { void served.close().finally(release); };
  process.once('SIGINT', stop).once('SIGTERM', stop);
}

/** The token made on the first start and kept, so a restart leaves the open pages signed in. The
 * sandbox shows the roles a private state root, without it. */
function kept(file: string): string {
  try { return readFileSync(file, 'utf8').trim(); } catch { /* none yet */ }
  const token = randomBytes(16).toString('hex');
  writeFileSync(file, token, { mode: 0o600 });
  return token;
}

main().catch(error => { process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`); process.exitCode = 1; });
