import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import test, { type TestContext } from 'node:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';

/** A server over one project made by hand, with these files in its workdir; no call is made. */
async function setup(t: TestContext, files: Record<string, string> = {}) {
  const home = await mkdtemp(join(tmpdir(), 'argus-workbench-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: join(home, 'work'), request: 'Chart the sales by region.', backend: 'codex', model: null });
  for (const [name, body] of Object.entries(files)) {
    await mkdir(dirname(join(project.workdir, name)), { recursive: true });
    await writeFile(join(project.workdir, name), body);
  }
  const served = await serve({ store, callAgent: () => assert.fail('no call is made'), backend: 'codex', model: null, port: 0, web: home });
  t.after(() => served.close());
  const call = (path: string, init?: RequestInit) => fetch(served.url + path, init);
  const get = async <T = Record<string, unknown>>(path: string): Promise<T> => {
    const response = await call(path);
    assert.equal(response.status, 200, `${path}: ${await response.clone().text()}`);
    return await response.json() as T;
  };
  return { store, project, call, get };
}

test('the code workspace is the workdir: its tree, a file as text or bytes, and what git says', async t => {
  const { store, project, call, get } = await setup(t, {
    'src/main.py': 'print(1)\n', 'Makefile': 'all:\n', 'plot.png': 'png', 'blob.bin': 'a\0b', '.env': 'KEY=1', 'node_modules/x/index.js': '',
  });
  const sid = project.id;
  const W = (path: string, more: Record<string, string> = {}) =>
    `/api/v2/workspace/${path}?${new URLSearchParams({ sid, workspace_id: `project:${sid}`, ...more })}`;
  const profiles = await get<{ profiles: { id: string; path: string }[]; default_id: string }>(`/api/v2/workspaces?sid=${sid}`);
  assert.deepEqual(profiles.profiles.map(({ id, path }) => [id, path]), [[`project:${sid}`, project.workdir]]);
  assert.equal(profiles.default_id, `project:${sid}`);

  // The tree, which the composer's `@` completion lists too: no hidden file, nothing tools fill.
  const tree = await get<{ entries: { path: string; type: string }[]; truncated: boolean }>(W('tree'));
  assert.deepEqual(tree.entries.map(entry => `${entry.type} ${entry.path}`).sort(),
    ['directory src', 'file Makefile', 'file blob.bin', 'file plot.png', 'file src/main.py']);
  assert.equal(tree.truncated, false);

  assert.equal((await get<{ content: string }>(W('file', { path: 'src/main.py' }))).content, 'print(1)\n');
  assert.equal((await get<{ content: string }>(W('file', { path: 'Makefile' }))).content, 'all:\n');
  assert.equal((await call(W('file', { path: 'blob.bin' }))).status, 415);
  for (const path of ['.env', '../work/.env', join(store.dir(sid), 'project.json'), 'missing.txt']) {
    assert.equal((await call(W('file', { path }))).status, 404, path);
  }
  assert.equal((await call(`/api/v2/workspace/tree?sid=${sid}&workspace_id=project:p-0`)).status, 404);

  const raw = await call(W('raw', { path: 'plot.png' }));
  assert.equal(raw.headers.get('content-type'), 'image/png');
  assert.equal(await raw.text(), 'png');
  assert.match((await call(W('download', { path: 'src/main.py' }))).headers.get('content-disposition') ?? '', /^attachment; filename\*=UTF-8''main\.py$/);

  // Git: nothing until the workdir is a repository, then its branch, changes, diff and history.
  assert.equal((await get<{ available: boolean }>(W('git'))).available, false);
  const git = (...args: string[]) => execFileSync('git', ['-c', 'user.name=Ada', '-c', 'user.email=ada@example.com', ...args], { cwd: project.workdir });
  git('init', '-q', '-b', 'main');
  git('add', 'src/main.py');
  git('commit', '-q', '-m', 'First script');
  await writeFile(join(project.workdir, 'src/main.py'), 'print(2)\n');
  const status = await get<{ available: boolean; branch: string; status: string; diff: string; log: string }>(W('git'));
  assert.equal(status.available, true);
  assert.equal(status.branch, 'main');
  assert.match(status.status, /^ M src\/main\.py$/m);
  assert.match(status.diff, /^\+print\(2\)$/m);
  assert.match(status.log, /\tAda\tFirst script$/);
  // A diff past the limit is shown up to it, and says so.
  await writeFile(join(project.workdir, 'big.txt'), 'x'.repeat(1.5 * (1 << 20)));
  git('add', 'big.txt');
  assert.equal((await get<{ truncated: boolean }>(W('git'))).truncated, true);
});
