import assert from 'node:assert/strict';
import test from 'node:test';
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { networkInterfaces, tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';
import { mobileOrigins } from '../src/serve/setup.js';

test('Settings probes installed CLI versions and actual sandbox launch only when requested, behind authentication', { skip: process.platform === 'win32' }, async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-setup-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const bin = join(home, 'bin');
  await mkdir(bin);
  const path = process.env.PATH;
  t.after(() => { process.env.PATH = path; });
  const probes = join(home, 'probes');
  for (const [name, output] of [['codex', 'echo "codex 1.2.3"'], ['claude', 'echo "version failed" >&2; exit 1'], ['pi', 'exit 0'], ['bwrap', 'exit 1']]) {
    const file = join(bin, name!);
    await writeFile(file, `#!/bin/sh\necho '${name}' >> '${probes}'\n${output}\n`);
    await chmod(file, 0o755);
  }
  process.env.PATH = bin;
  const store = new Store(home);
  const served = await serve({ store, callAgent: async () => { throw new Error('No agent call expected'); }, backend: 'claude', model: 'first-model', port: 0, host: '0.0.0.0', token: 'test token', web: home });
  t.after(() => served.close());
  const url = served.url.replace('0.0.0.0', '127.0.0.1');
  const get = (path: string, init?: RequestInit) => fetch(url + path, { ...init, headers: { authorization: 'Bearer test token' } });
  const response = await get('/api/daemons', { method: 'POST' });
  const { sid } = await response.json() as { sid: string };
  assert.deepEqual([store.load(sid).backend, store.load(sid).model], ['claude', 'first-model']);
  await get(`/api/projects/${sid}/snapshot`);
  await get(`/api/projects/${sid}/config`);
  await assert.rejects(readFile(probes), { code: 'ENOENT' });
  assert.equal((await fetch(url + '/api/system/setup')).status, 401);
  const statusResponse = await get('/api/system/setup');
  const status = await statusResponse.json() as { backends: { backend: string; installed: boolean; version: string | null; detail: string }[]; sandbox: { bubblewrap: boolean; platform: string }; mobile_origins: string[] };
  assert.deepEqual(status.backends.map(row => [row.backend, row.installed, row.version]), [
    ['codex', true, 'codex 1.2.3'], ['claude', true, null], ['copilot', false, null], ['pi', true, null],
  ]);
  assert.match(status.backends[1]!.detail, /version failed/);
  assert.match(status.backends[2]!.detail, /not found on PATH/);
  assert.match(status.backends[3]!.detail, /no output/);
  assert.deepEqual(status.sandbox, { bubblewrap: false, platform: process.platform });
  const port = Number(new URL(url).port);
  assert.deepEqual(status.mobile_origins, [...new Set(Object.values(networkInterfaces()).flatMap(entries => entries ?? []).filter(entry => !entry.internal && entry.family === 'IPv4').map(entry => `http://${entry.address}:${port}`))]);
  assert.ok(!JSON.stringify(status).includes('test token'));
  assert.deepEqual((await readFile(probes, 'utf8')).trim().split('\n').sort(), ['codex', 'claude', 'pi', ...process.platform === 'linux' ? ['bwrap'] : []].sort());
  await get(`/api/projects/${sid}/snapshot`);
  assert.equal((await readFile(probes, 'utf8')).trim().split('\n').length, process.platform === 'linux' ? 4 : 3);
});

test('phone addresses follow the listen address and never offer wildcard or loopback hosts', () => {
  for (const host of ['127.0.0.1', '127.0.1.1', 'localhost', '::1']) assert.deepEqual(mobileOrigins(host, 123), []);
  assert.deepEqual(mobileOrigins('192.168.1.5', 123), ['http://192.168.1.5:123']);
  assert.deepEqual(mobileOrigins('2001:db8::1', 123), ['http://[2001:db8::1]:123']);
  assert.ok(mobileOrigins('0.0.0.0', 123).every(origin => !origin.includes('0.0.0.0') && !origin.includes('127.0.0.1')));
});
