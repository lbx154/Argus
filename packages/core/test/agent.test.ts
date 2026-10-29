import assert from 'node:assert/strict';
import { once } from 'node:events';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { createServer, type AddressInfo, type Socket } from 'node:net';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';
import test, { after } from 'node:test';
import { fileURLToPath } from 'node:url';
import { schemaErrors } from '@argus/contracts';
import { quoteTokenUsage } from '@argus/runtime';
import { formSchema, judge } from '../src/agent/handoff.js';
import { confine } from '../src/agent/sandbox.js';
import { BACKENDS, callAgent, type Access, type AgentRequest, type AgentResult, type Backend, type Step } from '../src/index.js';

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
  schema: { required: string[]; properties: Record<string, { type?: unknown; enum?: unknown[] }> }; env: string[]; ends?: boolean; instructions?: string; run: string; model?: string;
};
const handoff = { to: 'reviewer', letter: 'The parser reads both formats; its tests pass.\nRun npm test.', open: [] };

test('a hand-off is a letter to one of the places its role can write to', () => {
  assert.deepEqual(schemaErrors({ to: 'done', letter: '', open: [], extra: 1 }, formSchema('engineer')), [
    'to must be one of ["reviewer","engineer","planner","manager","operator"]', 'letter must have length >= 1', 'extra is not allowed',
  ]);
  assert.deepEqual(schemaErrors({ to: 'planner' }, formSchema('manager')), ['letter is required', 'open is required']);
  // A letter that hands over a result names its files; the Planner's letters hand over none.
  const report = { to: 'operator', letter: 'The report is done.', open: ['report/index.html', 'figures/a.png'] };
  assert.deepEqual(judge('manager', formSchema('manager'), report), { form: 'manager', to: 'operator', letters: ['The report is done.'], open: report.open });
  assert.deepEqual(judge('manager', formSchema('manager'), { ...report, open: [] }), { form: 'manager', to: 'operator', letters: ['The report is done.'] });
  assert.deepEqual(judge('review', formSchema('review', 'planner'), { ...report, to: 'planner' }),
    { form: 'review', to: 'planner', letters: ['The report is done.'], open: report.open });
  assert.deepEqual(schemaErrors({ to: 'engineer', letters: [{ letter: 'Parse it', after: [], task: '' }], open: [] }, formSchema('plan')), ['open is not allowed']);
  // A task's form offers its giver: the Planner, which never had a task the Manager gave, is not on its form.
  for (const form of ['engineer', 'review'] as const) {
    assert.deepEqual([formSchema(form, 'manager'), formSchema(form, 'planner')].map(({ properties }) => properties!.to!.enum!.includes('planner')), [false, true]);
  }
  // The Planner's letters to the Engineer open a task each, waiting for the tasks each names.
  assert.deepEqual(schemaErrors({ to: 'engineer', letters: [] }, formSchema('plan')), ['letters must have 1 or more items']);
  // A letter naming an open task is for whoever holds it.
  const plan = { to: 'engineer', letters: [{ letter: 'Train the baseline', after: [], task: '' }, { letter: 'Compare with it', after: ['t3'], task: '' },
    { letter: 'Use the newer model.', after: [], task: 't2' }] };
  assert.deepEqual(judge('plan', formSchema('plan'), plan), { form: 'plan', to: 'engineer', letters: ['Train the baseline', 'Compare with it', 'Use the newer model.'],
    tasks: [{ task: '', after: [] }, { task: '', after: ['t3'] }, { task: 't2', after: [] }] });
  assert.deepEqual(judge('plan', formSchema('plan'), { to: 'manager', letters: [{ letter: 'Done.', after: [], task: '' }] }), { form: 'plan', to: 'manager', letters: ['Done.'] });
  // A letter that opens a task names it in its first line, the title the progress map shows.
  for (const form of ['manager', 'plan'] as const) assert.match(String(formSchema(form).properties!.to!.description), /opens a task, [^;]*first line, a short title/);
  // Every field is required and none other allowed: the strict shape OpenAI's structured outputs take.
  const strict = ({ properties, required, additionalProperties, items }: ReturnType<typeof formSchema>): void => {
    if (properties) assert.deepEqual([required, additionalProperties], [Object.keys(properties), false]);
    for (const inner of [...Object.values(properties ?? {}), ...items ? [items] : []]) strict(inner);
  };
  for (const form of ['manager', 'plan', 'engineer', 'review'] as const) strict(formSchema(form));
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

test('under bubblewrap codex reads the files its config names, however the config quotes them', { skip: process.platform !== 'linux' && 'Linux only' }, t => {
  const codexHome = join(root, 'codex-home');
  mkdirSync(join(root, 'catalog'), { recursive: true });
  mkdirSync(codexHome, { recursive: true });
  for (const name of ['models.json', 'prompt.md']) writeFileSync(join(root, 'catalog', name), '{}');
  writeFileSync(join(codexHome, 'config.toml'),
    `model_catalog_json = '${join(root, 'catalog', 'models.json')}'\nmodel_instructions_file = "${join(root, 'catalog', 'prompt.md')}"\n`);
  process.env.CODEX_HOME = codexHome;
  t.after(() => { delete process.env.CODEX_HOME; });
  const argv = confine(['codex'], { backend: 'codex', role: 'engineer', form: 'engineer', prompt: '', cwd: work, access: 'write', callsDir: '', usageFile: '' });
  for (const name of ['models.json', 'prompt.md']) assert.ok(argv.includes(join(root, 'catalog', name)), name);
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
    assert.match(readFileSync(join(root, 'argus', 'calls', `${result.callId}.jsonl`), 'utf8'), /"(type|method)":"(turn\/completed|result|agent_settled)"/);
    assert.equal(readFileSync(join(root, 'argus', 'calls', `${result.callId}.prompt.md`), 'utf8'), JSON.stringify({ output: handoff }));
  });

  test(`${backend}: the role's steps are told as its turn goes on: what it says, runs and changes`, async () => {
    const steps: Step[] = [];
    await call(backend, { output: handoff }, { onStep: step => steps.push(step) });
    // Not what only reads, a subagent's work, or the form the turn ends with.
    assert.deepEqual(steps, [
      { kind: 'message', text: 'Working on it.' }, { kind: 'command', text: 'npm test' }, { kind: 'edit', text: 'parser.js' },
    ]);
  });

  test(`${backend}: the role's instructions reach it with every turn, or at the head of its session`, async () => {
    const told = 'You are the Argus Engineer.\n\n## Where things are\nWorkspace: here';
    const first = await call(backend, { output: handoff }, { instructions: told });
    assert.equal(seen(backend).instructions, told);
    await call(backend, { output: handoff }, { instructions: told, resume: first.threadId! });
    // A backend with a place for them is given them again, past any compaction; the others have them in the session.
    assert.equal(seen(backend).instructions, backend === 'codex' || backend === 'claude' ? told : undefined);
  });

  test(`${backend}: a refused form completes the turn with no hand-off and says why`, async () => {
    const missing = await call(backend, { output: { to: 'reviewer', open: [] } });
    assert.deepEqual([missing.completed, missing.handoff, missing.error], [true, null, 'letter is required']);
    const misaddressed = await call(backend, { output: { to: 'planner', letter: 'Accepted.', open: [] } }, { giver: 'manager' });
    assert.deepEqual([misaddressed.handoff, misaddressed.error], [null, 'to must be one of ["reviewer","engineer","manager","operator"]']);
    assert.deepEqual(seen(backend).schema.properties.to?.enum, ['reviewer', 'engineer', 'manager', 'operator']);
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

// What a job started with `nohup … &` and one started with `setsid` noted in their files.
const jobs = (): string[] => ['nohup', 'setsid'].map(how => {
  const file = join(work, `job-${how}.txt`);
  const noted = readFileSync(file, 'utf8');
  rmSync(file);
  return noted;
});
const unseen = process.platform === 'win32' && 'Windows shows a turn nothing its CLI started once the CLI has exited';

for (const backend of ['codex', 'claude'] as const) {
  test(`${backend}: a session's turns go to one CLI, what a turn leaves running runs on into the next, and the session's end ends it`, { skip: unseen, timeout: 30_000 }, async t => {
    const server = createServer().listen(0, '127.0.0.1').unref();
    await once(server, 'listening');
    const held: Socket[] = [];
    server.on('connection', (job: Socket) => held.push(job.unref().on('error', () => {}).resume()));
    const session = `p-test/t1.${backend}`;
    t.after(() => callAgent.close!('p-test/'));
    const first = await call(backend, { output: handoff, jobs: { ms: 60_000, port: (server.address() as AddressInfo).port } }, { session });
    for (let waited = 0; held.length < 2; waited += 50) {
      assert.ok(waited < 10_000, 'the jobs did not start');
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    const { run } = seen(backend);
    const second = await call(backend, { output: handoff }, { session, resume: first.threadId! });
    assert.deepEqual([seen(backend).run, second.threadId, second.handoff?.to], [run, first.threadId, 'reviewer']);
    assert.ok(held.every(job => !job.closed), 'a job ended with its turn');
    // A turn that asks for another model: Codex takes it with the turn, Claude Code on a new CLI, which resumes the session.
    const third = await call(backend, { output: handoff }, { session, resume: first.threadId!, model: 'another' });
    assert.deepEqual([seen(backend).run === run, seen(backend).model, third.threadId], [backend === 'codex', 'another', first.threadId]);
    await callAgent.close!('p-test/');
    for (let waited = 0; held.some(job => !job.closed); waited += 50) {
      assert.ok(waited < 10_000, 'a job outlived its session');
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.deepEqual(jobs(), ['started\n', 'started\n']);
  });
}

test('stopping a turn ends what it started, however it was detached', { skip: unseen, timeout: 30_000 }, async () => {
  const server = createServer().listen(0, '127.0.0.1').unref();
  await once(server, 'listening');
  const held: Socket[] = [];
  server.on('connection', (job: Socket) => held.push(job.unref().on('error', () => {}).resume()));
  const controller = new AbortController();
  const port = (server.address() as AddressInfo).port;
  const pending = call('codex', { hang: port, jobs: { ms: 60_000, port } }, { signal: controller.signal });
  for (let waited = 0; held.length < 3; waited += 50) {
    assert.ok(waited < 10_000, 'the jobs did not start');
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  controller.abort();
  await pending;
  for (let waited = 0; held.some(job => !job.closed); waited += 50) {
    assert.ok(waited < 10_000, 'a job outlived the stop');
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.deepEqual(jobs(), ['started\n', 'started\n']);
});

test('a CLI not on PATH says how to install it', async () => {
  // Nothing on PATH, so no CLI installed on this machine can answer either.
  const path = process.env.PATH;
  process.env.PATH = join(root, 'nowhere');
  try {
    for (const backend of BACKENDS) {
      const result = await call(backend, { output: handoff });
      assert.deepEqual([result.completed, result.handoff], [false, null]);
      assert.match(result.error ?? '', new RegExp(`^${backend} is not installed .*Install it with: npm install -g \\S+`));
    }
  } finally {
    process.env.PATH = path;
  }
});
