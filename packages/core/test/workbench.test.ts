import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import test, { type TestContext } from 'node:test';
import { chmod, mkdir, mkdtemp, rm, truncate, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { crc32, inflateRawSync } from 'node:zlib';
import { Store, type Task } from '../src/store.js';
import { keep } from '../src/serve/files.js';
import { serve } from '../src/serve/index.js';
import { unified } from '../src/serve/versions.js';

/** A server over one project made by hand, with these files in its workdir; no call is made. */
async function setup(t: TestContext, files: Record<string, string> = {}, token?: string) {
  const home = await mkdtemp(join(tmpdir(), 'argus-workbench-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: join(home, 'work'), request: 'Chart the sales by region.', backend: 'codex', model: null });
  for (const [name, body] of Object.entries(files)) {
    await mkdir(dirname(join(project.workdir, name)), { recursive: true });
    await writeFile(join(project.workdir, name), body);
  }
  const served = await serve({ store, callAgent: () => assert.fail('no call is made'), backend: 'codex', model: null, port: 0, web: home, ...token ? { token } : {} });
  t.after(() => served.close());
  const call = (path: string, init: RequestInit = {}) =>
    fetch(served.url + path, { ...init, headers: { ...token ? { authorization: `Bearer ${token}` } : {}, ...init.headers } });
  const get = async <T = Record<string, unknown>>(path: string): Promise<T> => {
    const response = await call(path);
    assert.equal(response.status, 200, `${path}: ${await response.clone().text()}`);
    return await response.json() as T;
  };
  return { store, project, call, get };
}

/** A zip's files by name, read from its directory as an unzip does, each checked against its CRC. */
function unzip(zip: Buffer): Record<string, string> {
  const files: Record<string, string> = {};
  const end = zip.lastIndexOf(Buffer.from([0x50, 0x4b, 5, 6]));
  for (let at = zip.readUInt32LE(end + 16), left = zip.readUInt16LE(end + 10); left--; at += 46 + zip.readUInt16LE(at + 28) + zip.readUInt16LE(at + 30) + zip.readUInt16LE(at + 32)) {
    const local = zip.readUInt32LE(at + 42);
    assert.equal(zip.readUInt32LE(local), 0x04034b50);
    const start = local + 30 + zip.readUInt16LE(local + 26) + zip.readUInt16LE(local + 28);
    const data = inflateRawSync(zip.subarray(start, start + zip.readUInt32LE(at + 20)));
    assert.equal(crc32(data), zip.readUInt32LE(at + 16));
    files[zip.toString('utf8', at + 46, at + 46 + zip.readUInt16LE(at + 28))] = data.toString();
  }
  return files;
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
  git('init', '-q');
  git('symbolic-ref', 'HEAD', 'refs/heads/main');
  git('add', 'src/main.py');
  git('commit', '-q', '-m', 'First script');
  await writeFile(join(project.workdir, 'src/main.py'), 'print(2)\n');
  const status = await get<{ available: boolean; branch: string; status: string; diff: string; log: string }>(W('git'));
  assert.equal(status.available, true);
  assert.equal(status.branch, 'main');
  assert.match(status.status, /^ M src\/main\.py$/m);
  assert.match(status.diff, /^\+print\(2\)$/m);
  assert.match(status.log, /\tAda\tFirst script$/);
  // A program the repository's config or attributes name, which a role may write, never runs.
  const ran = join(project.workdir, '..', 'ran');
  await writeFile(join(project.workdir, '..', 'program.sh'), `#!/bin/sh\ntouch ${ran}\ncat\n`);
  await chmod(join(project.workdir, '..', 'program.sh'), 0o755);
  git('config', 'core.fsmonitor', join(project.workdir, '..', 'program.sh'));
  git('config', 'filter.x.clean', join(project.workdir, '..', 'program.sh'));
  git('config', 'filter.x.process', join(project.workdir, '..', 'program.sh'));
  await writeFile(join(project.workdir, '.gitattributes'), '* filter=x\n');
  await mkdir(join(project.workdir, '.git', 'info'), { recursive: true });
  await writeFile(join(project.workdir, '.git', 'info', 'attributes'), '* filter=x\n');
  const shown = await get<{ status: string; diff: string }>(W('git'));
  assert.match(shown.status, /src\/main\.py/);
  assert.match(shown.diff, /^\+print\(2\)$/m);
  assert.equal(existsSync(ran), false);
  // A diff past the limit is shown up to it, and says so.
  await writeFile(join(project.workdir, 'big.txt'), 'x'.repeat(1.5 * (1 << 20)));
  git('add', 'big.txt');
  assert.equal((await get<{ truncated: boolean }>(W('git'))).truncated, true);
});

test('the results listed are the newest files, however many the workdir has', async t => {
  const { project, get } = await setup(t);
  // More files than are listed, made in an order other than their times.
  const start = Date.parse(project.created) / 1000 + 60;
  for (let index = 0; index < 250; index += 1) {
    const name = `out/${String((index * 97) % 250).padStart(3, '0')}.txt`;
    await mkdir(join(project.workdir, 'out'), { recursive: true });
    await writeFile(join(project.workdir, name), name);
    await utimes(join(project.workdir, name), start + index, start + index);
  }
  const { artifacts } = await get<{ artifacts: { path: string }[] }>(`/api/projects/${project.id}/artifacts`);
  const newest = Array.from({ length: 200 }, (_, at) => `out/${String(((249 - at) * 97) % 250).padStart(3, '0')}.txt`);
  assert.deepEqual(artifacts.map(row => row.path), newest);
  // A workdir moved or deleted lists nothing.
  await rm(project.workdir, { recursive: true, force: true });
  assert.deepEqual((await get<{ artifacts: unknown[] }>(`/api/projects/${project.id}/artifacts`)).artifacts, []);
});

test('the results listed are what the project delivered and changed, not what its repository ignores', async t => {
  const { store, project, get } = await setup(t, {
    '.gitignore': 'dist/\nout/\n', 'dist/index.js': 'built', 'src/index.js': 'source', 'out/report.md': '# Report', 'docs/old.md': 'old',
  });
  execFileSync('git', ['init', '-q'], { cwd: project.workdir });
  // docs/old.md predates the project; out/report.md does too, and git ignores it, but it was delivered.
  const before = Date.parse(project.created) / 1000 - 60;
  for (const path of ['docs/old.md', 'out/report.md']) await utimes(join(project.workdir, path), before, before);
  // A file written as the project was made may carry a time a little before it, by the clock files are stamped with.
  for (const path of ['dist/index.js', 'src/index.js']) await utimes(join(project.workdir, path), before + 120, before + 120);
  store.save({ ...project, delivered: { 'out/report.md': [] } });
  const { artifacts } = await get<{ artifacts: { path: string }[] }>(`/api/projects/${project.id}/artifacts`);
  assert.deepEqual(artifacts.map(row => row.path), ['out/report.md', 'src/index.js']);
});

test('an HTML result is previewed as one page, its local files inlined, that can reach nothing', async t => {
  const files = {
    'site/index.html': '<!doctype html><html><head><link rel="stylesheet" href="style.css?v=1"><script src="app.js"></script>'
      + '<style>h1 { background: url("img/dot.png") }</style></head><body><img src="img/dot.png"><a href="next.html">next</a>'
      + '<img src="../../secret.png"><img src="https://example.com/x.png"><div style="background:url(\'/img/dot.png\')"></div></body></html>',
    'site/style.css': 'body { background: url(img/dot.png) }',
    'site/app.js': 'document.title = "ok";',
    'site/img/dot.png': 'PNG',
    'site/deep/page.html': '<script src="../app.js"></script>',
  };
  const { project, call, get } = await setup(t, files);
  const P = `/api/projects/${project.id}`;
  const shown = await get<{ html: string; warnings: string[]; file_count: number; served_page: boolean }>(`${P}/artifact/preview?path=site/index.html`);
  const data = (type: string, text: string) => `data:${type};base64,${Buffer.from(text).toString('base64')}`;
  const dot = data('image/png', 'PNG');
  const policy = 'default-src \'none\'; script-src \'unsafe-inline\' data: blob:; style-src \'unsafe-inline\' data:; '
    + 'img-src data: blob:; font-src data:; media-src data: blob:; base-uri \'none\'; form-action \'none\'';
  assert.equal(shown.html, `<!doctype html><meta http-equiv="Content-Security-Policy" content="${policy}">`
    + `<html><head><link rel="stylesheet" href="${data('text/css', `body { background: url(${dot}) }`)}">`
    + `<script src="${data('text/javascript', 'document.title = "ok";')}"></script><style>h1 { background: url(${dot}) }</style></head>`
    + `<body><img src="${dot}"><a href="next.html">next</a><img src="data:,"><img src="https://example.com/x.png">`
    + `<div style="background:url(${dot})"></div></body></html>`);
  assert.deepEqual(shown.warnings, ['Could not include local resource: ../../secret.png']);
  assert.equal(shown.file_count, 4);
  assert.equal(shown.served_page, true);
  assert.equal((await call(`${P}/artifact/preview?path=site/style.css`)).status, 404);
  // Without a token there is no cookie to grant, and the frame loads the same page by URL, sandboxed by its policy.
  assert.equal((await call(`${P}/artifact/preview?path=site/index.html`)).headers.get('set-cookie'), null);
  const served = await call(`${P}/artifact/preview/page?path=site/index.html`);
  assert.deepEqual(['content-type', 'content-security-policy', 'x-content-type-options', 'cache-control', 'referrer-policy'].map(name => served.headers.get(name)),
    ['text/html; charset=utf-8', `sandbox allow-scripts allow-downloads; ${policy}`, 'nosniff', 'private, no-store', 'no-referrer']);
  assert.equal(await served.text(), shown.html);
  assert.equal((await call(`${P}/artifact/preview/page?path=site/style.css`)).status, 404);

  // The page and the files it names as they are, in a zip rooted at its folder, with what was left out.
  const site = await call(`${P}/artifact/bundle?path=site/index.html`);
  assert.deepEqual([site.headers.get('content-type'), site.headers.get('content-disposition')], ['application/zip', 'attachment; filename*=UTF-8\'\'argus-website.zip']);
  assert.deepEqual(unzip(Buffer.from(await site.arrayBuffer())), {
    'index.html': files['site/index.html'], 'style.css': files['site/style.css'], 'app.js': files['site/app.js'], 'img/dot.png': 'PNG',
    'ARGUS-PREVIEW-NOTES.txt': 'Could not include local resource: ../../secret.png',
  });
  // A page that names a file above its folder is zipped from the folder holding both.
  const deep = await call(`${P}/artifact/bundle?path=site/deep/page.html`);
  assert.deepEqual(unzip(Buffer.from(await deep.arrayBuffer())), { 'deep/page.html': files['site/deep/page.html'], 'app.js': files['site/app.js'] });

  // Results chosen together: each once, under its path; those not there are named in the summary's README.
  const bundle = (body: Record<string, unknown>) => call(`${P}/artifacts/bundle`, { method: 'POST', body: JSON.stringify(body) });
  const chosen = await bundle({
    paths: ['site/app.js', ' site/img/dot.png ', 'site/app.js', 'gone.csv', '../../secret.png'], name: 'Q3: sales / "final".', summary: ' The sales site. ',
  });
  assert.equal(chosen.headers.get('content-disposition'), 'attachment; filename*=UTF-8\'\'Q3%20sales%20final.zip');
  assert.deepEqual(unzip(Buffer.from(await chosen.arrayBuffer())), {
    'site/app.js': files['site/app.js'], 'site/img/dot.png': 'PNG',
    'README-Argus.md': 'The sales site.\n\nNot included (no longer available):\n- gone.csv\n- ../../secret.png\n',
  });
  assert.equal((await bundle({ paths: ['site/app.js'], name: ' .. ' })).headers.get('content-disposition'), 'attachment; filename*=UTF-8\'\'argus-results.zip');
  assert.equal((await bundle({ paths: ['gone.csv'] })).status, 404);
  assert.equal((await bundle({ paths: [] })).status, 400);
  // Past 2 GiB in all, the results download one by one (the file is sparse, taking no disk).
  await writeFile(join(project.workdir, 'huge.bin'), '');
  await truncate(join(project.workdir, 'huge.bin'), 2 ** 31 + 1);
  assert.equal((await bundle({ paths: ['huge.bin'] })).status, 413);
});

test('with a token, the preview grants a cookie that its page alone takes, as a frame cannot send the bearer, and that is not the token', async t => {
  const { project, call } = await setup(t, { 'index.html': '<p>hi</p>' }, 'secret');
  const P = `/api/projects/${project.id}`;
  const page = (cookie?: string) => call(`${P}/artifact/preview/page?path=index.html`, { headers: { authorization: '', ...cookie ? { cookie } : {} } });
  assert.equal((await page()).status, 401);
  assert.equal((await page('argus_preview_access=wrong')).status, 401);
  const granted = (await call(`${P}/artifact/preview?path=index.html`)).headers.get('set-cookie');
  assert.match(granted ?? '', /^argus_preview_access=[\w-]{36}; Path=\/api\/projects; HttpOnly; SameSite=Strict$/);
  assert.doesNotMatch(granted!, /secret/);
  const shown = await page(`theme=dark; ${granted!.split(';')[0]}`);
  assert.equal(shown.status, 200);
  assert.match(await shown.text(), /<p>hi<\/p>$/);
  assert.equal((await call(`${P}/artifact/preview?path=index.html`, { headers: { authorization: '', cookie: 'argus_preview_access=secret' } })).status, 401);
});

test("a result's editions are saved beside it, never over it, and a result delivered again shows what changed", async t => {
  const report = '# Sales\n\nNorth 10\n';
  const { store, project, call, get } = await setup(t, { 'report.md': report, 'plot.png': 'PNG\0', 'big.md': 'x'.repeat(512 * 1024 + 1) });
  const P = `/api/projects/${project.id}`;
  type Editions = { original: string; versions: { version: number; content: string; note: string }[] };
  const editions = (kind: string, source: string) => get<Editions>(`${P}/editions/${kind}?${new URLSearchParams({ source })}`);
  const save = (kind: string, body: unknown) =>
    call(`${P}/editions/${kind}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  const saved = (versions: Editions['versions']) => versions.map(({ version, content, note }) => [version, content, note]);

  assert.deepEqual(await editions('artifact', 'report.md'), { original: report, versions: [] });
  assert.deepEqual(await (await save('artifact', { source: 'report.md', content: '# Sales\n\nNorth 12\n', base_version: 0 })).json(),
    { version: 1, content: '# Sales\n\nNorth 12\n' });
  // A save after a version it never saw is refused.
  assert.equal((await save('artifact', { source: 'report.md', content: 'stale', base_version: 0 })).status, 409);
  assert.equal((await save('artifact', { source: 'report.md', content: '# Sales\n', note: 'shorter', base_version: 1 })).status, 200);
  // The first edition was saved with the file as it was, as version 0, and the file is still as it was.
  const { original, versions } = await editions('artifact', 'report.md');
  assert.equal(original, report);
  assert.deepEqual(saved(versions), [[0, report, ''], [1, '# Sales\n\nNorth 12\n', ''], [2, '# Sales\n', 'shorter']]);
  // What an edition, saved or not, changes in the file as it is.
  assert.deepEqual(await (await save('artifact/diff', { source: 'report.md', content: '# Sales\n\nNorth 12\n' })).json(),
    { diff: '--- a/report.md\n+++ b/report.md\n@@ -1,3 +1,3 @@\n # Sales\n \n-North 10\n+North 12\n', truncated: false });
  assert.deepEqual(await (await save('artifact/diff', { source: 'report.md', content: report })).json(), { diff: '', truncated: false });
  // Only a text result of the workdir, small enough to edit in the page, has editions.
  for (const [source, status] of [['plot.png', 422], ['big.md', 413], ['missing.md', 404], [join(store.dir(project.id), 'project.json'), 404]] as const) {
    assert.equal((await call(`${P}/editions/artifact?${new URLSearchParams({ source })}`)).status, status, source);
  }
  // An answer's revisions start from the answer as recorded.
  await save('conversation', { source: 'm0-operator', content: 'Another answer', note: 'Ask it again', original: 'The answer', base_version: 0 });
  assert.deepEqual(saved((await editions('conversation', 'm0-operator')).versions), [[0, 'The answer', ''], [1, 'Another answer', 'Ask it again']]);
  assert.deepEqual(await editions('conversation', 'm2-operator'), { original: '', versions: [] });

  const dir = store.dir(project.id);
  const [first] = keep(project, dir, ['report.md']);
  await writeFile(join(project.workdir, 'report.md'), '# Sales\n\nNorth 12\nSouth 7\n');
  const [second] = keep(project, dir, ['report.md']);
  const diff = (before: string, after: string) => get(`${P}/delivery-diff?${new URLSearchParams({ path: 'report.md', before, after })}`);
  assert.deepEqual(await diff(first!.version, second!.version), {
    path: 'report.md', available: true, truncated: false,
    diff: '--- a/report.md\n+++ b/report.md\n@@ -1,3 +1,4 @@\n # Sales\n \n-North 10\n+North 12\n+South 7\n',
  });
  // A file keeps its last ten copies: an older one, or anything but a copy, is no longer there to compare.
  for (let n = 0; n < 9; n++) {
    await writeFile(join(project.workdir, 'report.md'), `# Sales\n\nNorth ${n}\n`);
    keep(project, dir, ['report.md']);
  }
  assert.equal(project.delivered?.['report.md']?.length, 10);
  for (const before of [first!.version, '../project.json']) {
    assert.deepEqual(await diff(before, second!.version), { path: 'report.md', available: false, diff: '', truncated: false });
  }
});

test('what changed between two deliveries reads as a unified diff, changes far apart in hunks of their own', () => {
  const lines = (line: (n: number) => string) => `${Array.from({ length: 20 }, (_, n) => line(n)).join('\n')}\n`;
  assert.equal(unified('a.txt', lines(String), lines(n => n === 2 || n === 15 ? `${n}!` : String(n))), '--- a/a.txt\n+++ b/a.txt\n'
    + '@@ -1,6 +1,6 @@\n 0\n 1\n-2\n+2!\n 3\n 4\n 5\n@@ -13,7 +13,7 @@\n 12\n 13\n 14\n-15\n+15!\n 16\n 17\n 18\n');
  assert.equal(unified('a.txt', 'same\n', 'same\n'), '');
  // A change too long to match line by line reads as all removed, then all added.
  const many = (mark: string) => Array.from({ length: 5000 }, (_, n) => `${mark}${n}`).join('\n');
  const rows = unified('big.txt', many('a'), many('b')).split('\n');
  assert.deepEqual([rows[2], rows[3], rows[5003], rows.length], ['@@ -1,5000 +1,5000 @@', '-a0', '+b0', 10004]);
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
  store.append(id, { type: 'handoff', role: 'planner', to: 'manager', letter: 'Totals:\n\n| region | sales |\n| - | - |\n| north | 4 |\n\nDone.' });
  store.append(id, { type: 'task.closed', task: 't1' });
  store.append(id, { type: 'task.added', task: years });
  end('engineer', 't2', 'codex exited with code 1');
  const { questions, ...record } = store.load(id);
  // A record from before there could be several questions holds its one as `question`.
  const question = { role: 'engineer' as const, task: 't2', question: 'Which years?' };
  await writeFile(join(store.dir(id), 'project.json'), JSON.stringify({
    ...record, name: chart.title, status: 'asking', question, tasks: [{ ...chart, status: 'closed', note: { from: 'reviewer', text: 'The chart matches the data.' } }, years],
  }));
  assert.deepEqual([questions, store.load(id).questions], [[], [question]]);
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
  // A project is named as the project list names it; a match is quoted with the table it is in, whole.
  assert.equal(results[0]!.title, 'Sales chart');
  assert.equal((await get<{ results: { snippet: string }[] }>('/api/search?q=north')).results[0]!.snippet,
    '| region | sales |\n| - | - |\n| north | 4 |');
  assert.equal((await call('/api/search?q=%20')).status, 422);
});

test('the resources panel reads the CPUs and memory, and the GPUs nvidia-smi lists, when asked', async t => {
  const { store, get } = await setup(t);
  type Accelerator = { kind: string; status: string; device_count: number; detail: string; devices: Record<string, unknown>[] };
  const accelerators = async () => (await get<{ enforcement: string; accelerators: Accelerator[] }>('/api/system/resources')).accelerators;
  const bin = join(store.home, 'bin');
  await mkdir(bin);
  const path = process.env.PATH;
  t.after(() => { process.env.PATH = path; });
  process.env.PATH = bin;
  // Without nvidia-smi there is no GPU to tell of.
  const [cpu, ...rest] = await accelerators();
  assert.deepEqual(rest, []);
  assert.equal(cpu!.kind, 'cpu');
  assert.ok(cpu!.device_count > 0);
  assert.match(cpu!.detail, /^load \d+\.\d\d · memory \d+\.\d of \d+\.\d GiB in use$/);
  if (process.platform === 'win32') return;
  await writeFile(join(bin, 'nvidia-smi'), '#!/bin/sh\nprintf "0, NVIDIA A100, 81920, 1024, 37\\n1, NVIDIA A100, 81920, [N/A], 0\\n"\n');
  await chmod(join(bin, 'nvidia-smi'), 0o755);
  assert.deepEqual((await accelerators()).slice(1), [{
    kind: 'cuda', status: 'available', device_count: 2, detail: '', devices: [
      { index: '0', name: 'NVIDIA A100', total_memory_mib: 81920, used_memory_mib: 1024, utilization_percent: 37 },
      { index: '1', name: 'NVIDIA A100', total_memory_mib: 81920, used_memory_mib: 0, utilization_percent: 0 },
    ],
  }]);
  await writeFile(join(bin, 'nvidia-smi'), '#!/bin/sh\necho "NVIDIA-SMI has failed" >&2\nexit 9\n');
  const [, failed] = await accelerators();
  assert.deepEqual([failed!.kind, failed!.status], ['cuda', 'inaccessible']);
  assert.match(failed!.detail, /NVIDIA-SMI has failed/);
});
