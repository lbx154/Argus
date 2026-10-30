import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs, { readFileSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { syncBuiltinESMExports } from 'node:module';
import type { Snapshot } from '@argus/contracts';
import { firstLine } from '../src/flow.js';
import { Store, type Project } from '../src/store.js';
import { serve } from '../src/serve/index.js';
import { conversationRows, projectRecord, progressMap, readRecord, taskTrace, type Recorded } from '../src/serve/views.js';

const fixture = new URL('../../../frontend/web/src/test/fixtures/home/projects/p-c855623c/', import.meta.url);
const source = JSON.parse(readFileSync(new URL('project.json', fixture), 'utf8')) as Project;
const record = readFileSync(new URL('./fixtures/conversation-task/events.jsonl', import.meta.url), 'utf8').trim().split('\n').map(line => JSON.parse(line) as Recorded);
const idle = { since: null, calls: [], requests: [] };

test('snapshot and map carry task summaries; on-demand details retain the recorded objective', async t => {
  const home = mkdtempSync(join(tmpdir(), 'argus-workbench-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const work = store.create({ request: source.request, workdir: home, backend: 'codex', model: null });
  work.tasks = source.tasks;
  work.status = 'stopped';
  store.save(work);
  writeFileSync(join(store.dir(work.id), 'events.jsonl'), readFileSync(new URL('events.jsonl', fixture)));
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
    assert.deepEqual(Object.keys(summary).sort(), ['deps', 'id', 'priority', 'status', 'title']);
    assert.equal(summary.title, source.tasks[i]!.title);
    assert.equal(summary.status, 'done');
  }
  const map = await get('map');
  const detail = await get('backlog/t3');
  assert.ok(map.tasks.every((task: object) => !('objective' in task)));
  const incremental = await get(`map?after=${map.cursor}`);
  assert.ok(incremental.tasks.every((task: object) => !('objective' in task)));
  const request = await get('backlog/q0');
  assert.equal(request.item.objective, source.request);
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
    const map = progressMap(store, work, 0, []).tasks.find(task => task.id === 't3');
    if (!row) continue;
    assert.equal(row.ts, ts);
    assert.equal(map!.ts, ts);
    assert.equal(map!.started_ts, ts);
    if (map!.latest_action) assert.ok(String(row.progress).startsWith(firstLine(map!.latest_action.text).replace(/…$/, '')));
    else assert.equal(row.progress, '');
    assert.deepEqual(row.result_paths, map!.result_paths);
    assert.deepEqual(row.result_paths, work.tasks[2]!.status === 'closed' ? ['result.md'] : []);
    // Controlled pending state over the real record: the question itself owns the card.
    work.questions = [{ role: 'reviewer', task: 't3', question: 'Ship the recorded result?' }];
    const asking = conversationRows(reading, work, idle).find(row => row.type === 'ui.task')!;
    const card = progressMap(store, work, 0, []).tasks.find(task => task.id === 't3')!;
    assert.equal(asking.progress, '');
    assert.equal(card.latest_action, undefined);
    assert.equal(card.status, work.tasks[2]!.status === 'closed' ? 'done' : 'blocked');
    assert.equal('pending_question' in card, false);
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

test('real task record: details and traces share the incremental reading and clear errors on the next successful call', async t => {
  const home = mkdtempSync(join(tmpdir(), 'argus-shared-reading-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const work = store.create({ request: source.request, workdir: home, backend: 'codex', model: null });
  work.tasks = source.tasks;
  work.status = 'stopped';
  store.save(work);
  const path = join(store.dir(work.id), 'events.jsonl');
  writeFileSync(path, readFileSync(new URL('events.jsonl', fixture)));
  const server = await serve({ store, callAgent: async () => { throw new Error('read-only replay'); }, backend: 'codex', model: null, port: 0, web: home });
  t.after(() => server.close());
  const get = async (tail: string) => {
    const response = await fetch(`${server.url}/api/projects/${work.id}/${tail}`);
    assert.equal(response.status, 200);
    return response.json();
  };
  const reading = readRecord(store, work.id);
  const expectedTrace = taskTrace(store, work, 't3');
  const reads = t.mock.method(fs, 'readSync');
  syncBuiltinESMExports();
  t.after(() => { reads.mock.restore(); syncBuiltinESMExports(); });
  const detail = await get('backlog/t3');
  const request = await get('backlog/q0');
  assert.equal(detail.item.objective, source.tasks[2]!.objective);
  assert.equal(request.item.objective, source.request);
  assert.ok(detail.item.notes.includes('result.md'));
  const closing = record.find(event => event.type === 'handoff' && event.role === 'reviewer' && event.task === 't3');
  assert.ok(closing?.type === 'handoff');
  assert.equal(detail.item.latest_action.text, closing.letter);
  assert.equal(request.item.notes, '');
  assert.deepEqual(await get('map-trace?task=t3'), expectedTrace);
  assert.ok(reads.mock.calls.every(call => call.arguments[1].byteLength === 0), 'warm requests read no old event bytes');
  // A modern delivery joins its original request by answers. Full progress must not
  // replace the request card's delivered paths with a team task's closing paths.
  store.append(work.id, { type: 'handoff', role: 'manager', to: 'operator', answers: 'r0',
    letter: 'Complete delivery\nThe final delivery sentence.', open: ['result.md'] });
  const delivered = (await get('backlog/q0')).item;
  assert.deepEqual(delivered.result_paths, ['result.md']);
  assert.equal(delivered.latest_action.text, 'Complete delivery\nThe final delivery sentence.');
  const end = record.find(event => event.type === 'call.end' && event.task === 't3')!;
  assert.equal(end.type, 'call.end');
  if (end.type !== 'call.end') return;
  for (const [task, error] of [['t3', 'first error'], ['t2', 'other task error'], ['t3', null]] as const) {
    const event: Recorded = { ...end, task, callId: `increment-${task}-${error}`, completed: !error, error };
    writeFileSync(path, JSON.stringify(event) + '\n', { flag: 'a' });
    const item = (await get(`backlog/${task}`)).item;
    assert.equal(item.last_error, error ?? '');
    assert.equal(readRecord(store, work.id), reading);
    assert.equal((await get(`map-trace?task=${task}`)).groups.at(-1).call_id, event.callId);
  }
  assert.equal((await get('backlog/t2')).item.last_error, 'other task error');
});
