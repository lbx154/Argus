import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createServer, type AddressInfo } from 'node:net';
import { get } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';

test('previews use the cockpit port plus one and preserve the request protocol and hostname', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-preview-port-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  await writeFile(join(project.workdir, 'index.html'), '<p>preview</p>');
  const probe = createServer().listen(0, '127.0.0.1');
  await once(probe, 'listening');
  const port = (probe.address() as AddressInfo).port;
  await new Promise<void>(resolve => probe.close(() => resolve()));
  const server = await serve({ store, callAgent: () => assert.fail('no backend call'), backend: 'codex', model: null, port, web: home });
  t.after(() => server.close());
  const path = `/api/projects/${project.id}/artifact/preview?path=index.html`;
  const plain = await (await fetch(server.url + path)).json() as { url: string };
  assert.equal(new URL(plain.url).origin, `http://127.0.0.1:${port + 1}`);
  assert.equal(await (await fetch(plain.url)).text(), '<p>preview</p>');
  const proxy = await new Promise<{ url: string }>((resolve, reject) => {
    get(server.url + path, { headers: { host: 'cockpit.example.com', 'x-forwarded-proto': 'https' } }, response => {
      let body = '';
      response.on('data', chunk => { body += String(chunk); });
      response.on('end', () => resolve(JSON.parse(body) as { url: string }));
    }).on('error', reject);
  });
  assert.equal(new URL(proxy.url).origin, `https://cockpit.example.com:${port + 1}`);
  assert.equal(new URL(proxy.url).pathname, new URL(plain.url).pathname);
});

test('an occupied preview port fails startup and names the listener and port', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-preview-occupied-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const occupied = createServer().listen(0, '127.0.0.1');
  await once(occupied, 'listening');
  t.after(() => new Promise<void>(resolve => occupied.close(() => resolve())));
  const port = (occupied.address() as AddressInfo).port;
  await assert.rejects(serve({ store: new Store(home), callAgent: () => assert.fail('no backend call'), backend: 'codex', model: null, port: port - 1, web: home }),
    new RegExp(`Preview server could not listen on 127.0.0.1:${port}: .*EADDRINUSE`));
});

test('the CLI accepts --preview-url and rejects that origin at the cockpit API, without a backend installed', { timeout: 15_000 }, async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-preview-cli-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const cli = spawn(process.execPath, ['packages/core/dist/argus.mjs', '--port', '0', '--preview-url', 'https://preview.example.com', '--web', home],
    { env: { ...process.env, ARGUS_HOME: home, ARGUS_WEB_TOKEN: 'preview-test', PATH: '' } });
  const ended = once(cli, 'exit');
  t.after(async () => { cli.kill(); await ended; });
  let output = '';
  const url = await new Promise<string>((resolve, reject) => {
    cli.stdout.on('data', chunk => {
      output += String(chunk);
      const address = /Argus is at (http:\/\/[^/\s]+)/.exec(output)?.[1];
      if (address) resolve(address);
    });
    cli.stderr.on('data', chunk => { output += String(chunk); });
    cli.once('error', reject);
    cli.once('exit', () => reject(new Error(output)));
  });
  const headers = { authorization: 'Bearer preview-test' };
  const made = await fetch(url + '/api/daemons', { method: 'POST', headers });
  assert.equal(made.status, 200);
  const { sid } = await made.json() as { sid: string };
  const project = new Store(home).load(sid);
  assert.equal(project.model, null);
  await writeFile(join(project.workdir, 'index.html'), '<p>proxied preview</p>');
  const result = await fetch(`${url}/api/projects/${sid}/artifact/preview?path=index.html`, { headers });
  assert.equal(result.status, 200);
  const { url: preview } = await result.json() as { url: string };
  assert.equal(new URL(preview).origin, 'https://preview.example.com');
  assert.match(new URL(preview).pathname, /^\/[A-Za-z0-9_-]{32}\/index.html$/);
  assert.equal((await fetch(`${url}/api/projects/${sid}/daemon/stop`, { method: 'POST', headers: { ...headers, origin: 'https://preview.example.com', 'content-type': 'text/plain' }, body: '{}' })).status, 403);
  assert.equal((await fetch(url + '/api/meta', { headers: { ...headers, origin: 'https://preview.example.com' } })).status, 403);
  assert.equal((await fetch(`${url}/api/projects/${sid}/daemon/stop`, { method: 'POST', headers: { ...headers, origin: 'https://cockpit.example.com' } })).status, 200);
});
