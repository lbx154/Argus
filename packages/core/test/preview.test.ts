import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { request as httpRequest } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';

async function setup(t: test.TestContext, options: { host?: string; previewUrl?: string } = {}) {
  const home = await mkdtemp(join(tmpdir(), 'argus-preview-host-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  await writeFile(join(project.workdir, 'index.html'), '<p>preview</p>');
  const server = await serve({ store, callAgent: () => assert.fail('no backend call'), backend: 'codex', model: null, port: 0, web: home, ...options });
  t.after(() => server.close());
  const path = `/api/projects/${project.id}/artifact/preview?path=index.html`;
  const request = (host: string, pathName = path, method = 'GET', headers = {}) => new Promise<{ status: number; body: string }>((resolve, reject) => {
    const req = httpRequest(server.url + pathName, { method, headers: { host, ...headers } }, response => {
      let body = '';
      response.on('data', chunk => { body += String(chunk); });
      response.on('end', () => resolve({ status: response.statusCode!, body }));
    });
    req.on('error', reject); req.end();
  });
  return { server, path, request };
}

for (const host of ['127.0.0.1', 'localhost']) test(`previews swap ${host} and share the actual cockpit port`, async t => {
  const { server, path, request } = await setup(t, { host });
  const port = new URL(server.url).port;
  const other = host === '127.0.0.1' ? 'localhost' : '127.0.0.1';
  const plain = await (await fetch(server.url + path)).json() as { url: string };
  assert.equal(new URL(plain.url).origin, `http://${other}:${port}`);
  assert.equal(await (await fetch(plain.url)).text(), '<p>preview</p>');
  const proxy = JSON.parse((await request(`${host}:5173`, path, 'GET', { 'x-forwarded-proto': 'https' })).body) as { url: string };
  assert.equal(new URL(proxy.url).origin, `https://${other}:${port}`);
  assert.equal(new URL(proxy.url).pathname, new URL(plain.url).pathname);
  for (const endpoint of ['/api/meta', '/api/daemons', '/', '/unknown/index.html']) {
    assert.equal((await request(`${other}:${port}`, endpoint)).status, 404);
    assert.equal((await request(`${other}:${port}`, endpoint, 'POST', { authorization: 'Bearer secret' })).status, 404);
  }
  assert.equal((await request(`${host}:${port}`, '/api/meta', 'GET', { origin: `http://${host}:${port}` })).status, 200);
  assert.equal((await request(`${host}:${port}`, '/api/meta', 'GET', { origin: new URL(plain.url).origin })).status, 403);
});

test('non-loopback hosts receive a configuration message and retain cockpit access', async t => {
  const { request } = await setup(t);
  for (const host of ['cockpit.example.com', '[::1]:8799', '192.168.1.2:8799']) {
    const response = await request(host);
    assert.equal(response.status, 200);
    const value = JSON.parse(response.body) as { message: string; url?: string };
    assert.match(value.message, /--preview-url/);
    assert.equal(value.url, undefined);
    assert.equal((await request(host, '/api/meta')).status, 200);
  }
});

test('--preview-url routes only keyed static files on its hostname', async t => {
  const { request, server } = await setup(t, { previewUrl: 'https://preview.example.com:9443' });
  const value = JSON.parse((await request('cockpit.example.com')).body) as { url: string };
  const url = new URL(value.url);
  assert.equal(url.origin, 'https://preview.example.com:9443');
  assert.match(url.pathname, /^\/[A-Za-z0-9_-]{32}\/index.html$/);
  for (const host of ['preview.example.com', 'PREVIEW.EXAMPLE.COM:9443', `preview.example.com:${new URL(server.url).port}`]) {
    assert.deepEqual(await request(host, url.pathname), { status: 200, body: '<p>preview</p>' });
    for (const endpoint of ['/api/meta', '/', '/wrong/index.html']) assert.equal((await request(host, endpoint)).status, 404);
  }
  for (const origin of [url.origin, `http://preview.example.com:${new URL(server.url).port}`]) {
    assert.equal((await request('cockpit.example.com', '/api/meta', 'GET', { origin })).status, 403);
  }
});

test('--preview-url rejects credentials, non-HTTP protocols, paths, queries and fragments', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-preview-invalid-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  for (const previewUrl of ['https://user:pass@preview.example.com', 'file:///tmp/', 'https://preview.example.com/page', 'https://preview.example.com/?key=secret', 'https://preview.example.com/#fragment', 'invalid']) {
    await assert.rejects(serve({ store: new Store(home), callAgent: () => assert.fail('no backend call'), backend: 'codex', model: null, port: 0, web: home, previewUrl }));
  }
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
