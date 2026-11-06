import assert from 'node:assert/strict';
import { once } from 'node:events';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, parse, resolve } from 'node:path';
import test, { type TestContext } from 'node:test';
import { Store } from '../src/store.js';
import { keep } from '../src/serve/files.js';
import { serve } from '../src/serve/index.js';
import { previewFetch } from './helpers/preview-fetch.js';
import { directoryLink, fileLink } from './helpers/fsLinks.js';

async function setup(t: TestContext) {
  const home = await mkdtemp(join(tmpdir(), 'argus-filesystem-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const server = await serve({ store, callAgent: () => assert.fail('no model call'), backend: 'codex', model: null, port: 0, token: 'filesystem-test', web: home });
  t.after(() => server.close());
  const call = (path: string, init: RequestInit = {}) => fetch(server.url + path, { ...init, headers: { authorization: 'Bearer filesystem-test', ...init.headers } });
  const post = (path: string, body: unknown) => call(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  const json = async (response: Response) => {
    assert.equal(response.status, 200, await response.clone().text());
    return response.json();
  };
  return { home, store, call, post, json };
}

test('uploads and zip downloads use portable names, including Windows reserved device names', async t => {
  const { store, post, call, json } = await setup(t);
  const { sid } = await json(await post('/api/daemons', {}));
  const project = store.load(sid);
  const P = `/api/projects/${sid}`;
  await writeFile(join(project.workdir, 'result.txt'), 'result');
  for (const name of ['CON', 'con.txt', 'NUL.csv', 'AUX', 'PRN.log', 'COM1.bin', 'lpt9', 'COM¹.txt', 'LPT².txt', 'COM10.txt', '中文 空格.txt', 'bad<>:"|?*.txt', '.notes.md. ']) {
    const stored = /^(?:con|nul|aux|prn|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)/i.test(name) ? `_${name}`
      : name === 'bad<>:"|?*.txt' ? 'bad_______.txt' : name === '.notes.md. ' ? 'notes.md' : name;
    await t.test(name, async () => {
      const begun = await json(await post(`${P}/attachments/uploads`, { name, mime: 'text/plain', size_bytes: 3 }));
      const U = `${P}/attachments/uploads/${begun.attachment_id}`;
      await json(await call(`${U}?offset=0`, { method: 'PUT', body: 'abc' }));
      const { attachment } = await json(await post(`${U}/complete`, {}));
      assert.equal(attachment.stored_name, stored);
      assert.equal(await readFile(join(project.workdir, attachment.relative_path), 'utf8'), 'abc');
      const raw = await call(`${P}/artifact/raw?${new URLSearchParams({ path: attachment.relative_path })}`);
      assert.equal(raw.status, 200);
      assert.equal(await raw.text(), 'abc');
    });
  }
  // Multipart uploads go through the same declaration as chunked ones.
  const form = new FormData();
  form.append('files', new Blob(['small']), 'NUL.txt');
  const { attachments } = await json(await call(`${P}/attachments`, { method: 'POST', body: form }));
  assert.equal(attachments[0].stored_name, '_NUL.txt');
  const bundle = await call(`${P}/artifacts/bundle`, { method: 'POST', body: JSON.stringify({ paths: ['result.txt'], name: 'CON' }) });
  assert.equal(bundle.status, 200);
  assert.equal(bundle.headers.get('content-disposition'), "attachment; filename*=UTF-8''_CON.zip");
  assert.equal(Buffer.from(await bundle.arrayBuffer()).readUInt32LE(0), 0x04034b50);
});

test('real paths with spaces, Chinese, long components and dot segments work across workspace, results, previews and trash', async t => {
  const { home, store, post, call, json } = await setup(t);
  const workdir = join(home, '中文 空格', ...Array(5).fill('long-component-'.repeat(4)), 'work');
  const { sid } = await json(await post('/api/daemons', { workdir }));
  const project = store.load(sid);
  const P = `/api/projects/${sid}`;
  const site = '报告 目录';
  await mkdir(join(workdir, site));
  await writeFile(join(workdir, site, '结果.txt'), '中文 result');
  await writeFile(join(workdir, site, 'index.html'), '<link rel="stylesheet" href="样式.css"><p>报告</p>');
  await writeFile(join(workdir, site, '样式.css'), 'p { color: red }');
  await mkdir(join(workdir, '.private'));
  await writeFile(join(workdir, '.private', 'secret.txt'), 'private');
  await directoryLink(join(workdir, '.private'), join(workdir, 'hidden-target'));
  await directoryLink(dirname(workdir), join(workdir, 'outside-target'));
  await writeFile(join(dirname(workdir), 'outside.txt'), 'outside');
  const W = (path: string, file?: string) => `/api/v2/workspace/${path}?${new URLSearchParams({ sid, workspace_id: `project:${sid}`, ...(file === undefined ? {} : { path: file }) })}`;
  const tree = await json(await call(W('tree')));
  assert.ok(tree.entries.some((entry: { path: string }) => entry.path === `${site}/结果.txt`));
  assert.ok(!tree.entries.some((entry: { path: string }) => entry.path.startsWith('outside-target/') || entry.path.startsWith('hidden-target/')));
  const normal = `${site}/结果.txt`;
  const variants = [normal, `${site}/../${normal}`, join(workdir, normal)];
  if (process.platform === 'win32') variants.push(`${site}\\结果.txt`, join(workdir, normal).replace(/^([A-Z]):/, (_, drive: string) => drive.toLowerCase() + ':'), join(workdir, normal).replace(/\\/g, '/'), `\\\\?\\${join(workdir, normal)}`);
  for (const path of variants) {
    assert.deepEqual(await json(await call(W('file', path))), { content: '中文 result' });
    const raw = await call(`${P}/artifact/raw?${new URLSearchParams({ path })}`);
    assert.equal(raw.status, 200, path);
    assert.equal(await raw.text(), '中文 result');
  }
  for (const path of ['../outside.txt', 'outside-target/outside.txt', 'hidden-target/secret.txt', '.private/secret.txt', join(home, 'owner.json')]) {
    assert.equal((await call(W('file', path))).status, 404, path);
    assert.equal((await call(`${P}/artifact/raw?${new URLSearchParams({ path })}`)).status, 404, path);
  }
  await t.test('file links into a hidden directory stay unavailable', async t => {
    if (!await fileLink(t, join(workdir, '.private', 'secret.txt'), join(workdir, 'hidden-file'))) return;
    assert.equal((await call(W('file', 'hidden-file'))).status, 404);
  });
  const snapshots = keep(project, store.dir(sid), [normal, 'outside-target/outside.txt', 'hidden-target/secret.txt']);
  assert.equal(snapshots.length, 1);
  assert.equal(snapshots[0]!.path, normal);
  store.save(project);
  assert.equal((await json(await call(`${P}/artifacts`))).artifacts.filter((item: { path: string }) => item.path === normal).length, 1);
  const { url } = await json(await call(`${P}/artifact/preview?${new URLSearchParams({ path: `${site}/index.html` })}`));
  const page = await previewFetch(url);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /报告/);
  const css = await previewFetch(new URL('样式.css', url));
  assert.equal(css.status, 200);
  assert.equal(await css.text(), 'p { color: red }');
  const zip = await call(`${P}/artifact/bundle?${new URLSearchParams({ path: `${site}/index.html` })}`);
  assert.equal(zip.status, 200);
  assert.equal(Buffer.from(await zip.arrayBuffer()).readUInt32LE(0), 0x04034b50);
  const deleted = await json(await call(P, { method: 'DELETE' }));
  assert.equal(deleted.workdir_preserved, true);
  const { entries } = await json(await call('/api/trash'));
  assert.equal(entries.length, 1);
  await json(await post(`/api/trash/${entries[0].trash_id}/restore`, {}));
  assert.equal(store.load(sid).workdir, resolve(workdir));
  assert.deepEqual(await json(await call(W('file', normal))), { content: '中文 result' });
});

test('Windows different-drive paths stay outside the workspace', { skip: process.platform !== 'win32' }, async t => {
  const { home, call, post, json } = await setup(t);
  const otherRoot = process.cwd();
  if (parse(home).root.toLowerCase() === parse(otherRoot).root.toLowerCase()) {
    t.skip('the test workspace and native temp directory are on the same drive');
    return;
  }
  const other = await mkdtemp(join(otherRoot, '.integration-filesystem-'));
  t.after(() => rm(other, { recursive: true, force: true }));
  await writeFile(join(other, 'outside.txt'), 'other drive');
  const { sid } = await json(await post('/api/daemons', { workdir: join(home, 'work') }));
  const query = new URLSearchParams({ sid, workspace_id: `project:${sid}`, path: join(other, 'outside.txt') });
  assert.equal((await call(`/api/v2/workspace/file?${query}`)).status, 404);
});

test('Windows occupied project.json surfaces a failed atomic rename without replacing the old record', { skip: process.platform !== 'win32', timeout: 15_000 }, async t => {
  const { store, call } = await setup(t);
  const work = store.create({ workdir: '', backend: 'codex', model: null, request: 'original' });
  const path = join(store.dir(work.id), 'project.json');
  // FileShare.Read allows observing the intact record but denies Windows rename/delete.
  const script = `$f=[System.IO.File]::Open('${path.replaceAll("'", "''")}', 'Open', 'Read', 'Read'); Write-Output 'locked'; [Console]::In.ReadLine() | Out-Null; $f.Dispose()`;
  const locker = spawn('powershell.exe', ['-NoProfile', '-Command', script], { stdio: ['pipe', 'pipe', 'pipe'] });
  const closed = once(locker, 'close');
  t.after(() => { if (locker.exitCode === null) locker.kill(); });
  const [data] = await once(locker.stdout, 'data');
  assert.match(String(data), /locked/);
  try {
    const start = Date.now();
    assert.throws(() => store.save({ ...work, request: 'replacement' }), (error: NodeJS.ErrnoException) => ['EPERM', 'EACCES', 'EBUSY'].includes(error.code ?? ''));
    assert.ok(Date.now() - start < 1_000, 'a failed rename is surfaced, not retried');
    assert.equal(store.load(work.id).request, 'original');
    const response = await call(`/api/projects/${work.id}`, { method: 'PATCH', body: JSON.stringify({ name: 'changed' }) });
    assert.equal(response.status, 500);
    const error = await response.json();
    assert.equal(error.code, 'internal_error');
    assert.match(error.detail, /EPERM|EACCES|EBUSY/);
    assert.equal(store.load(work.id).name, undefined);
  } finally {
    locker.stdin.end('\n');
    await closed;
  }
  store.save({ ...work, request: 'replacement' });
  assert.equal(store.load(work.id).request, 'replacement');
});
