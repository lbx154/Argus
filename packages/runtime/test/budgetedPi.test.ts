import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import type { BudgetedResult } from '@argus/contracts';
import { BudgetedPiBackend, type BudgetedPiRequest } from '../src/index.js';

const owner = fileURLToPath(new URL('./fixtures/budget-owner.mjs', import.meta.url));
const pi = fileURLToPath(new URL('./fixtures/fake-pi.mjs', import.meta.url));
const request: BudgetedPiRequest = { projectId: 'p', cwd: process.cwd(), prompt: 'private prompt never sent to budget owner',
  model: 'gpt-5.6-sol', provider: 'openai', toolPolicy: 'disabled', wallTimeoutMs: 5000, idleTimeoutMs: 2000 };
async function setup(t: test.TestContext, mode = 'success', piMode = 'accounting-tokens') {
  const dir = await mkdtemp(join(tmpdir(), 'argus-budget-test-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const trace = join(dir, 'trace.jsonl');
  const backend = new BudgetedPiBackend({ python: { executable: process.execPath, prefixArgs: [owner, mode, trace],
    sourceRoot: process.cwd(), globalRoot: process.cwd(), timeoutMs: mode === 'hang' ? 300 : 5000 },
    pi: { executable: process.execPath, prefixArgs: [pi, piMode], terminateGraceMs: 100 } });
  return { backend, trace, async calls(): Promise<Array<{ method: string; params: Record<string, unknown> }>> {
    return (await readFile(trace, 'utf8')).trim().split('\n').map(line => JSON.parse(line));
  } };
}
async function collect(backend: BudgetedPiBackend, input = request): Promise<BudgetedResult> {
  let result: BudgetedResult | undefined;
  for await (const event of backend.run(input)) if (event.type === 'result') result = event.result;
  assert.ok(result); return result;
}

test('budgeted Pi reserves, marks start, observes and settles without sharing the prompt', async t => {
  const { backend, calls, trace } = await setup(t);
  const result = await collect(backend);
  assert.equal(result.settlement, 'settled');
  assert.equal(result.runner?.accounting.pricing.cost_usd, 1.506);
  const operations = await calls();
  assert.deepEqual(operations.slice(0, 3).map(row => row.method), ['reserve', 'start', 'observe']);
  assert.equal(operations.at(-1)?.method, 'settle');
  assert.equal(operations.at(-1)?.params.completed, true);
  assert.ok(!(await readFile(trace, 'utf8')).includes(request.prompt));
});

test('denied budget never starts the provider', async t => {
  const { backend, calls } = await setup(t, 'denied');
  const result = await collect(backend);
  assert.equal(result.admitted, false);
  assert.equal(result.runner, null);
  assert.deepEqual((await calls()).map(row => row.method), ['reserve']);
});

test('live budget exhaustion cancels Pi and still settles the observed spending', async t => {
  const { backend, calls } = await setup(t, 'cap', 'accounting-cancel');
  const result = await collect(backend);
  assert.equal(result.settlement, 'unresolved');
  assert.equal(result.runner?.turnCompleted, false);
  assert.match(result.reason, /budget exhausted/);
  assert.equal(result.runner?.accounting.pricing.cost_usd, 0.753);
  assert.equal((await calls()).at(-1)?.method, 'settle');
});

for (const mode of ['wrong-version', 'owner-dies', 'hang']) {
  test(`budget failure ${mode} cannot produce a successful run`, async t => {
    const { backend } = await setup(t, mode);
    const result = await collect(backend);
    assert.equal(result.settlement, 'failed');
    assert.equal(result.runner, null);
    assert.ok(result.reason);
  });
}

test('closing the output iterator cancels the provider and finalizes accounting', async t => {
  const { backend, calls } = await setup(t, 'success', 'accounting-cancel');
  for await (const event of backend.run(request)) {
    if (event.type === 'provider_event' && event.event.type === 'ready') break;
  }
  const final = (await calls()).at(-1)!;
  assert.equal(final.method, 'settle');
  assert.equal(final.params.completed, false);
});

test('pre-cancellation avoids both budget owner and provider startup', async t => {
  const { backend, trace } = await setup(t);
  const result = await collect(backend, { ...request, signal: AbortSignal.abort() });
  assert.equal(result.admitted, false);
  assert.equal(result.settlement, 'not_started');
  await assert.rejects(readFile(trace), { code: 'ENOENT' });
});

test('losing the budget owner after provider usage stops the live invocation', async t => {
  const { backend } = await setup(t, 'owner-dies-after-usage', 'accounting-cancel');
  const result = await collect(backend);
  assert.equal(result.settlement, 'failed');
  assert.equal(result.runner?.turnCompleted, false);
  assert.equal(result.runner?.accounting.pricing.cost_usd, 0.753);
});

test('a paused output consumer does not pause live budget enforcement', async t => {
  const { backend, calls } = await setup(t, 'cap', 'accounting-cancel');
  const stream = backend.run(request);
  const first = await stream.next();
  assert.equal(first.done, false);
  // Wait for the durable protocol's terminal operation without pulling output.
  const deadline = Date.now() + 5000;
  while ((await calls()).at(-1)?.method !== 'settle') {
    assert.ok(Date.now() < deadline, 'settlement stalled behind output consumption');
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  let result: BudgetedResult | undefined;
  for await (const event of stream) if (event.type === 'result') result = event.result;
  assert.equal(result?.settlement, 'unresolved');
  assert.match(result!.reason, /budget exhausted/);
});

test('invalid native schema does not open a budget session', async t => {
  const { backend, trace } = await setup(t);
  await assert.rejects(collect(backend, { ...request, toolPolicy: 'read-only', outputSchema: { type: 'object' } }), /toolPolicy disabled/);
  await assert.rejects(readFile(trace), { code: 'ENOENT' });
});

test('schema bindings are snapshotted before asynchronous admission', async t => {
  const { backend, trace } = await setup(t, 'delayed-reserve', 'structured-completions');
  const outputSchema = { type: 'object', description: 'original schema' };
  const stream = backend.run({ ...request, outputSchema });
  const first = stream.next();
  const deadline = Date.now() + 5000;
  while (true) {
    try { if ((await readFile(trace, 'utf8')).includes('reserve')) break; }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    assert.ok(Date.now() < deadline);
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  outputSchema.description = 'mutated during admission';
  await first;
  let observed: unknown;
  let result: BudgetedResult | undefined;
  for await (const event of stream) {
    if (event.type === 'provider_event' && event.event.type === 'request_payload') observed = event.event.payload;
    if (event.type === 'result') result = event.result;
  }
  assert.equal(((observed as { response_format: { json_schema: { schema: { description: string } } } }).response_format.json_schema.schema.description), 'original schema');
  assert.equal(result?.settlement, 'settled');
  assert.ok(!(await readFile(trace, 'utf8')).includes('original schema'));
});

test('provider-turn allowance still settles partial usage through the budget owner', async t => {
  const { backend, calls } = await setup(t, 'success', 'turn-cap-paced');
  const result = await collect(backend, { ...request, providerTurnCap: 2 });
  assert.equal(result.settlement, 'unresolved');
  assert.equal(result.runner?.providerTurnCapHit, true);
  assert.ok(result.runner!.accounting.pricing.cost_usd! >= 0.2);
  assert.equal((await calls()).at(-1)?.params.completed, false);
});
