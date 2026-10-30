import assert from 'node:assert/strict';
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { Store } from '../src/store.js';
import { readRecord, snapshot, type Recorded } from '../src/serve/views.js';

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
});
