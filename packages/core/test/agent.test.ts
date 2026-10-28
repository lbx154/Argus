import assert from 'node:assert/strict';
import { once } from 'node:events';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync } from 'node:fs';
import { createServer, type AddressInfo, type Socket } from 'node:net';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';
import test, { after } from 'node:test';
import { fileURLToPath } from 'node:url';
import { schemaErrors } from '@argus/contracts';
import { quoteTokenUsage } from '@argus/runtime';
import { formSchema } from '../src/agent/handoff.js';
import { callAgent, type AgentRequest, type Backend } from '../src/index.js';

// The fake CLIs stand in for codex and claude on PATH, installed as npm installs a node CLI: a
// link on POSIX, on Windows the .cmd shim npm writes beside it. HOME and ARGUS_HOME are the test's own.
const root = mkdtempSync(join(tmpdir(), 'argus-agent-'));
const bin = join(root, 'bin');
const work = join(root, 'work');
const home = join(root, 'home');
for (const dir of [bin, work, home]) mkdirSync(dir);
const fixture = (name: string): string => fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url));
copyFileSync(fixture('fake-agent.mjs'), join(bin, 'fake-agent.mjs'));
for (const name of ['codex', 'claude']) {
  if (process.platform === 'win32') copyFileSync(fixture('fake-agent.cmd'), join(bin, `${name}.cmd`));
  else symlinkSync(join(bin, 'fake-agent.mjs'), join(bin, name));
}
Object.assign(process.env, {
  HOME: home, USERPROFILE: home, ARGUS_HOME: join(root, 'argus'), PATH: `${bin}${delimiter}${process.env.PATH}`,
  GH_TOKEN: 'push', MY_SECRET: 'secret', ANTHROPIC_API_KEY: 'login',
});
delete process.env.CODEX_HOME;
delete process.env.CLAUDE_CONFIG_DIR;
after(() => rmSync(root, { recursive: true, force: true }));

const usageFile = join(root, 'argus', 'usage.jsonl');
const call = (backend: Backend, scenario: object, more: Partial<AgentRequest> = {}) => callAgent({
  backend, role: 'engineer', form: 'engineer', prompt: JSON.stringify(scenario), cwd: work, access: 'write',
  callsDir: join(root, 'argus', 'calls'), usageFile, ...more,
});
const seen = (backend: Backend) => JSON.parse(readFileSync(join(work, `seen-${backend}.json`), 'utf8')) as {
  schema: { required: string[]; properties: Record<string, { type?: unknown; enum?: unknown[] }> }; env: string[];
};
const handoff = { to: 'reviewer', summary: 'The parser reads both formats; its tests pass.', note: 'Run npm test.', assumptions: [] };

test('each form holds the hand-off to its limits', () => {
  const choice = { chose: 'one reading', instead_of: 'another' };
  assert.deepEqual(schemaErrors({ ...handoff, summary: 'two\nlines', assumptions: Array(6).fill(choice), extra: 1 }, formSchema('engineer')), [
    'summary must match ^[^\\n]*$', 'assumptions must have 5 or fewer items', 'extra is not allowed',
  ]);
  const task = { title: 't', objective: 'o', key: 'a b', impact_score: 3 };
  assert.deepEqual(schemaErrors({ to: 'engineer', summary: 's', assumptions: [], tasks: [task] }, formSchema('plan')), [
    'tasks[0].key must match ^[A-Za-z0-9_.:-]+$', 'tasks[0].impact_score is not allowed',
  ]);
  assert.deepEqual(schemaErrors({ to: 'team', summary: 's', acceptance: [] }, formSchema('manager')), [
    'assumptions is required', 'reply is required', 'acceptance must have 1 or more items',
  ]);
});

for (const backend of ['codex', 'claude'] as const) {
  test(`${backend}: the form the turn ends with, usage and the raw output are recorded`, async () => {
    const result = await call(backend, { output: handoff });
    assert.equal(result.completed, true);
    assert.equal(result.error, null);
    assert.ok(result.threadId);
    const { to, summary, ...fields } = handoff;
    assert.deepEqual(result.handoff, { form: 'engineer', to, summary, fields });
    assert.deepEqual(seen(backend).schema.properties.to!.enum, ['reviewer', 'done', 'engineer', 'planner', 'manager', 'operator']);
    // Credentials stay out of the CLI's environment, except the one it signs in with.
    assert.deepEqual(seen(backend).env, backend === 'claude' ? ['ANTHROPIC_API_KEY'] : []);
    if (backend === 'codex') {
      assert.equal(result.model, 'gpt-5.5');
      const cost = quoteTokenUsage('gpt-5.5', { input_tokens: 1000, cached_input_tokens: 400, cache_write_tokens: 100, output_tokens: 50 }).cost_usd;
      assert.deepEqual(result.usage, { input_tokens: 1000, cached_input_tokens: 400, output_tokens: 50, reasoning_output_tokens: 20, cost_usd: cost });
    } else {
      assert.equal(result.model, 'claude-test');
      assert.deepEqual(result.usage, { input_tokens: 360, cached_input_tokens: 300, output_tokens: 40, reasoning_output_tokens: 5, cost_usd: 0.0123 });
    }
    const line = readFileSync(usageFile, 'utf8').trim().split('\n').map(row => JSON.parse(row)).find(row => row.callId === result.callId);
    const { cost_usd, ...tokens } = result.usage!;
    assert.deepEqual({ ...line, ts: 0 }, { ts: 0, callId: result.callId, role: 'engineer', backend, model: result.model, tokens, cost_usd });
    assert.match(readFileSync(join(root, 'argus', 'calls', `${result.callId}.jsonl`), 'utf8'), /"type":"(turn\.completed|result)"/);
  });

  test(`${backend}: a refused form completes the turn with no hand-off and says why`, async () => {
    const { assumptions: _, ...incomplete } = handoff;
    const missing = await call(backend, { output: incomplete });
    assert.deepEqual([missing.completed, missing.handoff, missing.error], [true, null, 'assumptions is required']);
    const checked = await call(backend, { output: handoff }, { check: given => given.to === 'reviewer' ? 'Not yet.' : null });
    assert.deepEqual([checked.handoff, checked.error], [null, 'Not yet.']);
    const asking = await call(backend, { output: { ...handoff, to: 'operator' } });
    assert.match(asking.error ?? '', /to=operator goes with ask/);
  });

  test(`${backend}: a turn that ends in prose has no hand-off`, async () => {
    const result = await call(backend, { output: 'Handed off to the reviewer.' });
    assert.deepEqual([result.completed, result.handoff, result.error], [true, null, 'The turn ended without the hand-off form.']);
  });

  test(`${backend}: a failed turn is reported in the backend's words`, async () => {
    const result = await call(backend, { fail: 'The model is overloaded.' });
    assert.equal(result.completed, false);
    assert.equal(result.error, 'The model is overloaded.');
  });

  test(`${backend}: resume continues the session and counts only its own usage`, async () => {
    const first = await call(backend, { output: 'Not done yet.' });
    const second = await call(backend, { output: handoff }, { resume: first.threadId! });
    assert.equal(second.threadId, first.threadId);
    assert.equal(second.handoff?.to, 'reviewer');
    assert.deepEqual(second.usage, first.usage);
  });

  test(`${backend}: cancelling ends the CLI and everything it started`, { timeout: 30_000 }, async () => {
    // The job the CLI starts holds a connection here, which closes when the job ends.
    const server = createServer().listen(0, '127.0.0.1').unref();
    await once(server, 'listening');
    const controller = new AbortController();
    const pending = call(backend, { hang: (server.address() as AddressInfo).port }, { signal: controller.signal });
    const [job] = await once(server, 'connection') as [Socket];
    const ended = new Promise(resolve => job.unref().on('error', () => {}).on('close', resolve).resume());
    controller.abort();
    const result = await pending;
    assert.equal(result.completed, false);
    assert.match(result.error ?? '', /cancelled/);
    await ended;
  });
}

test('codex: every field is required, an optional one nullable, and a null reads as left out', async () => {
  const result = await call('codex', { output: { ...handoff, ask: null } });
  assert.deepEqual(result.handoff?.fields, { note: handoff.note, assumptions: [] });
  const { schema } = seen('codex');
  assert.deepEqual(schema.required, Object.keys(schema.properties));
  assert.deepEqual(schema.properties.ask!.type, ['object', 'null']);
  assert.equal(schema.properties.summary!.type, 'string');
});
