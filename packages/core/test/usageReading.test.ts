import assert from 'node:assert/strict';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { Store } from '../src/store.js';
import { projectRow, readRecord, snapshot, type Recorded } from '../src/serve/views.js';

const record = fs.readFileSync(new URL('./fixtures/conversation-task/events.jsonl', import.meta.url), 'utf8')
  .trim().split('\n').map(line => JSON.parse(line) as Recorded);
const planner = record.find(event => event.type === 'call.end' && event.role === 'planner')! as Extract<Recorded, { type: 'call.end' }>;
const engineer = record.find(event => event.type === 'call.end' && event.role === 'engineer')! as typeof planner;

test('snapshots share the record reading, read appended usage bytes, and wait for complete lines', t => {
  const home = fs.mkdtempSync(join(tmpdir(), 'argus-usage-reading-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  const usagePath = join(store.dir(project.id), 'usage.jsonl');
  const eventsPath = join(store.dir(project.id), 'events.jsonl');
  fs.writeFileSync(eventsPath, JSON.stringify(planner) + '\n');
  const take = () => snapshot(store, project, { since: null, calls: [], requests: [] });
  assert.equal(take().spend_status, 'empty');
  assert.equal(take().last_call, null);

  const row = (event: typeof planner) => ({
    ts: Date.parse(event.ts) / 1000, callId: event.callId, role: event.role, backend: 'codex', model: event.model,
    tokens: event.usage, cost_usd: event.usage!.cost_usd,
  });
  const first = row(planner), second = { ...row(engineer), cost_usd: null };
  const line = (value: unknown) => JSON.stringify(value) + '\n';
  const initial = line(first);
  fs.writeFileSync(usagePath, initial);
  const opened = new Map<number, string>();
  const open = fs.openSync, read = fs.readSync;
  const reads: Array<[number, number]> = [];
  const opening = t.mock.method(fs, 'openSync', (path: fs.PathLike, flags: fs.OpenMode, mode?: fs.Mode) => {
    const fd = open(path, flags, mode);
    opened.set(fd, String(path));
    return fd;
  });
  const reading = t.mock.method(fs, 'readSync', ((fd: number, buffer: Buffer, offset: number, length: number, position: number) => {
    if (opened.get(fd) === usagePath) reads.push([position, length]);
    return read(fd, buffer, offset, length, position);
  }) as typeof fs.readSync);
  syncBuiltinESMExports();
  t.after(() => { opening.mock.restore(); reading.mock.restore(); syncBuiltinESMExports(); });
  const measure = () => { reads.length = 0; return take(); };

  const one = measure();
  assert.deepEqual(reads, [[0, Buffer.byteLength(initial)]]);
  assert.equal(one.spend_usd, first.cost_usd);
  assert.equal(one.spend_status, 'priced');
  assert.equal(one.last_call!.role, 'planner');
  assert.equal(one.roles.find(role => role.role === 'planner')!.model, planner.model);
  const held = readRecord(store, project.id);
  const same = measure();
  assert.deepEqual(reads, [[Buffer.byteLength(initial), 0]]);
  assert.deepEqual(same.usage_summary, one.usage_summary);
  assert.equal(readRecord(store, project.id), held);

  const appended = line(second);
  fs.appendFileSync(usagePath, appended);
  const two = measure();
  assert.deepEqual(reads, [[Buffer.byteLength(initial), Buffer.byteLength(appended)]]);
  assert.equal(two.usage_summary!.call_count, 2);
  assert.equal(two.spend_status, 'unpriced');
  assert.equal(two.spend_usd, first.cost_usd);
  // Usage can arrive before call.end: it counts toward spend immediately, while role metadata
  // still names only a call in this project's record. The later event uses the cached row.
  assert.deepEqual(two.last_call, one.last_call);
  fs.appendFileSync(eventsPath, line(engineer));
  const ended = measure();
  assert.deepEqual(reads, [[Buffer.byteLength(initial + appended), 0]]);
  assert.equal(ended.last_call!.role, 'engineer');
  assert.equal(ended.roles.find(role => role.role === 'engineer')!.model, engineer.model);

  const imported = line({ ...first, callId: 'external', role: 'manager', model: '外部调用', cost_usd: 0.125 });
  const partial = Buffer.from(imported);
  const cut = partial.indexOf(Buffer.from('外')) + 1; // Even a UTF-8 character may span writes.
  fs.appendFileSync(usagePath, partial.subarray(0, cut));
  assert.deepEqual(measure().usage_summary, two.usage_summary);
  const offset = Buffer.byteLength(initial + appended);
  assert.deepEqual(reads, [[offset, cut]]);
  assert.deepEqual(measure().usage_summary, two.usage_summary);
  assert.deepEqual(reads, [[offset, cut]]);
  fs.appendFileSync(usagePath, partial.subarray(cut));
  fs.appendFileSync(usagePath, '{"cut":\n' + line({ ...first, callId: 'free', cost_usd: 0 }));
  const complete = measure();
  assert.equal(complete.usage_summary!.call_count, 4);
  assert.equal(complete.usage_summary!.priced_calls, 3);
  assert.equal(complete.usage_summary!.unpriced_calls, 1);
  assert.equal(complete.usage_summary!.input_tokens, first.tokens!.input_tokens * 3 + second.tokens!.input_tokens);
  assert.equal(complete.spend_usd, first.cost_usd! + 0.125);
  assert.deepEqual(complete.last_call, ended.last_call);
  assert.equal(complete.roles.find(role => role.role === 'manager')!.model, '');
  measure();
  assert.deepEqual(reads, [[fs.statSync(usagePath).size, 0]]);
});

test('unpriced-only usage keeps the unknown cost and zero-cost calls remain priced', t => {
  const home = fs.mkdtempSync(join(tmpdir(), 'argus-unpriced-reading-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  const path = join(store.dir(project.id), 'usage.jsonl');
  fs.writeFileSync(path, '{"cost_usd":null}\n');
  const take = () => snapshot(store, project, { since: null, calls: [], requests: [] });
  assert.deepEqual([take().spend_status, take().spend_usd], ['unpriced', null]);
  fs.appendFileSync(path, '{"cost_usd":0}\n');
  const result = take();
  assert.deepEqual([result.spend_status, result.spend_usd, result.usage_summary!.priced_calls], ['unpriced', null, 1]);
  assert.deepEqual([projectRow(store, project, { since: null, calls: [], requests: [] }).spend_status,
    projectRow(store, project, { since: null, calls: [], requests: [] }).spend_usd], ['unpriced', null]);
});

test('the project list shares its first record fold and usage with snapshots and ignores internal letters', t => {
  const home = fs.mkdtempSync(join(tmpdir(), 'argus-list-reading-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  // Real multi-task delivery record, also used by the browser replay tests.
  const events = fs.readFileSync(new URL('../../../frontend/web/src/test/fixtures/home/projects/p-c855623c/events.jsonl', import.meta.url), 'utf8');
  fs.writeFileSync(join(store.dir(project.id), 'events.jsonl'), events);
  const usageLines = '{"cost_usd":0.125}\n{"cost_usd":null}\n';
  fs.writeFileSync(join(store.dir(project.id), 'usage.jsonl'), usageLines);
  const reads = t.mock.method(fs, 'readSync');
  syncBuiltinESMExports();
  t.after(() => { reads.mock.restore(); syncBuiltinESMExports(); });
  const idle = { since: null, calls: [], requests: [] };
  const row = projectRow(store, project, idle);
  assert.equal(row.last_message, '计算结果：result.md');
  assert.equal(row.spend_usd, 0.125);
  assert.equal(row.spend_status, 'unpriced');
  assert.deepEqual(reads.mock.calls.map(call => Array.from(call.arguments)[4]), [0, 0]);
  assert.deepEqual(reads.mock.calls.map(call => Array.from(call.arguments)[3]), [Buffer.byteLength(events), Buffer.byteLength(usageLines)]);
  reads.mock.resetCalls();
  const snap = snapshot(store, project, idle);
  assert.deepEqual([snap.spend_usd, snap.spend_status], [row.spend_usd, row.spend_status]);
  assert.ok(reads.mock.calls.every(call => Array.from(call.arguments)[3] === 0));
  store.append(project.id, { type: 'handoff', role: 'manager', to: 'planner', letter: 'Internal next work' });
  store.append(project.id, { type: 'handoff', role: 'reviewer', to: 'operator', letter: 'A task question', task: 't1' });
  assert.equal(projectRow(store, project, idle).last_message, row.last_message);
  store.append(project.id, { type: 'handoff', role: 'manager', to: 'operator', letter: '\n# Excel 还是 CSV？\nThe full question stays in the conversation.' });
  assert.equal(projectRow(store, project, idle).last_message, 'Excel 还是 CSV？');
  assert.equal(readRecord(store, project.id).lastReply, 'Excel 还是 CSV？');
  project.status = 'failed';
  project.error = 'Model gpt-bogus-9 is not available.';
  assert.equal(projectRow(store, project, idle).last_message, project.error);
  project.status = 'stopped';
  project.error = null;
  assert.equal(projectRow(store, project, idle).last_message, 'Excel 还是 CSV？');
});

test('one shared summary groups every call by role, model and UTC day, and unions parallel intervals', t => {
  const home = fs.mkdtempSync(join(tmpdir(), 'argus-usage-groups-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  const path = join(store.dir(project.id), 'usage.jsonl');
  const at = Date.parse('2026-10-30T00:00:00Z') / 1000;
  const rows = [
    // [0, 60s], [30s, 90s], nested [40s, 50s], touching [90s, 100s], gap, [120s, 125s].
    { ts: at + 60, ms: 60_000, role: 'engineer', model: 'a', cost_usd: 0.1 },
    { ts: at + 90, ms: 60_000, role: 'reviewer', model: 'b', cost_usd: 0.2 },
    { ts: at + 50, ms: 10_000, role: 'manager', model: 'a', cost_usd: null },
    { ts: at + 100, ms: 10_000, role: 'engineer', model: 'b', cost_usd: 0 },
    { ts: at + 125, ms: 5_000, role: 'engineer', model: null, cost_usd: 0.3 },
    { ts: at - 1, role: 'planner', model: 'a', cost_usd: null }, // Old record: priced independently of duration.
    { ts: null, ms: 100, role: null, model: null, cost_usd: null },
    { ts: at, ms: -1, role: 'engineer', model: 'a', cost_usd: null },
  ].map(row => ({ ...row, tokens: { input_tokens: 100, output_tokens: 10 } }));
  fs.writeFileSync(path, rows.map(row => JSON.stringify(row) + '\n').join(''));
  const live = { since: null, calls: [], requests: [] };
  const snap = snapshot(store, project, live);
  const summary = snap.usage_summary!;
  assert.equal(summary.work_duration_ms, 105_000);
  assert.equal(summary.calls_without_duration, 3);
  assert.equal(summary.call_count, 8);
  assert.ok(Math.abs(summary.known_cost_usd - 0.6) < 1e-12);
  assert.equal(summary.pricing_status, 'unpriced');
  for (const dimension of [summary.by_role, summary.by_model, summary.by_date]) {
    for (const key of ['call_count', 'known_cost_usd', 'input_tokens', 'output_tokens', 'priced_calls', 'unpriced_calls'] as const) {
      assert.ok(Math.abs(dimension.reduce((sum, group) => sum + group[key], 0) - summary[key]) < 1e-12, key);
    }
  }
  assert.deepEqual(summary.by_role.map(group => [group.key, group.call_count]), [[null, 1], ['engineer', 4], ['manager', 1], ['planner', 1], ['reviewer', 1]]);
  assert.deepEqual(summary.by_model.map(group => [group.key, group.call_count]), [[null, 2], ['a', 4], ['b', 2]]);
  assert.deepEqual(summary.by_date.map(group => [group.key, group.call_count]), [[null, 1], ['2026-10-29', 1], ['2026-10-30', 6]]);
  assert.equal(summary.by_role.find(group => group.key === 'manager')!.cost_usd, null);
  assert.equal(summary.by_role.find(group => group.key === 'manager')!.pricing_status, 'unpriced');
  const listed = projectRow(store, project, live);
  assert.deepEqual([listed.spend_usd, listed.spend_status], [summary.cost_usd, summary.pricing_status]);
  assert.equal(snapshot(store, project, live).usage_summary, summary, 'unchanged lines reuse the same summary object');
  fs.appendFileSync(path, JSON.stringify({ ts: at + 70, ms: 70_000, role: 'engineer', cost_usd: null }) + '\n');
  assert.equal(snapshot(store, project, live).usage_summary!.work_duration_ms, 105_000, 'out of order containing intervals count once');
  assert.equal(summary.call_count, 8, 'a previous response stays immutable');
});
