#!/usr/bin/env node
// Exercise a built/installed Argus's filesystem APIs using a new isolated state root; no model calls.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { request } from 'node:http';
import { mkdir, mkdtemp, readFile, symlink, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { crc32, inflateRawSync } from 'node:zlib';

const [entry, parent] = process.argv.slice(2);
if (!entry || !parent) throw new Error('Usage: node filesystem-smoke.mjs <built-or-installed argus.mjs> <scratch-parent>');
await mkdir(resolve(parent), { recursive: true });
const root = await mkdtemp(join(resolve(parent), 'argus-filesystem-'));
const home = join(root, 'state');
const env = { ...process.env, ARGUS_HOME: home, ARGUS_SHARED: home, ARGUS_WEB_TOKEN: 'isolated-filesystem-smoke' };
const child = spawn(process.execPath, [resolve(entry), '--port', '0'], { env, stdio: ['ignore', 'pipe', 'pipe'] });
const exited = once(child, 'close');
let stdout = '', stderr = '';
child.stderr.on('data', data => { stderr += data; });
child.stdout.on('data', data => { stdout += data; });
const ready = new Promise((accept, reject) => {
  const parse = () => { const match = /Argus is at (http\S+?)\/\?token=/.exec(stdout); if (match) accept(match[1]); };
  child.stdout.on('data', parse);
  child.once('error', reject);
  child.once('exit', code => reject(new Error(`CLI exited before ready (${code}): ${stderr}`)));
});
function unzip(zip) {
  const entries = {};
  const end = zip.lastIndexOf(Buffer.from([0x50, 0x4b, 5, 6]));
  for (let at = zip.readUInt32LE(end + 16), left = zip.readUInt16LE(end + 10); left--; at += 46 + zip.readUInt16LE(at + 28) + zip.readUInt16LE(at + 30) + zip.readUInt16LE(at + 32)) {
    const local = zip.readUInt32LE(at + 42);
    const start = local + 30 + zip.readUInt16LE(local + 26) + zip.readUInt16LE(local + 28);
    const data = inflateRawSync(zip.subarray(start, start + zip.readUInt32LE(at + 20)));
    assert.equal(crc32(data), zip.readUInt32LE(at + 16));
    entries[zip.toString('utf8', at + 46, at + 46 + zip.readUInt16LE(at + 28))] = data.toString();
  }
  return entries;
}
async function preview(input) {
  const url = new URL(input);
  return new Promise((accept, reject) => {
    const req = request({ hostname: '127.0.0.1', port: url.port, path: url.pathname, headers: { host: url.host } }, response => {
      const chunks = [];
      response.on('data', data => chunks.push(data));
      response.on('end', () => accept({ status: response.statusCode, body: Buffer.concat(chunks).toString() }));
      response.on('error', reject);
    });
    req.on('error', reject); req.end();
  });
}
try {
  const origin = await ready;
  const call = (path, init = {}) => fetch(origin + path, { ...init, headers: { authorization: `Bearer ${env.ARGUS_WEB_TOKEN}`, ...init.headers } });
  const post = (path, body) => call(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  const json = async response => { assert.equal(response.status, 200, await response.clone().text()); return response.json(); };
  const shell = await call('/');
  assert.equal(shell.status, 200); assert.match(await shell.text(), /<!doctype html>/i);
  const workdir = join(root, '中文 空格', ...Array(5).fill('long-component-'.repeat(4)), 'work');
  const { sid } = await json(await post('/api/daemons', { workdir }));
  const P = `/api/projects/${sid}`;
  await mkdir(join(workdir, '报告'));
  const page = '<link rel="stylesheet" href="样式.css"><p>报告</p>';
  await writeFile(join(workdir, '报告', 'index.html'), page);
  await writeFile(join(workdir, '报告', '样式.css'), 'p{color:red}');
  await writeFile(join(workdir, '结果.txt'), '中文 result');
  await writeFile(join(root, 'outside.txt'), 'outside');
  await mkdir(join(workdir, '.private'));
  await writeFile(join(workdir, '.private', 'secret.txt'), 'private');
  await symlink(root, join(workdir, 'outside-dir'), process.platform === 'win32' ? 'junction' : 'dir');
  await symlink(join(workdir, '.private'), join(workdir, 'hidden-dir'), process.platform === 'win32' ? 'junction' : 'dir');
  const W = (operation, path) => `/api/v2/workspace/${operation}?${new URLSearchParams({ sid, workspace_id: `project:${sid}`, ...(path === undefined ? {} : { path }) })}`;
  const tree = await json(await call(W('tree')));
  assert.ok(tree.entries.some(entry => entry.path === '结果.txt'));
  assert.ok(!tree.entries.some(entry => entry.path.startsWith('outside-dir/') || entry.path.startsWith('hidden-dir/')));
  for (const path of ['结果.txt', '报告/../结果.txt', join(workdir, '结果.txt')]) assert.equal((await json(await call(W('file', path)))).content, '中文 result');
  if (process.platform === 'win32') {
    assert.equal((await json(await call(W('file', join(workdir, '结果.txt').replace(/^([A-Z]):/, (_, drive) => drive.toLowerCase() + ':'))))).content, '中文 result');
    assert.equal((await json(await call(W('file', '报告\\index.html')))).content, page);
  }
  for (const path of ['../outside.txt', 'outside-dir/outside.txt', 'hidden-dir/secret.txt', '.private/secret.txt']) assert.equal((await call(W('file', path))).status, 404, path);
  const listed = (await json(await call(`${P}/artifacts`))).artifacts;
  assert.ok(listed.some(item => item.path === '结果.txt'));
  assert.ok(!listed.some(item => item.path.includes('secret.txt') || item.path.includes('outside.txt')));
  const raw = await call(`${P}/artifact/raw?${new URLSearchParams({ path: '结果.txt' })}`);
  assert.equal(raw.status, 200); assert.equal(await raw.text(), '中文 result');
  const { url } = await json(await call(`${P}/artifact/preview?${new URLSearchParams({ path: '报告/index.html' })}`));
  assert.deepEqual(await preview(url), { status: 200, body: page });
  assert.deepEqual(await preview(new URL('样式.css', url)), { status: 200, body: 'p{color:red}' });
  assert.equal((await preview(new URL('/outside-dir/outside.txt', url))).status, 404);
  const siteZip = await call(`${P}/artifact/bundle?${new URLSearchParams({ path: '报告/index.html' })}`);
  assert.equal(siteZip.status, 200);
  assert.deepEqual(unzip(Buffer.from(await siteZip.arrayBuffer())), { 'index.html': page, '样式.css': 'p{color:red}' });
  const zip = await post(`${P}/artifacts/bundle`, { paths: ['结果.txt'], name: 'CON' });
  assert.equal(zip.status, 200);
  assert.equal(zip.headers.get('content-disposition'), `attachment; filename*=UTF-8''${process.platform === 'win32' ? '_CON' : 'CON'}.zip`);
  assert.deepEqual(unzip(Buffer.from(await zip.arrayBuffer())), { '结果.txt': '中文 result' });
  const form = new FormData(); form.append('files', new Blob(['small']), 'NUL.txt');
  const { attachments } = await json(await call(`${P}/attachments`, { method: 'POST', body: form }));
  assert.equal(attachments[0].stored_name, process.platform === 'win32' ? '_NUL.txt' : 'NUL.txt');
  const begun = await json(await post(`${P}/attachments/uploads`, { name: '中文 附件.txt', size_bytes: 3 }));
  const U = `${P}/attachments/uploads/${begun.attachment_id}`;
  await json(await call(`${U}?offset=0`, { method: 'PUT', body: 'abc' }));
  assert.equal((await call(`${U}?offset=0`, { method: 'PUT', body: 'abc' })).status, 409);
  const complete = await json(await post(`${U}/complete`, {}));
  assert.equal(await readFile(join(workdir, complete.attachment.relative_path), 'utf8'), 'abc');
  const attachment = await call(`${P}/artifact/raw?${new URLSearchParams({ path: complete.attachment.relative_path })}`);
  assert.equal(attachment.status, 200); assert.equal(await attachment.text(), 'abc');
  await json(await call(P, { method: 'DELETE' }));
  const { entries } = await json(await call('/api/trash'));
  assert.equal(entries.length, 1);
  await json(await post(`/api/trash/${entries[0].trash_id}/restore`, {}));
  assert.equal((await json(await call(W('file', '结果.txt')))).content, '中文 result');
  console.log(JSON.stringify({ ok: true, root, sid, workdir_length: workdir.length, node: process.version, platform: process.platform, covered: ['CLI cockpit', 'HTTP create', 'workspace tree/text', 'native/Chinese/space/long paths', 'directory links', 'hidden/outside boundary', 'results/raw', 'HTML+CSS preview', 'CRC-checked website/results zip', 'multipart/chunk attachments', 'reserved device names', 'trash/restore'] }, null, 2));
} finally {
  child.kill();
  await exited;
  await writeFile(join(root, 'cli.stdout.txt'), stdout);
  await writeFile(join(root, 'cli.stderr.txt'), stderr);
}
