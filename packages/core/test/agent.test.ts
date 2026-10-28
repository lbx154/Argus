import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';
import test, { after } from 'node:test';
import { fileURLToPath } from 'node:url';
import { schemaErrors } from '@argus/contracts';
import { quoteTokenUsage } from '@argus/runtime';
import { handOffTool } from '../src/agent/handoff.js';
import { callAgent, type AgentRequest, type Backend } from '../src/index.js';

// The fake CLIs stand in for codex and claude on PATH; HOME and ARGUS_HOME are the test's own.
const root = mkdtempSync(join(tmpdir(), 'argus-agent-'));
const bin = join(root, 'bin');
const work = join(root, 'work');
for (const dir of [bin, work, join(root, 'home')]) mkdirSync(dir);
const fake = fileURLToPath(new URL('./fixtures/fake-agent.mjs', import.meta.url));
for (const name of ['codex', 'claude']) symlinkSync(fake, join(bin, name));
Object.assign(process.env, {
  HOME: join(root, 'home'), ARGUS_HOME: join(root, 'argus'), PATH: `${bin}${delimiter}${process.env.PATH}`,
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
const reply = (text: string) => JSON.parse(text) as { answers: Array<{ isError: boolean; content: Array<{ text: string }> }>; to: string[]; env: string[] };
const handoff = { to: 'reviewer', summary: 'The parser reads both formats; its tests pass.', note: 'Run npm test.', assumptions: [] };

test('the hand_off schema holds each form to its limits', () => {
  const choice = { chose: 'one reading', instead_of: 'another' };
  assert.deepEqual(schemaErrors({ ...handoff, summary: 'two\nlines', assumptions: Array(6).fill(choice), extra: 1 }, handOffTool('engineer').inputSchema), [
    'summary must match ^[^\\n]*$', 'assumptions must have 5 or fewer items', 'extra is not allowed',
  ]);
  const task = { title: 't', objective: 'o', impact_score: 6, key: 'a b' };
  assert.deepEqual(schemaErrors({ to: 'engineer', summary: 's', assumptions: [], tasks: [task] }, handOffTool('plan').inputSchema), [
    'tasks[0].impact_score must be <= 5', 'tasks[0].key must match ^[A-Za-z0-9_.:-]+$',
  ]);
  assert.deepEqual(schemaErrors({ to: 'team', summary: 's', acceptance: [] }, handOffTool('manager').inputSchema), [
    'assumptions is required', 'acceptance must have 1 or more items',
  ]);
});

for (const backend of ['codex', 'claude'] as const) {
  test(`${backend}: the accepted hand-off, the reply, usage and the raw output are recorded`, async () => {
    const result = await call(backend, { calls: [handoff] });
    assert.equal(result.completed, true);
    assert.equal(result.error, null);
    assert.ok(result.threadId);
    const { to, summary, ...fields } = handoff;
    assert.deepEqual(result.handoff, { form: 'engineer', to, summary, fields });
    const said = reply(result.text);
    assert.equal(said.answers[0]!.isError, false);
    assert.deepEqual(said.to, ['reviewer', 'done', 'engineer', 'planner', 'manager', 'operator']);
    // Credentials stay out of the CLI's environment, except the one it signs in with.
    assert.deepEqual(said.env, backend === 'claude' ? ['ANTHROPIC_API_KEY'] : []);
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
    assert.equal(existsSync(join(root, 'argus', 'calls', `${result.callId}.mcp.json`)), false);
  });

  test(`${backend}: a refused hand-off is corrected in the same turn`, async () => {
    const { assumptions: _, ...incomplete } = handoff;
    const result = await call(backend, { calls: [incomplete, { ...handoff, summary: 'Done.' }, handoff] }, {
      check: given => given.summary.includes('tests') ? null : 'Say what the tests showed.',
    });
    const [missing, checked, accepted] = reply(result.text).answers;
    assert.equal(missing!.isError, true);
    assert.match(missing!.content[0]!.text, /assumptions is required/);
    assert.deepEqual([checked!.isError, checked!.content[0]!.text], [true, 'Say what the tests showed.']);
    assert.equal(accepted!.isError, false);
    assert.equal(result.handoff?.summary, handoff.summary);
  });

  test(`${backend}: a refused call after an accepted one leaves no hand-off`, async () => {
    const result = await call(backend, { calls: [handoff, { ...handoff, to: 'operator' }] });
    assert.match(reply(result.text).answers[1]!.content[0]!.text, /to=operator goes with ask/);
    assert.equal(result.handoff, null);
  });

  test(`${backend}: a turn without a hand-off completes with none`, async () => {
    const result = await call(backend, { calls: [] });
    assert.equal(result.completed, true);
    assert.equal(result.handoff, null);
  });

  test(`${backend}: a failed turn is reported in the backend's words`, async () => {
    const result = await call(backend, { calls: [], fail: 'The model is overloaded.' });
    assert.equal(result.completed, false);
    assert.equal(result.error, 'The model is overloaded.');
  });

  test(`${backend}: resume continues the session and counts only its own usage`, async () => {
    const first = await call(backend, { calls: [] });
    const second = await call(backend, { calls: [handoff] }, { resume: first.threadId! });
    assert.equal(second.threadId, first.threadId);
    assert.equal(second.handoff?.to, 'reviewer');
    assert.deepEqual(second.usage, first.usage);
  });

  test(`${backend}: cancelling ends the CLI and everything it started`, async () => {
    const marker = `argus-agent-test-${process.pid}-${backend}`;
    const running = (): boolean => {
      try { execFileSync('pgrep', ['-f', marker]); return true; } catch { return false; }
    };
    const controller = new AbortController();
    const pending = call(backend, { hang: marker }, { signal: controller.signal });
    for (let waited = 0; !running(); waited += 50) {
      assert.ok(waited < 10_000, 'the fake CLI never started its job');
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    controller.abort();
    const result = await pending;
    assert.equal(result.completed, false);
    assert.match(result.error ?? '', /cancelled/);
    assert.equal(running(), false);
  });
}
