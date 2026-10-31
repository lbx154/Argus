import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createServer, request as httpRequest } from 'node:http';
import { connect } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';

async function setup(t: test.TestContext, options: { host?: string; previewUrl?: string; token?: string } = {}) {
  const home = await mkdtemp(join(tmpdir(), 'argus-preview-host-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  await writeFile(join(project.workdir, 'index.html'), '<p>preview</p>');
  await writeFile(join(home, 'index.html'), '<p>cockpit</p>');
  const server = await serve({ store, callAgent: () => assert.fail('no backend call'), backend: 'codex', model: null, port: 0, web: home, ...options });
  t.after(() => server.close());
  const path = `/api/projects/${project.id}/artifact/preview?path=index.html`;
  const request = (host: string, pathName = path, method = 'GET', headers = {}) => new Promise<{ status: number; body: string }>((resolve, reject) => {
    const req = httpRequest(server.url + pathName, { method, setHost: false, headers: { host, ...(options.token ? { authorization: `Bearer ${options.token}` } : {}), ...headers } }, response => {
      let body = '';
      response.on('data', chunk => { body += String(chunk); });
      response.on('end', () => resolve({ status: response.statusCode!, body }));
    });
    req.on('error', reject); req.end();
  });
  return { server, path, request, store, project };
}

for (const host of ['127.0.0.1', 'localhost', '[::1]', '0.0.0.0', '[::]', '[::ffff:127.0.0.1]']) test(`cockpit Host ${host} without Origin retains cockpit and selects an isolated preview`, async t => {
  const { server, path, request, store, project } = await setup(t);
  const port = new URL(server.url).port;
  const plain = new URL(JSON.parse((await request(`${host}:${port}`)).body).url);
  assert.match(plain.hostname, /^[a-z0-9]{32}\.localhost$/);
  assert.equal(plain.protocol, 'http:');
  assert.equal(plain.port, port);
  assert.equal(plain.pathname, '/index.html');
  for (const cockpit of ['127.0.0.1', 'localhost', '[::1]']) {
    assert.deepEqual(await request(`${cockpit}:${port}`, '/'), { status: 200, body: '<p>cockpit</p>' });
    assert.equal((await request(`${cockpit}:${port}`, '/api/meta')).status, 200);
    assert.equal(JSON.parse((await request(`${cockpit}:${port}`)).body).url, plain.href);
  }
  const proxy = new URL(JSON.parse((await request(`${host}:9000`, path, 'GET', { 'x-forwarded-proto': 'https' })).body).url);
  assert.equal(proxy.origin, `http://${plain.hostname}:9000`);
  assert.deepEqual(await request(plain.host, plain.pathname), { status: 200, body: '<p>preview</p>' });
  assert.equal((await request(plain.host, '/', 'HEAD')).status, 200);
  assert.equal((await request('unknown.localhost', '/api/meta')).status, 404);
  for (const endpoint of ['/api/meta', '/api/daemons', '/unknown/index.html']) {
    for (const method of ['GET', 'POST']) assert.equal((await request(plain.host, endpoint, method)).status, 404);
  }
  assert.equal((await request(`${host}:${port}`, '/api/meta', 'GET', { origin: plain.origin })).status, 403);
  const second = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  await writeFile(join(second.workdir, 'index.html'), 'other');
  await writeFile(join(second.workdir, 'second.txt'), 'private');
  const secondUrl = new URL(JSON.parse((await request(`${host}:${port}`, `/api/projects/${second.id}/artifact/preview?path=index.html`)).body).url);
  assert.notEqual(secondUrl.origin, plain.origin);
  assert.equal((await request(plain.host, '/second.txt')).status, 404);
  assert.deepEqual(await request(secondUrl.host, '/second.txt'), { status: 200, body: 'private' });
  const delivery = new URL(JSON.parse((await request(`${host}:${port}`, path + '&delivery=d-new')).body).url);
  assert.notEqual(delivery.origin, plain.origin);
  assert.equal(project.id === second.id, false);
});

test('LAN access falls back to the independent keyed static port with no cockpit or API', async t => {
  const { request, server } = await setup(t);
  const value = new URL(JSON.parse((await request('192.168.1.2:9000')).body).url);
  assert.equal(value.hostname, '192.168.1.2');
  assert.equal(Number(value.port), Number(new URL(server.url).port) + 1);
  assert.match(value.pathname, /^\/[a-z0-9]{32}\/index.html$/);
  const base = `http://127.0.0.1:${value.port}`;
  assert.equal(await (await fetch(base + value.pathname)).text(), '<p>preview</p>');
  for (const endpoint of ['/', '/api/meta', '/api/daemons']) assert.equal((await fetch(base + endpoint)).status, 404);
  assert.equal((await request('localhost', '/api/meta', 'GET', { origin: value.origin })).status, 403);
  assert.equal((await request('192.168.1.2', '/api/meta')).status, 200);
});

test('--preview-url preserves a template hostname on real reading requests; Origin uses the same matcher', async t => {
  const { request, server } = await setup(t, { previewUrl: 'https://js-trial1-*.argusbot.cn:9443' });
  const url = new URL(JSON.parse((await request('localhost')).body).url);
  assert.match(url.hostname, /^js-trial1-[a-z0-9]{32}\.argusbot\.cn$/);
  assert.equal(url.origin, `https://${url.hostname}:9443`);
  assert.equal(url.pathname, '/index.html');
  for (const host of [url.host, url.host.toUpperCase(), `${url.hostname}.:9443`, `${url.hostname}:${new URL(server.url).port}`]) {
    assert.deepEqual(await request(host, url.pathname), { status: 200, body: '<p>preview</p>' });
    for (const endpoint of ['/api/meta', '/missing.html']) assert.equal((await request(host, endpoint)).status, 404);
  }
  const unknown = 'js-trial1-' + 'a'.repeat(32) + '.argusbot.cn';
  assert.equal((await request(unknown, '/')).status, 404);
  assert.equal((await request('js-trial1-unknown.argusbot.cn', '/api/meta')).status, 404);
  assert.equal((await request('localhost', '/api/meta', 'GET', { origin: 'https://js-trial1-unknown.argusbot.cn' })).status, 403);
  for (const origin of [url.origin, `https://${url.hostname}.:9443`, `http://${unknown}:80`]) assert.equal((await request('localhost', '/api/meta', 'GET', { origin })).status, 403);
  for (const host of ['localhost', '127.0.0.1', 'cockpit.argusbot.cn']) assert.equal((await request(host, '/api/meta')).status, 200);
});

test('malformed Host returns 400 without stack output and does not affect subsequent requests', async t => {
  const { request } = await setup(t);
  let errors = '';
  t.mock.method(process.stderr, 'write', (text: string | Uint8Array) => { errors += text; return true; });
  for (const host of ['[bad', 'localhost:bad', 'a/b', 'a@b', 'localhost:99999', '']) {
    assert.equal((await request(host, '/')).status, 400, host);
    assert.equal((await request(host, '/api/meta')).status, 400, host);
  }
  assert.equal(errors, '');
  assert.equal((await request('localhost', '/api/meta')).status, 200);
});

test('HTTP(S) templates support full and partial first-label wildcards up to the DNS limit', async t => {
  for (const previewUrl of ['http://*.localhost:8799', 'https://*.preview.example.com',
    'https://js-trial1-*.argusbot.cn', 'https://prefix*suffix.example.com',
    'https://*.' + [63, 63, 63, 28].map(n => 'a'.repeat(n)).join('.'), 'https://' + 'a'.repeat(31) + '*.example.com']) {
    const { request } = await setup(t, { previewUrl });
    const url = new URL(JSON.parse((await request('localhost')).body).url);
    assert.equal(url.hostname.split('.')[0]!.length <= 63, true);
    const [before, after] = new URL(previewUrl).hostname.split('*');
    const key = url.hostname.slice(before!.length, url.hostname.length - after!.length);
    assert.match(key, /^[a-f0-9]{32}$/);
    assert.equal(url.origin, new URL(previewUrl.replace('*', key)).origin);
    assert.deepEqual(await request(url.host, '/index.html'), { status: 200, body: '<p>preview</p>' });
  }
});

test('--preview-url rejects credentials, non-HTTP protocols, paths, queries and fragments', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-preview-invalid-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  for (const previewUrl of ['', 'https://user:pass@*.example.com', 'https://user@*.example.com', 'file:///tmp/',
    'https://*.example.com/page', 'https://*.example.com/./', 'https://*.example.com/?', 'https://*.example.com/#',
    'https://*.example.com?key=secret', 'https://*.example.com#fragment', 'invalid', 'https://preview.example.com',
    'https://*.*.example.com', 'https://preview.*.example.com', 'https://**.example.com', 'https://*.example.com.',
    'https://foo_*.example.com', 'https://-*.example.com', 'https://*.example.com:99999', 'https://' + 'a'.repeat(32) + '*.example.com',
    'https://*.' + [63, 63, 63, 29].map(n => 'a'.repeat(n)).join('.'),
    'https://*-.example.com', 'https://%2a.example.com', 'http://*.localhost:', 'http://*.localhost:bad', 'https://*.example.com\t']) {
    await assert.rejects(serve({ store: new Store(home), callAgent: () => assert.fail('no backend call'), backend: 'codex', model: null, port: 0, web: home, previewUrl }), /--preview-url must be an HTTP\(S\) origin template with one \* in its first DNS label/);
  }
});

test('the CLI accepts --preview-url and rejects that origin at the cockpit API, without a backend installed', { timeout: 15_000 }, async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-preview-cli-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const cli = spawn(process.execPath, ['packages/core/dist/argus.mjs', '--port', '0', '--preview-url', 'https://js-trial1-*.argusbot.cn', '--web', home],
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
  assert.match(new URL(preview).hostname, /^js-trial1-[a-z0-9]{32}\.argusbot\.cn$/);
  assert.equal(new URL(preview).pathname, '/index.html');
  assert.equal((await fetch(`${url}/api/projects/${sid}/daemon/stop`, { method: 'POST', headers: { ...headers, origin: new URL(preview).origin, 'content-type': 'text/plain' }, body: '{}' })).status, 403);
  assert.equal((await fetch(url + '/api/meta', { headers: { ...headers, origin: new URL(preview).origin } })).status, 403);
  assert.equal((await fetch(`${url}/api/projects/${sid}/daemon/stop`, { method: 'POST', headers: { ...headers, origin: 'https://cockpit.example.com' } })).status, 200);
});

// Replays r25 input #3/#11: a real occupied P+1, followed by explicit requests after release.
test('a failed LAN listener reports its port, clears state and binds on the next explicit request', async t => {
  const { request, server } = await setup(t, { token: 'preview-test' });
  const port = Number(new URL(server.url).port) + 1;
  const blocker = createServer();
  await new Promise<void>((resolve, reject) => blocker.once('error', reject).listen(port, '127.0.0.1', resolve));
  t.after(() => blocker.close());
  let errors = '';
  t.mock.method(process.stderr, 'write', (text: string | Uint8Array) => { errors += text; return true; });
  const failed = await Promise.all([request('192.168.1.2'), request('192.168.1.2')]);
  for (const reply of failed) {
    assert.equal(reply.status, 503);
    const body = JSON.parse(reply.body);
    assert.equal(body.code, 'preview_unavailable');
    assert.match(body.detail, new RegExp(`Preview server could not listen on 127.0.0.1:${port}`));
  }
  assert.equal(errors, '');
  assert.equal((await request('localhost')).status, 200);
  // The failed port is not classified as an active preview origin.
  assert.equal((await request('localhost', '/api/meta', 'GET', { origin: `http://localhost:${port}` })).status, 200);
  await new Promise<void>(resolve => blocker.close(() => resolve()));
  const url = new URL(JSON.parse((await request('192.168.1.2')).body).url);
  assert.equal(url.port, String(port));
  assert.equal(await (await fetch(`http://127.0.0.1:${port}${url.pathname}`)).text(), '<p>preview</p>');
  assert.equal((await request('localhost', '/api/meta', 'GET', { origin: url.origin })).status, 403);
});

// r25 review #1: a link from before a restart, a POST and a namespace host all get the same plain words.
test('a preview host with nothing to serve says in plain text that links end when Argus restarts', async t => {
  const { request } = await setup(t);
  const url = new URL(JSON.parse((await request('localhost')).body).url);
  const restarted = await setup(t);
  for (const reply of [await request(url.host, url.pathname, 'POST'), await restarted.request(`${url.hostname}:${new URL(restarted.server.url).port}`, url.pathname)]) {
    assert.equal(reply.status, 404);
    assert.match(reply.body, /^No preview here: preview links stop working when Argus restarts/);
  }
});

// r25 #4/#12: reserve the whole namespace, including empty and non-label identifiers.
test('all matching preview hosts are static-only and explain cockpit namespace conflicts', async t => {
  for (const options of [{}, { previewUrl: 'https://js-trial1-*.argusbot.cn' }, { previewUrl: 'https://*.example.com' }]) {
    const { request } = await setup(t, options);
    const hosts = options.previewUrl?.includes('js-trial1-')
      ? ['js-trial1-.argusbot.cn', 'js-trial1-a.b.argusbot.cn', 'js-trial1-a_b.argusbot.cn']
      : options.previewUrl ? ['argus.example.com'] : ['argus.localhost', 'x.abc.localhost', 'a_b.localhost', '.localhost'];
    for (const host of hosts) {
      for (const path of ['/', '/api/meta', '/api/projects']) {
        const reply = await request(host, path);
        assert.equal(reply.status, 404, host);
        assert.match(reply.body, /^No preview here.*only serves previews\.$/);
      }
      assert.equal((await request('localhost', '/api/meta', 'GET', { origin: `http://${host}` })).status, 403);
    }
    assert.equal((await request('localhost', '/api/meta')).status, 200);
  }
});

// r25 #8: use the actual wire protocol so Node does not invent a Host header.
test('HTTP/1.0 probes without Host use the local socket; mapped IPv6 Host remains legal', async t => {
  const { server, request } = await setup(t);
  const port = Number(new URL(server.url).port);
  const raw = await new Promise<string>((resolve, reject) => {
    const socket = connect(port, '127.0.0.1', () => socket.write('GET /api/meta HTTP/1.0\r\n\r\n'));
    let data = '';
    socket.on('data', chunk => { data += chunk; });
    socket.on('end', () => resolve(data)); socket.on('error', reject);
  });
  assert.match(raw, /^HTTP\/1\.1 200/);
  assert.equal((await request('[::ffff:192.168.1.2]', '/api/meta')).status, 200);
  const bad = await request('[::ffff:999.1.2.3]', '/api/meta');
  assert.equal(bad.status, 400);
  assert.match(bad.body, /invalid Host header/);
});

test('invalid --preview-url CLI arguments all return the same format explanation', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-preview-cli-invalid-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  for (const value of ['', 'https//*.example.com', '://bad', 'https://*.example.com:99999', 'https://*.example.com/path']) {
    const cli = spawnSync(process.execPath, ['packages/core/dist/argus.mjs', '--preview-url', value], {
      env: { ...process.env, ARGUS_HOME: home, PATH: '' }, encoding: 'utf8',
    });
    assert.equal(cli.status, 1);
    assert.match(cli.stderr, /--preview-url must be an HTTP\(S\) origin template with one \* in its first DNS label/);
    assert.equal(cli.stderr.includes('Invalid URL'), false);
  }
});
