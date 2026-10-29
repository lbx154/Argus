import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, rmSync } from 'node:fs';
import test, { type TestContext } from 'node:test';
import { chmod, mkdir, mkdtemp, readFile, rm, truncate, utimes, writeFile } from 'node:fs/promises';
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

test('unknown file extensions preview UTF-8 text, including JSONL, and leave binary bytes downloadable', async t => {
  const jsonl = '{"sample":1,"value":42}\n{"sample":2,"value":43}\n';
  // A file is judged by its first bytes, which may end inside a character.
  const wide = '中'.repeat(3000);
  const { project, call, get } = await setup(t, { 'results.jsonl': jsonl, 'notes.unknown': '研究记录\n', 'README': 'No extension\n', 'wide.unknown': wide });
  await writeFile(join(project.workdir, 'nul.unknown'), Buffer.from([65, 0, 66]));
  await writeFile(join(project.workdir, 'invalid.unknown'), Buffer.from([0xff, 0xfe]));
  const P = `/api/projects/${project.id}`;
  for (const [path, preview] of [['results.jsonl', jsonl], ['notes.unknown', '研究记录\n'], ['README', 'No extension\n'], ['wide.unknown', wide]]) {
    assert.deepEqual(await get(`${P}/artifact?path=${path}`).then(({ kind, mime, preview: text }) => ({ kind, mime, text })),
      { kind: 'text', mime: 'text/plain; charset=utf-8', text: preview });
    assert.match((await call(`${P}/artifact/raw?path=${path}`)).headers.get('content-type')!, /^text\/plain/);
  }
  for (const path of ['nul.unknown', 'invalid.unknown']) {
    assert.equal((await get(`${P}/artifact?path=${path}`)).kind, 'binary');
    assert.equal((await call(`${P}/artifact/raw?path=${path}&download=true`)).status, 200);
  }
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
  // Conversation rewrites are new messages; there is no version endpoint for them.
  assert.equal((await save('conversation', { source: 'm0-operator', content: 'Another answer', base_version: 0 })).status, 404);
  assert.equal((await call(`${P}/editions/conversation?source=m0-operator`)).status, 404);

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

test('a task in full and a search of every project\'s record', async t => {
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

  assert.equal((await call(`${P}/work-log/download?role=engineer&task_id=t1`)).status, 404);

  // Every word asked, in the request, the conversation (the Manager's letters are its replies) and the team's letters.
  const { results } = await get<{ results: Record<string, unknown>[] }>('/api/search?q=CHART%20sales');
  assert.deepEqual(results.map(({ sid, kind, role, snippet }) => [sid, kind, role, snippet]), [
    [id, 'conversation', 'operator', 'Chart the sales by region.'],
    [id, 'conversation', 'argus', chart.objective],
    [id, 'project', 'engineer', 'chart.png drawn from sales.csv'],
  ]);
  assert.equal(results[0]!.message_id, 'm0-operator');
  // A project is named as the project list names it; a match is quoted with the table it is in, whole.
  assert.equal(results[0]!.title, 'Sales chart');
  assert.equal((await get<{ results: { snippet: string }[] }>('/api/search?q=north')).results[0]!.snippet,
    '| region | sales |\n| - | - |\n| north | 4 |');
  assert.equal((await call('/api/search?q=%20')).status, 422);
});

test('the resources panel reads the CPUs and memory, and the GPUs nvidia-smi lists, when asked', async t => {
  const { store, get } = await setup(t);
  type Accelerator = { kind: string; status: string; device_count: number; detail?: string; load_average: number; used_memory_gib: number; total_memory_gib: number; devices: Record<string, unknown>[] };
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
  assert.equal(cpu!.detail, undefined);
  assert.ok(cpu!.load_average >= 0);
  assert.ok(cpu!.total_memory_gib > 0);
  assert.ok(cpu!.used_memory_gib >= 0 && cpu!.used_memory_gib <= cpu!.total_memory_gib);
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
  assert.match(failed!.detail!, /NVIDIA-SMI has failed/);
});

// Captured from the served cockpit: the request was returned three times and its path
// began "mponent/". The Manager really repeated the words, so that speaker stays.
test('search quotes complete paths and keeps one hit per recorded message', async t => {
  const { store, project, get } = await setup(t);
  const fixture = JSON.parse(await readFile(new URL('./fixtures/search-path-record.json', import.meta.url), 'utf8'));
  project.request = fixture.request;
  store.save(project);
  await writeFile(join(store.dir(project.id), 'events.jsonl'), fixture.events.map((event: unknown) => JSON.stringify(event)).join('\n') + '\n');
  type Hit = { sid: string; kind: string; role: string; snippet: string; message_id?: string };
  const search = () => get<{ results: Hit[]; partial: boolean }>('/api/search?q=needle');
  const first = await search();
  assert.deepEqual(first.results.map(hit => [hit.role, hit.kind]), [['operator', 'conversation'], ['argus', 'conversation']]);
  assert.ok(first.results.every(hit => hit.snippet.startsWith('/research/complete-path-component/')));
  assert.ok(first.results.every(hit => hit.snippet.includes('/needle/report.md')));
  assert.equal(first.results[0]!.message_id, 'm0-operator');
  // Identical words later, from another role, or in another project are independent hits.
  store.append(project.id, { type: 'message', from: 'operator', request_id: 'r2', text: fixture.request });
  store.append(project.id, { type: 'handoff', role: 'engineer', task: 't1', to: 'reviewer', letter: fixture.request });
  const other = store.create({ request: fixture.request, workdir: project.workdir, backend: 'codex', model: null });
  const next = await search();
  assert.equal(next.results.length, 5);
  assert.equal(next.results.filter(hit => hit.sid === project.id && hit.role === 'operator').length, 2);
  assert.ok(next.results.some(hit => hit.sid === other.id && hit.role === 'operator' && hit.kind === 'project'));
  assert.ok(next.results.some(hit => hit.role === 'engineer'));
  assert.equal(next.partial, false);
});

// Reviewer reproduction: a space inside an inline-code path was treated as the excerpt boundary.
test('search keeps a backtick-quoted path with spaces whole', async t => {
  const { store, project, get } = await setup(t);
  const fixture = JSON.parse(await readFile(new URL('./fixtures/search-spaced-path-record.json', import.meta.url), 'utf8'));
  project.request = fixture.request;
  store.save(project);
  await writeFile(join(store.dir(project.id), 'events.jsonl'), fixture.events.map((event: unknown) => JSON.stringify(event)).join('\n') + '\n');
  const { results } = await get<{ results: { snippet: string }[] }>('/api/search?q=spaceneedle');
  assert.equal(results.length, 1);
  assert.ok(results[0]!.snippet.startsWith('`/research/My Project/'), results[0]!.snippet);
  const path = fixture.request.slice(fixture.request.indexOf('`'), fixture.request.lastIndexOf('`') + 1);
  assert.ok(results[0]!.snippet.includes(path), results[0]!.snippet);
});

// Reviewer reproduction: a hit inside a long run without spaces quoted the whole 790,000-character letter,
// and a search past its last result read every older project still.
test('search quotes near the hit and stops reading once its results are full', async t => {
  const { store, project, get } = await setup(t);
  // The project set up first is the older, searched last; its record cannot be read.
  rmSync(join(store.dir(project.id), 'events.jsonl'), { force: true });
  mkdirSync(join(store.dir(project.id), 'events.jsonl'));
  const newer = store.create({ request: 'newer', workdir: project.workdir, backend: 'codex', model: null });
  const blob = JSON.stringify({ rows: Array.from({ length: 5_000 }, (_, i) => ({ id: i, tag: i === 2_500 ? 'needle' : 'hay' })) });
  for (let i = 0; i < 51; i += 1) store.append(newer.id, { type: 'handoff', role: 'engineer', task: 't1', to: 'reviewer', letter: blob });
  const { results, partial, errors } = await get<{ results: { snippet: string }[]; partial: boolean; errors: string[] }>('/api/search?q=needle');
  assert.equal(results.length, 50);
  assert.ok(results.every(hit => hit.snippet.length <= 2_000 && hit.snippet.includes('needle')));
  assert.deepEqual([partial, errors], [true, []]);
});

test('recorded deliveries count occurrences per file and preview their saved text through every result route', async t => {
  const fixture = JSON.parse(await readFile(new URL('./fixtures/delivery-versions/record.json', import.meta.url), 'utf8')) as {
    text: { events: import('../src/serve/views.js').Recorded[]; copies: Record<string, string> };
  };
  const { store, project, get, call } = await setup(t, { 'report.md': '# 版本报告\n\n第二版数值：20\n', 'data.csv': 'value\n20\n' });
  const dir = store.dir(project.id);
  await mkdir(join(dir, 'delivered'));
  for (const [version, content] of Object.entries(fixture.text.copies)) await writeFile(join(dir, 'delivered', version), content);
  const eventsFile = join(dir, 'events.jsonl');
  // Read once, then append: the same fold drives initial pages, the live stream and later pages.
  const { follow } = await import('../src/serve/views.js');
  const next = follow(store, project.id);
  await writeFile(eventsFile, fixture.text.events.slice(0, 2).map(event => JSON.stringify(event)).join('\n') + '\n');
  const firstBatch = next();
  await writeFile(eventsFile, fixture.text.events.map(event => JSON.stringify(event)).join('\n') + '\n');
  const rest = next();
  const receipts = [...firstBatch, ...rest].flatMap(event => event.delivery ? [event.delivery as import('@argus/contracts').DeliveryReceipt] : []);
  assert.deepEqual(receipts.map(receipt => receipt.targets.map(file => [file.path, file.delivery_number])), [
    [['report.md', 1]], [['report.md', 2], ['data.csv', 1]], [['report.md', 3], ['data.csv', 2]],
  ]);
  assert.equal(receipts[1]!.snapshots![0]!.version, receipts[2]!.snapshots![0]!.version, 'unchanged content reuses its copy but counts as another delivery');
  const P = `/api/projects/${project.id}`;
  const page = await get<import('@argus/contracts').ConversationPage>(`${P}/conversation?limit=1`);
  assert.equal((page.rows[0]!.delivery as import('@argus/contracts').DeliveryReceipt).targets[0]!.delivery_number, 3, 'a page does not reset occurrence counts');
  for (const [index, value] of [[0, 10], [1, 20], [2, 20]] as const) {
    const delivery = receipts[index]!.delivery_id;
    const q = new URLSearchParams({ path: 'report.md', delivery });
    const info = await get<import('@argus/contracts').ArtifactInfo>(`${P}/artifact?${q}`);
    assert.equal(info.kind, 'markdown');
    assert.match(info.preview!, new RegExp(`数值：${value}`));
    const raw = await call(`${P}/artifact/raw?${q}&download=true`);
    assert.match(raw.headers.get('content-disposition')!, /report.md/);
    assert.match(await raw.text(), new RegExp(`数值：${value}`));
    const bundle = await call(`${P}/artifacts/bundle`, { method: 'POST', body: JSON.stringify({ paths: ['report.md'], delivery }) });
    assert.equal(bundle.status, 200);
    assert.match(unzip(Buffer.from(await bundle.arrayBuffer()))['report.md']!, new RegExp(`数值：${value}`));
  }
  await rm(join(project.workdir, 'report.md'));
  assert.match((await get<{ preview: string }>(`${P}/artifact?path=report.md&delivery=${receipts[0]!.delivery_id}`)).preview, /数值：10/);
  const version = receipts[0]!.snapshots![0]!.version;
  await rm(join(dir, 'delivered', version));
  for (const route of ['artifact', 'artifact/raw']) {
    const response = await call(`${P}/${route}?path=report.md&delivery=${receipts[0]!.delivery_id}`);
    assert.equal(response.status, 410);
    assert.equal((await response.json() as { code: string }).code, 'delivery_snapshot_unavailable');
  }
  assert.equal((await call(`${P}/artifact?path=data.csv&delivery=${receipts[0]!.delivery_id}`)).status, 404, 'a delivery cannot read other workspace files');
  assert.equal((await call(`${P}/artifact?path=report.md&delivery=d999`)).status, 404);
});

test('delivered HTML, nested resources, model assets and downloads use their own copies; unsaved versions fail explicitly', async t => {
  const { store, project, call, get } = await setup(t, {
    'index.html': '<h1>Old page</h1><link rel="stylesheet" href="style.css"><img src="unlisted.png">',
    'style.css': 'body { color: red }', 'unlisted.png': 'current image', 'data.bin': 'old\0',
    'robot.urdf': '<robot name="old"/>', 'mesh.obj': 'v 0 0 1\n', 'legacy.md': 'now',
  });
  const paths = ['index.html', 'style.css', 'data.bin', 'robot.urdf', 'mesh.obj'];
  const first = keep(project, store.dir(project.id), paths);
  store.append(project.id, { type: 'handoff', role: 'manager', to: 'operator', letter: 'Old page', open: paths, snapshots: first });
  const P = `/api/projects/${project.id}`;
  assert.equal(await (await call(`${P}/artifact/raw?path=data.bin&delivery=d0`)).text(), 'old\0');
  await writeFile(join(project.workdir, 'index.html'), '<h1>New page</h1>');
  await writeFile(join(project.workdir, 'style.css'), 'body { color: blue }');
  await writeFile(join(project.workdir, 'data.bin'), 'updated binary\0');
  await writeFile(join(project.workdir, 'mesh.obj'), 'v 9 9 9\n');
  const second = keep(project, store.dir(project.id), paths);
  store.append(project.id, { type: 'handoff', role: 'manager', to: 'operator', letter: 'New page', open: paths, snapshots: second });
  const old = await get<{ html: string; warnings: string[] }>(`${P}/artifact/preview?path=index.html&delivery=d0`);
  assert.match(old.html, /Old page/);
  assert.ok(old.html.includes(Buffer.from('body { color: red }').toString('base64')));
  assert.equal(old.warnings.length, 1, 'resources outside this delivery are reported, never loaded from current files');
  assert.match(await (await call(`${P}/artifact/preview/page?path=index.html&delivery=d0`)).text(), /Old page/);
  assert.match(await (await call(`${P}/artifact/preview/page?path=index.html&delivery=d1`)).text(), /New page/);
  const files = unzip(Buffer.from(await (await call(`${P}/artifact/bundle?path=index.html&delivery=d0`)).arrayBuffer()));
  assert.match(files['index.html']!, /Old page/);
  assert.equal(files['style.css'], 'body { color: red }');
  assert.equal(files['unlisted.png'], undefined);
  const model = await call(`${P}/artifact/model-asset?path=robot.urdf&ref=package://robot/mesh.obj&delivery=d0`);
  assert.equal(model.status, 200);
  assert.equal(await model.text(), 'v 0 0 1\n');
  for (const route of ['artifact', 'artifact/raw']) {
    const response = await call(`${P}/${route}?path=data.bin&delivery=d0`);
    assert.equal(response.status, 410);
    assert.equal((await response.json() as { code: string }).code, 'delivery_file_updated');
  }
  assert.equal(await (await call(`${P}/artifact/raw?path=data.bin&delivery=d1`)).text(), 'updated binary\0');
  store.append(project.id, { type: 'handoff', role: 'manager', to: 'operator', letter: 'No snapshot', open: ['legacy.md'] });
  assert.equal((await call(`${P}/artifact?path=legacy.md&delivery=d2`)).status, 410);
});

test('a delivered model stops at its first available mesh candidate and reports that candidate’s own failure', async t => {
  const fixture = JSON.parse(await readFile(new URL('./fixtures/delivery-versions/model-candidates.json', import.meta.url), 'utf8')) as {
    events: import('../src/serve/views.js').Recorded[];
    copies: Record<string, string>;
    files: Record<string, { base64: string; mtime: number }>;
    originalRoot: { base64: string; mtime: number };
  };
  const { store, project, call } = await setup(t);
  const dir = store.dir(project.id);
  await mkdir(join(dir, 'delivered'));
  for (const [version, content] of Object.entries(fixture.copies)) await writeFile(join(dir, 'delivered', version), content);
  for (const [path, file] of Object.entries(fixture.files)) {
    await mkdir(dirname(join(project.workdir, path)), { recursive: true });
    await writeFile(join(project.workdir, path), Buffer.from(file.base64, 'base64'));
    await utimes(join(project.workdir, path), file.mtime, file.mtime);
  }
  await writeFile(join(dir, 'events.jsonl'), fixture.events.map(event => JSON.stringify(event)).join('\n') + '\n');
  const { webEvents } = await import('../src/serve/views.js');
  const receipt = webEvents(fixture.events).find(event => event.delivery)!.delivery as import('@argus/contracts').DeliveryReceipt;
  const P = `/api/projects/${project.id}`;
  const raw = (path: string) => call(`${P}/artifact/raw?${new URLSearchParams({ path, delivery: receipt.delivery_id })}`);
  const asset = () => call(`${P}/artifact/model-asset?${new URLSearchParams({ path: 'robot/robot.urdf', ref: 'package://robot/mesh.stl', delivery: receipt.delivery_id })}`);
  assert.equal((await raw('robot/mesh.stl')).status, 200);
  assert.equal((await raw('mesh.stl')).status, 410, 'a later candidate was updated, independently of the model’s mesh');
  const found = await asset();
  assert.equal(found.status, 200, 'the first candidate wins before the unrelated updated candidate is read');
  assert.deepEqual(Buffer.from(await found.arrayBuffer()), Buffer.from(fixture.files['robot/mesh.stl']!.base64, 'base64'));

  // Make the later candidate readable, then invalidate the selected mesh: its own failure must propagate.
  await writeFile(join(project.workdir, 'mesh.stl'), Buffer.from(fixture.originalRoot.base64, 'base64'));
  await utimes(join(project.workdir, 'mesh.stl'), fixture.originalRoot.mtime, fixture.originalRoot.mtime);
  await writeFile(join(project.workdir, 'robot/mesh.stl'), Buffer.concat([Buffer.from(fixture.files['robot/mesh.stl']!.base64, 'base64'), Buffer.from([0])]));
  assert.equal((await raw('mesh.stl')).status, 200);
  const unavailable = await asset();
  assert.equal(unavailable.status, 410);
  assert.equal((await unavailable.json() as { code: string }).code, 'delivery_file_updated');
});
