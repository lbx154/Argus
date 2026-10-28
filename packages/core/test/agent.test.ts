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
import { confine } from '../src/agent/sandbox.js';
import { BACKENDS, callAgent, type Access, type AgentRequest, type AgentResult, type Backend } from '../src/index.js';

// The fake CLIs stand in for every backend on PATH, installed as npm installs a node CLI: a
// link on POSIX, on Windows the .cmd shim npm writes beside it. HOME and ARGUS_HOME are the test's own.
const root = mkdtempSync(join(tmpdir(), 'argus-agent-'));
const bin = join(root, 'bin');
const work = join(root, 'work');
const home = join(root, 'home');
for (const dir of [bin, work, home]) mkdirSync(dir);
const fixture = (name: string): string => fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url));
copyFileSync(fixture('fake-agent.mjs'), join(bin, 'fake-agent.mjs'));
for (const name of BACKENDS) {
  if (process.platform === 'win32') copyFileSync(fixture('fake-agent.cmd'), join(bin, `${name}.cmd`));
  else symlinkSync(join(bin, 'fake-agent.mjs'), join(bin, name));
}
Object.assign(process.env, {
  HOME: home, USERPROFILE: home, ARGUS_HOME: join(root, 'argus'), PATH: `${bin}${delimiter}${process.env.PATH}`,
  GH_TOKEN: 'push', MY_SECRET: 'secret', ANTHROPIC_API_KEY: 'login', COPILOT_GITHUB_TOKEN: 'login',
});
for (const name of ['CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'COPILOT_HOME', 'PI_CODING_AGENT_DIR']) delete process.env[name];
after(() => rmSync(root, { recursive: true, force: true }));

const usageFile = join(root, 'argus', 'usage.jsonl');
const call = (backend: Backend, scenario: object, more: Partial<AgentRequest> = {}) => callAgent({
  backend, role: 'engineer', form: 'engineer', prompt: JSON.stringify(scenario), cwd: work, access: 'write',
  callsDir: join(root, 'argus', 'calls'), usageFile, ...more,
});
const seen = (backend: Backend) => JSON.parse(readFileSync(join(work, `seen-${backend}.json`), 'utf8')) as {
  schema: { required: string[]; properties: Record<string, { type?: unknown; enum?: unknown[] }> }; env: string[]; ends?: boolean;
};
const handoff = { to: 'reviewer', letter: 'The parser reads both formats; its tests pass.\nRun npm test.' };

test('a hand-off is a letter to one of the places its role can write to', () => {
  assert.deepEqual(schemaErrors({ to: 'done', letter: '', extra: 1 }, formSchema('engineer')), [
    'to must be one of ["reviewer","engineer","planner","manager","operator"]', 'letter must have length >= 1', 'extra is not allowed',
  ]);
  assert.deepEqual(schemaErrors({ to: 'planner' }, formSchema('manager')), ['letter is required']);
  // The Planner's letters to the Engineer open a task each.
  assert.deepEqual(schemaErrors({ to: 'engineer', letters: [] }, formSchema('plan')), ['letters must have 1 or more items']);
  // Every field is required and none other allowed: the strict shape OpenAI's structured outputs take.
  for (const form of ['manager', 'plan', 'engineer', 'review'] as const) {
    const { properties, required, additionalProperties } = formSchema(form);
    assert.deepEqual([required, additionalProperties], [Object.keys(properties!), false]);
  }
});

test('under bubblewrap a turn writes the directories it is given to write only when its role writes', { skip: process.platform !== 'linux' && 'Linux only' }, () => {
  const skills = join(root, 'argus', 'skills');
  mkdirSync(skills, { recursive: true });
  const bound = (access: Access): string | undefined => {
    const argv = confine(['codex'], {
      backend: 'codex', role: 'engineer', form: 'engineer', prompt: '', cwd: work, access, writeDirs: [skills], callsDir: '', usageFile: '',
    });
    return argv[argv.lastIndexOf(skills) - 2];
  };
  assert.deepEqual([bound('write'), bound('read')], ['--bind-try', '--ro-bind-try']);
});

// What each fake CLI reports: its model, usage and the credentials it signs in with.
const codexCost = quoteTokenUsage('gpt-5.5', { input_tokens: 1000, cached_input_tokens: 400, cache_write_tokens: 100, output_tokens: 50 }).cost_usd;
const reported: Record<Backend, Pick<AgentResult, 'model' | 'usage'> & { env: string[] }> = {
  codex: { model: 'gpt-5.5', env: [],
    usage: { input_tokens: 1000, cached_input_tokens: 400, output_tokens: 50, reasoning_output_tokens: 20, cost_usd: codexCost } },
  claude: { model: 'claude-test', env: ['ANTHROPIC_API_KEY'],
    usage: { input_tokens: 360, cached_input_tokens: 300, output_tokens: 40, reasoning_output_tokens: 5, cost_usd: 0.0123 } },
  // Two model requests, a subagent's among them, each $0.025 of AI credits.
  copilot: { model: 'copilot-test', env: ['COPILOT_GITHUB_TOKEN'],
    usage: { input_tokens: 2400, cached_input_tokens: 1600, output_tokens: 60, reasoning_output_tokens: 20, cost_usd: 0.05 } },
  // Two assistant messages.
  pi: { model: 'anthropic/claude-test', env: ['ANTHROPIC_API_KEY'],
    usage: { input_tokens: 740, cached_input_tokens: 600, output_tokens: 80, reasoning_output_tokens: 10, cost_usd: 0.0084 } },
};

for (const backend of BACKENDS) {
  test(`${backend}: the form the turn ends with, usage and the raw output are recorded`, async () => {
    const result = await call(backend, { output: handoff });
    assert.equal(result.completed, true);
    assert.equal(result.error, null);
    assert.ok(result.threadId);
    assert.deepEqual(result.handoff, { form: 'engineer', to: handoff.to, letters: [handoff.letter] });
    assert.deepEqual(seen(backend).schema.properties.to!.enum, ['reviewer', 'engineer', 'planner', 'manager', 'operator']);
    // Where the form is a tool, its call ends the turn.
    assert.equal(seen(backend).ends, backend === 'copilot' || backend === 'pi' ? true : undefined);
    // Credentials stay out of the CLI's environment, except the one it signs in with.
    const { env, ...report } = reported[backend];
    assert.deepEqual(seen(backend).env, env);
    assert.deepEqual({ model: result.model, usage: result.usage }, report);
    const line = readFileSync(usageFile, 'utf8').trim().split('\n').map(row => JSON.parse(row)).find(row => row.callId === result.callId);
    const { cost_usd, ...tokens } = result.usage!;
    assert.deepEqual({ ...line, ts: 0 }, { ts: 0, callId: result.callId, role: 'engineer', backend, model: result.model, tokens, cost_usd });
    assert.match(readFileSync(join(root, 'argus', 'calls', `${result.callId}.jsonl`), 'utf8'), /"type":"(turn\.completed|result|agent_settled)"/);
  });

  test(`${backend}: a refused form completes the turn with no hand-off and says why`, async () => {
    const missing = await call(backend, { output: { to: 'reviewer' } });
    assert.deepEqual([missing.completed, missing.handoff, missing.error], [true, null, 'letter is required']);
    const checked = await call(backend, { output: handoff }, { check: given => given.to === 'reviewer' ? 'Not yet.' : null });
    assert.deepEqual([checked.handoff, checked.error], [null, 'Not yet.']);
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
    job.unref().on('error', () => {}).resume();
    controller.abort();
    const result = await pending;
    assert.equal(result.completed, false);
    assert.match(result.error ?? '', /cancelled/);
    for (let waited = 0; !job.closed; waited += 50) {
      assert.ok(waited < 10_000, 'the job outlived the call');
      await new Promise(resolve => setTimeout(resolve, 50));
    }
  });
}
