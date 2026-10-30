import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import type { Snapshot } from '@argus/contracts';
import { firstLine } from '../src/flow.js';
import { Store, type Project } from '../src/store.js';
import { serve } from '../src/serve/index.js';
import { conversationRows, projectRecord, progressMap, type Recorded } from '../src/serve/views.js';

const fixture = new URL('../../../frontend/web/src/test/fixtures/home/projects/p-c855623c/', import.meta.url);
const source = JSON.parse(readFileSync(new URL('project.json', fixture), 'utf8')) as Project;
const record = readFileSync(new URL('./fixtures/conversation-task/events.jsonl', import.meta.url), 'utf8').trim().split('\n').map(line => JSON.parse(line) as Recorded);
const idle = { since: null, calls: [], requests: [] };

test('snapshot carries task summaries; map and task details retain the recorded objective', async t => {
  const home = mkdtempSync(join(tmpdir(), 'argus-workbench-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const work = store.create({ request: source.request, workdir: home, backend: 'codex', model: null });
  work.tasks = source.tasks;
  work.status = 'stopped';
  store.save(work);
  writeFileSync(join(store.dir(work.id), 'events.jsonl'), record.map(event => JSON.stringify(event)).join('\n') + '\n');
  const server = await serve({ store, callAgent: async () => { throw new Error('read-only replay'); }, backend: 'codex', model: null, port: 0, web: home });
  t.after(() => server.close());
  const get = async (path: string) => {
    const response = await fetch(`${server.url}/api/projects/${work.id}/${path}`);
    assert.equal(response.status, 200);
    return response.json();
  };
  const snap = await get('snapshot') as Snapshot;
  assert.equal(snap.session.objective, source.request);
  assert.equal('goal' in snap, false);
  assert.equal(snap.backlog.length, source.tasks.length);
  for (const [i, summary] of snap.backlog.entries()) {
    assert.equal('objective' in summary, false);
    assert.equal(summary.title, source.tasks[i]!.title);
    assert.equal(summary.status, 'done');
  }
  const map = await get('map');
  const detail = await get('backlog/t3');
  assert.equal(map.tasks.find((task: { id: string }) => task.id === 't3').objective, source.tasks[2]!.objective);
  assert.equal(detail.item.objective, source.tasks[2]!.objective);
  assert.ok(detail.item.notes.includes('result.md'));
});

test('recorded task: dialogue and map share hidden progress while asking, closing files, and the original added time', t => {
  const home = mkdtempSync(join(tmpdir(), 'argus-projection-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const work = store.create({ request: '', workdir: home, backend: 'codex', model: null });
  work.tasks = source.tasks.map(task => ({ ...task, status: 'open' }));
  work.status = 'working';
  const path = join(store.dir(work.id), 'events.jsonl');
  writeFileSync(path, '');
  const added = record.find(event => event.type === 'task.added')!;
  const ts = Date.parse(added.ts) / 1000;
  for (const event of record) {
    writeFileSync(path, JSON.stringify(event) + '\n', { flag: 'a' });
    const end = record.indexOf(event) + 1;
    if (event.type === 'task.closed') work.tasks[2]!.status = 'closed';
    const reading = projectRecord(record.slice(0, end));
    const row = conversationRows(reading, work, idle).find(row => row.type === 'ui.task');
    const map = progressMap(store, work, 0).tasks.find(task => task.id === 't3');
    if (!row) continue;
    assert.equal(row.ts, ts);
    assert.equal(map!.ts, ts);
    assert.equal(map!.started_ts, ts);
    assert.equal(row.progress, map!.latest_action ? firstLine(map!.latest_action.text) : '');
    assert.deepEqual(row.result_paths, map!.result_paths);
    assert.deepEqual(row.result_paths, work.tasks[2]!.status === 'closed' ? ['result.md'] : []);
    // Controlled pending state over the real record: the question itself owns the card.
    work.questions = [{ role: 'reviewer', task: 't3', question: 'Ship the recorded result?' }];
    const asking = conversationRows(reading, work, idle).find(row => row.type === 'ui.task')!;
    const card = progressMap(store, work, 0).tasks.find(task => task.id === 't3')!;
    assert.equal(asking.progress, '');
    assert.equal(card.latest_action, undefined);
    assert.equal(card.pending_question, work.questions[0]!.question);
    assert.deepEqual(asking.result_paths, card.result_paths);
    work.questions = [];
  }
});

test('read-only git returns only workspace consumers; trash uses the project naming rule', async t => {
  const home = mkdtempSync(join(tmpdir(), 'argus-workspace-git-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  execFileSync('git', ['init', '-b', 'main', home]);
  execFileSync('git', ['-C', home, 'config', 'user.name', 'Recorded Author']);
  execFileSync('git', ['-C', home, 'config', 'user.email', 'author@example.test']);
  writeFileSync(join(home, 'report.md'), '# Recorded report\n');
  execFileSync('git', ['-C', home, 'add', 'report.md']);
  execFileSync('git', ['-C', home, '-c', 'commit.gpgsign=false', 'commit', '-m', 'Save report']);
  execFileSync('git', ['-C', home, 'remote', 'add', 'origin', 'https://example.test/work.git']);
  const store = new Store(join(home, 'state'));
  const work = store.create({ request: 'Saved request title\nRequest body', workdir: home, backend: 'codex', model: null });
  const server = await serve({ store, callAgent: async () => { throw new Error('read-only replay'); }, backend: 'codex', model: null, port: 0, web: home });
  t.after(() => server.close());
  const git = await (await fetch(`${server.url}/api/v2/workspace/git?sid=${work.id}&workspace_id=project:${work.id}`)).json();
  assert.deepEqual(Object.keys(git).sort(), ['available', 'branch', 'diff', 'log', 'status', 'truncated']);
  assert.equal(git.branch, 'main');
  assert.match(git.log, /Recorded Author\tSave report/);
  assert.equal((await fetch(`${server.url}/api/projects/${work.id}`, { method: 'DELETE' })).status, 200);
  const trash = await (await fetch(`${server.url}/api/trash`)).json();
  assert.equal(trash.entries[0].label, 'Saved request title');
});
