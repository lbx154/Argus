import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import test, { type TestContext } from 'node:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { Store, type Task } from '../src/store.js';
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

test('an HTML result is previewed as one page, its local files inlined, that can reach nothing', async t => {
  const { project, call, get } = await setup(t, {
    'site/index.html': '<!doctype html><html><head><link rel="stylesheet" href="style.css?v=1"><script src="app.js"></script>'
      + '<style>h1 { background: url("img/dot.png") }</style></head><body><img src="img/dot.png"><a href="next.html">next</a>'
      + '<img src="../../secret.png"><img src="https://example.com/x.png"><div style="background:url(\'/img/dot.png\')"></div></body></html>',
    'site/style.css': 'body { background: url(img/dot.png) }',
    'site/app.js': 'document.title = "ok";',
    'site/img/dot.png': 'PNG',
  });
  const P = `/api/projects/${project.id}`;
  const shown = await get<{ html: string; warnings: string[]; file_count: number; served_page: boolean }>(`${P}/artifact/preview?path=site/index.html`);
  const data = (type: string, text: string) => `data:${type};base64,${Buffer.from(text).toString('base64')}`;
  const dot = data('image/png', 'PNG');
  assert.equal(shown.html, '<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
    + 'script-src \'unsafe-inline\' data: blob:; style-src \'unsafe-inline\' data:; img-src data: blob:; font-src data:; media-src data: blob:; '
    + 'base-uri \'none\'; form-action \'none\'">'
    + `<html><head><link rel="stylesheet" href="${data('text/css', `body { background: url(${dot}) }`)}">`
    + `<script src="${data('text/javascript', 'document.title = "ok";')}"></script><style>h1 { background: url(${dot}) }</style></head>`
    + `<body><img src="${dot}"><a href="next.html">next</a><img src="data:,"><img src="https://example.com/x.png">`
    + `<div style="background:url(${dot})"></div></body></html>`);
  assert.deepEqual(shown.warnings, ['Could not include local resource: ../../secret.png']);
  assert.equal(shown.file_count, 4);
  assert.equal(shown.served_page, false);
  assert.equal((await call(`${P}/artifact/preview?path=site/style.css`)).status, 404);

  // Results are downloaded one by one: a zip is refused, and says why.
  for (const response of [await call(`${P}/artifact/bundle?path=site/index.html`),
    await call(`${P}/artifacts/bundle`, { method: 'POST', body: JSON.stringify({ paths: ['site/app.js'] }) })]) {
    assert.equal(response.status, 501);
    assert.match((await response.json() as { detail: string }).detail, /download each file by itself/);
  }
});

test('a task in full, a role\'s letters to download, and a search of every project\'s record', async t => {
  const { store, project, call, get } = await setup(t);
  const { id } = project;
  const end = (role: 'manager' | 'engineer' | 'reviewer', task: string | undefined, error: string | null) => store.append(id, {
    type: 'call.end', role, ...(task ? { task } : {}), callId: `c-${role}`, threadId: null, completed: !error, error, model: null, usage: null, ms: 5,
  });
  const chart: Task = { id: 't1', title: 'Sales chart', from: 'manager', objective: 'Plot sales per region into chart.png.', status: 'open', holder: 'engineer', note: null };
  const years: Task = { id: 't2', title: 'Years', from: 'manager', objective: 'Split the chart by year.', status: 'open', holder: 'engineer', note: null };
  store.append(id, { type: 'message', from: 'operator', text: 'Chart the sales by region.' });
  end('manager', undefined, null);
  store.append(id, { type: 'handoff', role: 'manager', to: 'engineer', letter: chart.objective });
  store.append(id, { type: 'task.added', task: chart });
  end('engineer', 't1', 'The turn ended without the hand-off form.');
  end('engineer', 't1', null);
  store.append(id, { type: 'handoff', role: 'engineer', task: 't1', to: 'reviewer', letter: 'chart.png drawn from sales.csv' });
  end('reviewer', 't1', null);
  store.append(id, { type: 'handoff', role: 'reviewer', task: 't1', to: 'manager', letter: 'The chart matches the data.' });
  store.append(id, { type: 'task.closed', task: 't1' });
  store.append(id, { type: 'task.added', task: years });
  end('engineer', 't2', 'codex exited with code 1');
  const question = { role: 'engineer' as const, task: 't2', question: 'Which years?' };
  store.save({ ...store.load(id), status: 'asking', question, tasks: [{ ...chart, status: 'closed', note: { from: 'reviewer', text: 'The chart matches the data.' } }, years] });
  const P = `/api/projects/${id}`;

  const { item: done } = await get<{ item: Record<string, unknown> }>(`${P}/backlog/t1`);
  assert.equal(done.title, 'Sales chart');
  assert.equal(done.status, 'done');
  assert.equal(done.objective, chart.objective);
  assert.ok(typeof done.started_ts === 'number' && typeof done.finished_ts === 'number' && done.finished_ts >= done.started_ts);
  assert.equal(done.notes, 'Engineer to the Reviewer:\nchart.png drawn from sales.csv\n\nReviewer to the Manager:\nThe chart matches the data.');
  assert.equal(done.last_error, '');
  const { item: asking } = await get<{ item: Record<string, unknown> }>(`${P}/backlog/t2`);
  assert.deepEqual([asking.status, asking.pending_question, asking.last_error, asking.finished_ts], ['blocked', 'Which years?', 'codex exited with code 1', null]);
  assert.equal((await call(`${P}/backlog/t9`)).status, 404);

  const log = await call(`${P}/work-log/download?role=engineer&task_id=t1`);
  assert.match(log.headers.get('content-disposition') ?? '', /^attachment/);
  const text = await log.text();
  assert.match(text, /^# The Engineer's letters on t1\n\n## \S+, to the Reviewer on t1\n\nchart\.png drawn from sales\.csv\n$/);
  assert.equal((await call(`${P}/work-log/download?role=nobody`)).status, 400);

  // Every word asked, in the request, the conversation (the Manager's letters are its replies) and the team's letters.
  const { results } = await get<{ results: Record<string, unknown>[] }>('/api/search?q=CHART%20sales');
  assert.deepEqual(results.map(({ sid, kind, role, snippet }) => [sid, kind, role, snippet]), [
    [id, 'project', undefined, 'Chart the sales by region.'],
    [id, 'conversation', 'operator', 'Chart the sales by region.'],
    [id, 'conversation', 'argus', chart.objective],
    [id, 'project', 'engineer', 'chart.png drawn from sales.csv'],
  ]);
  assert.equal(results[1]!.message_id, 'm0-operator');
  assert.equal((await call('/api/search?q=%20')).status, 422);
});
