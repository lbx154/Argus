import assert from 'node:assert/strict';
import { execFile, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import test from 'node:test';
import { mergeEnv } from '../src/commands.js';
import { Flow } from '../src/flow.js';
import { packSetups, prepare } from '../src/packSetup.js';
import { serve } from '../src/serve/index.js';
import { Store } from '../src/store.js';
import type { AgentRequest } from '../src/types.js';

const run = promisify(execFile);
const root = fileURLToPath(new URL('../../../', import.meta.url));
const catalog = 'https://raw.githubusercontent.com/Argus-AiTeam/argus-verticals/main/packs/';

test('a local pack goes through the CLI and authenticated HTTP, sets up once and runs by name in every role environment', async t => {
  const scratch = mkdtempSync(join(tmpdir(), 'argus portable 中文 '));
  t.after(() => rmSync(scratch, { recursive: true, force: true }));
  const home = join(scratch, 'state home 中文');
  const workspace = join(scratch, 'workspace 中文 space');
  mkdirSync(workspace);
  const shared = process.env.ARGUS_SHARED;
  delete process.env.ARGUS_SHARED;
  t.after(() => { if (shared !== undefined) process.env.ARGUS_SHARED = shared; });
  const body = '---\r\nname: portable\r\ndescription: Check native setup and command environments\r\nversion: 1\r\nsetup: setup/install.cjs\r\n---\r\n';
  const setup = `#!/usr/bin/env node
const fs = require('node:fs'), path = require('node:path');
const home = process.env.ARGUS_PACK_HOME, bin = path.join(home, 'bin');
fs.mkdirSync(bin, {recursive:true});
fs.appendFileSync(path.join(home, 'runs'), 'run\\n');
fs.writeFileSync(path.join(bin, 'payload.cjs'), 'console.log(JSON.stringify({workspace:process.env.PROBE_WORKSPACE,args:process.argv.slice(2)}))');
const win = process.platform === 'win32';
const command = win ? '@echo off\\r\\n"' + process.execPath + '" "%~dp0payload.cjs" %*\\r\\n' : '#!/bin/sh\\n"' + process.execPath + '" "$(dirname "$0")/payload.cjs" "$@"\\n';
fs.writeFileSync(path.join(bin, win ? 'pack-probe.cmd' : 'pack-probe'), command, {mode:0o755});
fs.writeFileSync(path.join(home, 'env'), (win ? 'Path' : 'PATH') + '=\u0024{ARGUS_PACK_HOME}/bin\u0024{PATH_SEPARATOR}\u0024{PATH}\\r\\nPROBE_WORKSPACE=\u0024{WORKSPACE}\\r\\n');
`;
  const files: Record<string, string> = { 'portable/SKILL.md': body, 'portable/setup/install.cjs': setup };
  files['catalog.json'] = JSON.stringify({ packs: [{ name: 'portable', version: '1', description: 'Check native setup and command environments', requires: [], needs: [], files: Object.entries(files).map(([path, text]) => ({ path: path.slice('portable/'.length), size: Buffer.byteLength(text) })) }] });
  const source = createServer((request, response) => {
    const text = files[decodeURIComponent(request.url!.slice(1))];
    response.writeHead(text === undefined ? 404 : 200);
    response.end(text ?? 'not found');
  });
  source.listen(0, '127.0.0.1');
  await once(source, 'listening');
  t.after(() => new Promise<void>((done, reject) => source.close(error => error ? reject(error) : done())));
  const sourceURL = `http://127.0.0.1:${(source.address() as { port: number }).port}/`;
  // The product's catalog stays fixed. Only this test process and its CLI child redirect downloads
  // to an actual local HTTP fixture; size validation, staging, parsing and setup are unmodified.
  const redirect = `const nativeFetch = globalThis.fetch; globalThis.fetch = (input, init) => nativeFetch(String(input).startsWith(${JSON.stringify(catalog)}) ? ${JSON.stringify(sourceURL)} + String(input).slice(${catalog.length}) : input, init);`;
  const cli = (...args: string[]) => run(process.execPath, ['--import', `data:text/javascript,${encodeURIComponent(redirect)}`, '--import', 'tsx', 'packages/core/src/cli.ts', ...args], { cwd: root, env: mergeEnv(process.env, { ARGUS_HOME: home, ARGUS_SHARED: '' }) });
  const nativeFetch = globalThis.fetch;
  t.mock.method(globalThis, 'fetch', (input: string | URL | Request, init?: RequestInit) => nativeFetch(String(input).startsWith(catalog) ? sourceURL + String(input).slice(catalog.length) : input, init));
  await cli('skills', 'add', 'portable');
  assert.match((await cli('skills')).stdout, /portable\t1\t1/);
  assert.equal(packSetups(home)[0]!.state, 'pending', 'installing through CLI does not start arbitrary code');
  const store = new Store(home);
  const requests: AgentRequest[] = [];
  const roles = ['manager', 'engineer', 'reviewer', 'manager'];
  const destinations = ['engineer', 'reviewer', 'manager', 'operator'];
  const flow = new Flow(store, async request => {
    const index = requests.length;
    requests.push(request);
    assert.equal(request.role, roles[index]);
    const output = spawnSync(process.platform === 'win32' ? process.env.ComSpec ?? 'cmd.exe' : '/bin/sh',
      process.platform === 'win32' ? ['/d', '/v:off', '/s', '/c', 'pack-probe "中文 space"'] : ['-c', 'pack-probe "中文 space"'],
      { env: mergeEnv(process.env, request.env ?? {}), cwd: request.cwd, encoding: 'utf8', windowsVerbatimArguments: process.platform === 'win32' });
    assert.ifError(output.error);
    assert.equal(output.status, 0, output.stderr);
    assert.deepEqual(JSON.parse(output.stdout.trim()), { workspace, args: ['中文 space'] });
    const path = request.env!.PATH!;
    assert.equal(path.split(delimiter)[0], join(home, 'tools'));
    assert.equal(path.split(delimiter)[1], `${join(home, 'pack-homes', 'portable', '1')}/bin`);
    return { callId: `native-${index}`, threadId: null, completed: true, error: null, model: 'scripted', usage: null, ms: 1,
      handoff: { form: request.form, to: destinations[index]!, letters: ['native command checked'], open: [] } };
  });
  const project = store.create({ workdir: workspace, request: '', backend: 'codex', model: null });
  await flow.say(project.id, 'Check native pack command');
  await flow.settled(project.id);
  assert.equal(requests.length, 4);
  const [ready] = await prepare(home);
  assert.equal(ready!.state, 'ready');
  assert.equal(readFileSync(join(ready!.home, 'runs'), 'utf8'), 'run\n');
  assert.ok(requests[1]!.writeDirs!.includes(ready!.home));
  assert.ok(requests[2]!.readDirs!.includes(ready!.home));
  assert.ok(!requests[2]!.writeDirs!.includes(ready!.home));
  const server = await serve({ store, callAgent: async () => { throw new Error('No model expected'); }, backend: 'codex', model: null, port: 0, token: 'ephemeral-fixture-token', web: home });
  t.after(() => server.close());
  const call = (path: string, value?: object, authorized = true) => nativeFetch(server.url + path, { method: value ? 'POST' : 'GET', headers: { ...(authorized ? { authorization: 'Bearer ephemeral-fixture-token' } : {}), 'content-type': 'application/json' }, ...(value ? { body: JSON.stringify(value) } : {}) });
  assert.equal((await call('/api/skill-packs/add', { name: 'portable' }, false)).status, 401);
  assert.equal((await call('/api/skill-packs/remove', { name: 'portable' })).status, 200);
  assert.equal((await call('/api/skill-packs/add', { name: 'portable' })).status, 200);
  await prepare(home);
  const listing = await (await call('/api/skill-packs')).json();
  assert.equal(listing.packs[0].installed_version, '1');
  assert.equal((await (await call('/api/skill-library')).json()).items.find((item: { name: string }) => item.name === 'portable').source, 'store');
  assert.equal(readFileSync(join(ready!.home, 'runs'), 'utf8'), 'run\n', 'reinstall of same version does not rerun setup');
  await cli('skills', 'remove', 'portable');
  assert.match((await cli('skills')).stdout, /portable\t1\t-/);
  assert.equal(packSetups(home).length, 0);
});
