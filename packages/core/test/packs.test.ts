import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, mkdtemp, readFile, readdir, rm, symlink, truncate, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { add, catalog, remove, type Contribution, type Pack } from '../src/packs.js';
import { instructions, packsHome, purpose, skills, SKILLS } from '../src/prompts.js';
import { confine } from '../src/agent/sandbox.js';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';

const root = fileURLToPath(new URL('../../..', import.meta.url));
const url = 'https://raw.githubusercontent.com/Argus-AiTeam/argus-verticals/main/packs/catalog.json';
const run = promisify(execFile);
const body = (version = '1') => `---\nname: materials\nversion: "${version}"\ndescription: Check material evidence\n---\n\nRun checks/check.mjs.\n`;
const pack = (version = '1'): Pack => ({ name: 'materials', version, description: 'Check material evidence',
  description_zh: '检查材料证据', requires: [], needs: ['python3'], files: [{ path: 'SKILL.md', size: Buffer.byteLength(body(version)) }, { path: 'checks/check.mjs', size: 2 }] });

test('installation, replacement, rejection and cleanup share a bounded download transaction', async t => {
  const home = await mkdtemp(join(root, '.integration-store-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  let entries = [pack()];
  let text = body();
  let fail = false;
  let oversizedCatalog = false;
  const seen: string[] = [];
  t.mock.method(globalThis, 'fetch', async (input: string | URL | Request, init?: RequestInit) => {
    const address = String(input);
    seen.push(address);
    assert.equal(init?.redirect, 'error');
    if (address === url) return oversizedCatalog ? new Response('x'.repeat(2 ** 20 + 1)) : Response.json({ packs: entries });
    if (address.endsWith('/SKILL.md')) return new Response(text);
    return new Response(fail ? 'bad' : '{}', { status: fail ? 503 : 200 });
  });
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  const prompt = () => instructions('engineer', project, store);
  assert.ok(!prompt().includes('- materials:'));
  await add(home, 'materials');
  assert.equal(purpose(join(packsHome(home), 'materials'), 'version'), '1');
  assert.ok(prompt().includes('- materials: Check material evidence'));
  assert.ok(seen.includes(url.replace('catalog.json', 'materials/checks/check.mjs')));
  assert.ok(skills(home).findIndex(([name]) => name === 'materials') > skills(home).findIndex(([name]) => name === 'learning'));
  assert.deepEqual(await readdir(home).then(names => names.filter(name => name.includes('.tmp'))), []);
  entries = [pack('2')]; text = body('2');
  await add(home, 'materials');
  assert.equal(purpose(join(packsHome(home), 'materials'), 'version'), '2');
  entries = [pack('3')]; text = body('3');
  const rename = fs.promises.rename;
  const move = t.mock.method(fs.promises, 'rename', async (from: string, to: string) => {
    if (basename(String(from)) === 'new') throw new Error('Replacement move failed');
    return rename(from, to);
  });
  syncBuiltinESMExports();
  try { await assert.rejects(add(home, 'materials'), /Replacement move failed/); }
  finally { move.mock.restore(); syncBuiltinESMExports(); }
  assert.equal(purpose(join(packsHome(home), 'materials'), 'version'), '2');
  assert.ok(!(await readdir(home)).some(name => name.startsWith('skill-packs.tmp')));
  fail = true;
  await assert.rejects(add(home, 'materials'), /503/);
  assert.equal(purpose(join(packsHome(home), 'materials'), 'version'), '2');
  fail = false;
  for (const path of ['/outside', '../outside', '.hidden', 'checks/.hidden', 'checks\\outside', 'C:/outside', 'checks//outside']) {
    entries = [{ ...pack(), files: [...pack().files, { path, size: 1 }] }];
    await assert.rejects(add(home, 'materials'), /No such pack/);
  }
  for (const entry of [
    { ...pack(), name: 'learning' }, { ...pack(), name: '../bad' },
    { ...pack(), files: [{ path: 'other.md', size: 1 }] },
    { ...pack(), files: [{ path: 'SKILL.md', size: 8 * 2 ** 20 + 1 }] },
    { ...pack(), files: [{ path: 'SKILL.md', size: -1 }] },
    { ...pack(), files: [...pack().files, pack().files[0]!] },
    { ...pack(), needs: null }, { ...pack(), version: 'bad\nversion' },
  ]) {
    // A pack this version cannot install is left out; the rest of the catalog still lists.
    entries = [entry as Pack, { ...pack(), name: 'other', tags: ['new field'] } as Pack];
    assert.deepEqual((await catalog()).map(({ name }) => name), ['other']);
  }
  entries = [{ ...pack(), requires: ['other'] }];
  await assert.rejects(add(home, 'materials'), /dependencies/);
  entries = [pack()];
  for (const invalid of ['', body('wrong'), body().replace('description:', 'other:')]) {
    text = invalid;
    await assert.rejects(add(home, 'materials'));
    assert.equal(purpose(join(packsHome(home), 'materials'), 'version'), '2');
    assert.ok(!(await readdir(home)).some(name => name.startsWith('skill-packs.tmp')));
  }
  text = body() + 'excess';
  await assert.rejects(add(home, 'materials'), /size limit/);
  oversizedCatalog = true;
  await assert.rejects(catalog(), /size limit/);
  await remove(home, 'learning');
  assert.ok(fs.existsSync(join(SKILLS, 'learning')));
  await remove(home, 'materials');
  await remove(home, 'materials');
  assert.ok(!prompt().includes('- materials:'));
  assert.deepEqual(await readdir(packsHome(home)), []);
});

test('CLI and authenticated HTTP install and remove the same skill files synchronously', async t => {
  const home = await mkdtemp(join(root, '.integration-store-api-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const nativeFetch = globalThis.fetch;
  const responses = { [url]: JSON.stringify({ packs: [pack()] }),
    [url.replace('catalog.json', 'materials/SKILL.md')]: body(),
    [url.replace('catalog.json', 'materials/checks/check.mjs')]: '{}' };
  const patch = `globalThis.fetch = async (url, init) => { const bodies = ${JSON.stringify(responses)}; if (!(String(url) in bodies) || init.redirect !== 'error') throw new Error('Unexpected download'); return new Response(bodies[String(url)]); };`;
  // Run this worktree's source even when the test is invoked from elsewhere.
  const localCli = (...args: string[]) => run(process.execPath, ['--import', `data:text/javascript,${encodeURIComponent(patch)}`, '--import', 'tsx', 'packages/core/src/cli.ts', ...args], { cwd: root, env: { ...process.env, ARGUS_HOME: home } });
  let offline = false;
  t.mock.method(globalThis, 'fetch', async (input: string | URL | Request, init?: RequestInit) => {
    if (offline) throw new TypeError('fetch failed');
    if (String(input) in responses) return new Response(responses[String(input) as keyof typeof responses]);
    return nativeFetch(input, init);
  });
  const store = new Store(home);
  const served = await serve({ store, callAgent: async () => { throw new Error('No model calls expected'); }, backend: 'codex', model: null, port: 0, token: 'store-token', web: home });
  t.after(() => served.close());
  const call = (path: string, authorized = true, value?: object) => nativeFetch(served.url + path, {
    method: value ? 'POST' : 'GET', headers: { ...(authorized ? { authorization: 'Bearer store-token' } : {}), 'Content-Type': 'application/json' },
    ...(value ? { body: JSON.stringify(value) } : {}),
  });
  for (const path of ['/api/skill-packs', '/api/skill-packs/add', '/api/skill-packs/remove']) {
    assert.equal((await call(path, false, path.endsWith('packs') ? undefined : { name: 'materials' })).status, 401);
  }
  assert.deepEqual(await readdir(packsHome(home)), []);
  await localCli('skills', 'add', 'materials');
  assert.match((await localCli('skills')).stdout, /materials\t1\t1\t检查材料证据\tpython3/);
  const items = async () => (await (await call('/api/skill-library')).json()).items as { name: string; source: string }[];
  assert.ok((await items()).some(item => item.name === 'materials' && item.source === 'store'));
  assert.equal((await (await call('/api/skill-library/document?library=store&path=materials/SKILL.md')).json()).content, body());
  assert.equal((await call('/api/skill-library/document?library=store&path=../web-token')).status, 404);
  assert.equal((await call('/api/skill-packs/remove', true, { name: 'materials' })).status, 200);
  assert.ok(!(await items()).some(item => item.name === 'materials'));
  assert.equal((await call('/api/skill-packs/add', true, { name: 'materials' })).status, 200);
  assert.equal(await readFile(join(packsHome(home), 'materials', 'SKILL.md'), 'utf8'), body());
  const listing = await (await call('/api/skill-packs')).json();
  assert.equal(listing.packs[0].installed_version, '1');
  await localCli('skills', 'remove', 'materials');
  assert.ok(!(await items()).some(item => item.name === 'materials'));
  await assert.rejects(localCli('skills', 'add', 'learning'), error => (error as { code: number }).code === 1);
  assert.equal((await call('/api/skill-packs/add', true, { name: '../bad' })).status, 400);
  offline = true;
  const down = await call('/api/skill-packs');
  assert.equal(down.status, 502);
  assert.match((await down.json()).detail, /skill store is unavailable: fetch failed/);
});

test('the pack root is read-only even for a writing role', { skip: process.platform !== 'linux' }, async t => {
  const home = await mkdtemp(join(root, '.integration-store-permission-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const packs = packsHome(home);
  const team = join(home, 'skills');
  const argv = confine(['codex'], { backend: 'codex', role: 'engineer', form: 'engineer', prompt: '', cwd: join(home, 'work'), access: 'write',
    readDirs: [SKILLS, packs], writeDirs: [team], callsDir: '', usageFile: '' });
  assert.equal(argv[argv.lastIndexOf(packs) - 2], '--ro-bind-try');
  assert.equal(argv[argv.lastIndexOf(team) - 2], '--bind-try');
});

test("a team skill offered to the store is checked by the store's rule, kept for the host, and shows what the host answered", async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-offer-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const served = await serve({ store: new Store(home), callAgent: async () => { throw new Error('No model calls expected'); }, backend: 'codex', model: null, port: 0, token: 'offer-token', web: home });
  t.after(() => served.close());
  const call = (path: string, value?: object) => fetch(served.url + path, { method: value ? 'POST' : 'GET',
    headers: { authorization: 'Bearer offer-token', 'Content-Type': 'application/json' }, ...(value ? { body: JSON.stringify(value) } : {}) });
  const offer = (name: unknown) => call('/api/skill-library/contribute', { name });
  const items = async () => (await (await call('/api/skill-library')).json()).items as { name: string; source: string; contribution?: Contribution | null }[];
  const shown = async (name: string) => (await items()).find(item => item.source === 'shared' && item.name === name)?.contribution;
  const write = async (path: string, text: string) => { await mkdir(dirname(path), { recursive: true }); await writeFile(path, text); };
  const skill = (name: string, text = body()) => write(join(home, 'skills', name, 'SKILL.md'), text);
  const kept = join(home, 'contributions', 'materials.json');

  await skill('materials');
  await write(join(home, 'skills', 'materials', 'checks', 'check.mjs'), '{}');
  // Compiled Python is left out, as the store's catalog leaves it out: it counts toward nothing.
  for (const compiled of ['__pycache__/check.cpython-312.pyc', 'check.pyc']) {
    await write(join(home, 'skills', 'materials', 'checks', compiled), '');
    await truncate(join(home, 'skills', 'materials', 'checks', compiled), 8 * 2 ** 20);
  }
  // Without a host that answers offers there is nothing to offer: the page shows none, and none is kept.
  assert.ok(!('contribution' in ((await items()).find(item => item.name === 'materials') ?? {})));
  const nobody = await offer('materials');
  assert.equal(nobody.status, 400);
  assert.equal((await nobody.json()).code, 'request_failed');
  await mkdir(join(home, 'contributions'));
  assert.equal(await shown('materials'), null);
  // Two offers at once, from two tabs say, are both kept, one after the other.
  assert.deepEqual((await Promise.all([offer('materials'), offer('materials')])).map(answer => answer.status), [200, 200]);
  const asked = JSON.parse(await readFile(kept, 'utf8')) as Contribution;
  assert.deepEqual({ ...asked, requested_at: 0 }, { name: 'materials', version: '1', requested_at: 0 });
  assert.ok(Math.abs(asked.requested_at - Date.now() / 1000) < 60);
  assert.deepEqual(await readdir(join(home, 'contributions')), ['materials.json']);
  // What the host answered is shown as it wrote it; offering again asks anew, without the old answer.
  const answered = { ...asked, url: 'https://github.com/Argus-AiTeam/argus-verticals/pull/17', submitted_at: asked.requested_at + 1 };
  await writeFile(kept, JSON.stringify(answered));
  assert.deepEqual(await shown('materials'), answered);
  assert.equal((await offer('materials')).status, 200);
  assert.deepEqual(Object.keys(JSON.parse(await readFile(kept, 'utf8'))), ['name', 'version', 'requested_at']);
  await skill('unversioned', body().replace('version: "1"\n', ''));
  assert.equal((await offer('unversioned')).status, 200);
  assert.equal((await shown('unversioned'))?.version, '0.1.0');
  // Only Argus's skills and installed packs carry no offer.
  await write(join(packsHome(home), 'installed', 'SKILL.md'), body());
  assert.ok((await items()).filter(item => item.source !== 'shared').every(item => !('contribution' in item)));

  // Refused with the reason, and nothing kept: what the store would not take, and what is not the team's.
  await skill('learning');
  await skill('linked');
  await symlink(join(home, 'skills', 'materials', 'checks', 'check.mjs'), join(home, 'skills', 'linked', 'check.mjs'));
  await skill('linked-folder');
  await symlink(join(home, 'skills', 'materials', 'checks'), join(home, 'skills', 'linked-folder', 'checks'));
  // A link to a folder above is refused where it is met, not walked: walking it would never end.
  await skill('looped');
  await symlink(join(home, 'skills'), join(home, 'skills', 'looped', 'up'));
  await skill('large');
  await writeFile(join(home, 'skills', 'large', 'data.bin'), '');
  await truncate(join(home, 'skills', 'large', 'data.bin'), 8 * 2 ** 20);
  await skill('hidden');
  await write(join(home, 'skills', 'hidden', '.env'), 'TOKEN=x');
  await skill('Named');
  await skill('undescribed', body().replace('description:', 'other:'));
  // Only the store's rule answers with its code, whose reason the page shows; not the team's skill, or no name, is another error.
  const other = new Set<unknown>(['installed', 'research', 1]);
  for (const [name, reason] of [['learning', /name of a skill Argus comes with/], ['linked', /check\.mjs is a link/], ['linked-folder', /checks is a link/], ['looped', /up is a link/],
    ['large', /at most 8 MiB; this one holds 8\.0 MiB/], ['hidden', /cannot hold "\.env"/], ['Named', /store name/], ['undescribed', /description/],
    ['installed', /No such team skill/], ['research', /No such team skill/], ['../skills/materials', /store name/], [1, /Name required/]] as const) {
    const refused = await offer(name);
    assert.equal(refused.status, 400, String(name));
    const { detail, code } = await refused.json();
    assert.match(detail, reason);
    assert.equal(code, other.has(name) ? 'request_failed' : 'not_for_store', String(name));
  }
  assert.deepEqual((await readdir(join(home, 'contributions'))).sort(), ['materials.json', 'unversioned.json']);
});
