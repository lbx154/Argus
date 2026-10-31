import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { appendFileSync, existsSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
const script = fileURLToPath(new URL('../skills/rl/ledger.mjs', import.meta.url));
const base = { parent: '', model: 'small', benchmark: 'math pass@1', metric: 'accuracy', value: 10, baseline: 10,
  change: 'framework v1 CPU', gpu_h: 0, verdict: 'baseline', path: 'result.txt' };
function setup(t: { after: (fn: () => void) => void }) {
  const cwd = mkdtempSync('/fastdata/v-boxiuli/argus-work/scratch/ledger-test-');
  t.after(() => rmSync(cwd, { recursive: true, force: true }));
  writeFileSync(join(cwd, 'result.txt'), 'actual result');
  const file = join(cwd, 'runs', 'p-one.tsv');
  const run = (command: string, argument?: unknown, ledger = file, model?: string) => spawnSync(process.execPath,
    [script, ledger, command, ...(argument === undefined ? [] : [typeof argument === 'string' ? argument : JSON.stringify(argument)]), ...(model ? [model] : [])], { cwd, encoding: 'utf8' });
  return { cwd, file, run };
}
test('ledger add owns columns, numbering, UTC dates, normalization and local/cross-project parents', t => {
  const { file, run, cwd } = setup(t);
  assert.equal(run('add', { ...base, change: 'framework\tversion\r\nCPU short abc1234' }).status, 0);
  const first = readFileSync(file, 'utf8').split('\n');
  assert.equal(first[0], 'run\tdate\tparent\tmodel\tbenchmark\tmetric\tvalue\tbaseline\tchange\tgpu_h\tverdict\tpath');
  assert.match(first[1]!, /^r1\t\d{4}-\d{2}-\d{2}\t/);
  assert.equal(first[1]!.split('\t')[8], 'framework version  CPU short abc1234');
  assert.equal(first[1]!.split('\t')[1], new Date().toISOString().slice(0, 10));
  assert.equal(run('add', { ...base, verdict: 'better', parent: 'r1', value: 11 }).status, 0);
  const other = join(cwd, 'runs', 'p-two.tsv');
  assert.equal(run('add', { ...base, verdict: 'same', parent: 'p-one:r2' }, other).status, 0);
  assert.match(readFileSync(other, 'utf8'), /p-one:r2/);
});
test('ledger refuses every missing/unknown key, invalid numbers, verdicts, parents, paths and long hex value', t => {
  const { run, file, cwd } = setup(t);
  assert.equal(run('add', base).status, 0);
  const original = readFileSync(file, 'utf8');
  const reject = (row: unknown) => { const result = run('add', row); assert.equal(result.status, 1, result.stderr); assert.equal(readFileSync(file, 'utf8'), original); };
  for (const key of Object.keys(base)) { const row: Record<string, unknown> = { ...base }; delete row[key]; reject(row); }
  for (const key of ['unknown', 'run', 'date']) reject({ ...base, [key]: 'bad' });
  for (const key of ['value', 'baseline', 'gpu_h']) for (const value of ['1', null, true, {}, []]) reject({ ...base, [key]: value });
  for (const verdict of ['running', '', 'BETTER']) reject({ ...base, verdict });
  for (const parent of ['', 'r0', 'r2', 'p-missing:r1', '../p-one:r1', 'p-one:r9']) reject({ ...base, parent, verdict: 'better' });
  for (const path of ['', 'missing', join(cwd, 'result.txt'), '../missing', 'dir\\result.txt']) reject({ ...base, path });
  symlinkSync(script, join(cwd, 'outside')); reject({ ...base, path: 'outside' });
  for (const key of Object.keys(base)) reject({ ...base, [key]: 'a'.repeat(32) });
  reject(null); reject([]); reject('invalid JSON');
  assert.equal(run('add', { ...base, change: 'abcdef123456' }).status, 0);
  const missing = run('add', { ...base, path: 'missing' }, join(cwd, 'absent', 'p.tsv'));
  assert.equal(missing.status, 1); assert.equal(existsSync(join(cwd, 'absent')), false);
});
test('show keeps 20 recent rows plus last better per benchmark and reports all bad line numbers', t => {
  const { run, file } = setup(t);
  assert.equal(run('add', base).status, 0);
  for (let i = 2; i <= 25; i++) assert.equal(run('add', { ...base, parent: 'r1', verdict: i <= 3 ? 'better' : 'same' }).status, 0);
  const shown = run('show'); assert.equal(shown.status, 0);
  const lines = shown.stdout.trim().split('\n'); assert.equal(lines.length, 22);
  assert.ok(lines.some(line => line.startsWith('r3\t'))); assert.ok(!lines.some(line => line.startsWith('r2\t')));
  appendFileSync(file, 'bad\trow\n' + readFileSync(file, 'utf8').split('\n')[1] + '\n');
  const bad = run('show'); assert.equal(bad.status, 1); assert.match(bad.stderr, /line 27: wrong column count/); assert.match(bad.stderr, /line 28: duplicate run/);
  assert.equal(run('add', base).status, 1);
});
test('near excludes current project, filters benchmark/model and selects baseline, final better and all broken, capped at 30', t => {
  const { run, cwd } = setup(t);
  assert.equal(run('add', base).status, 0);
  const other = join(cwd, 'runs', 'p-two.tsv');
  assert.equal(run('add', base, other).status, 0);
  for (const verdict of ['better', 'better', 'worse', 'same', 'broken', 'broken']) assert.equal(run('add', { ...base, parent: 'r1', verdict }, other).status, 0);
  assert.equal(run('add', { ...base, model: 'large' }, other).status, 0);
  const near = run('near', base.benchmark); assert.equal(near.status, 0);
  assert.deepEqual(near.stdout.trim().split('\n').slice(1).map(line => line.split('\t')[0]), ['p-two:r1', 'p-two:r3', 'p-two:r6', 'p-two:r7', 'p-two:r8']);
  assert.equal(run('near', base.benchmark, undefined, 'large').stdout.trim().split('\n').length, 2);
  assert.equal(run('near', 'different protocol').stdout.trim().split('\n').length, 1);
  for (let i = 0; i < 35; i++) assert.equal(run('add', { ...base, parent: 'r1', verdict: 'broken' }, other).status, 0);
  assert.equal(run('near', base.benchmark).stdout.trim().split('\n').length, 31);
  appendFileSync(other, 'bad\n'); assert.equal(run('near', base.benchmark).status, 1);
});
