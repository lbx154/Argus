import assert from 'node:assert/strict';
import { once } from 'node:events';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs';
import { createServer, type AddressInfo, type Socket } from 'node:net';
import { homedir, tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';
import test, { after } from 'node:test';
import { fileURLToPath } from 'node:url';
import { schemaErrors } from '@argus/contracts';
import { quoteTokenUsage } from '@argus/runtime';
import { formSchema, judge } from '../src/agent/handoff.js';
import { confine, privatePath, sandboxed } from '../src/agent/sandbox.js';
import { writing } from '../src/agent/steps.js';
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
  steered?: string;
};
const handoff = { to: 'reviewer', letter: 'The parser reads both formats; its tests pass.\nRun npm test.', open: [] };

test('a hand-off is a letter to one of the places its role can write to', () => {
  assert.deepEqual(schemaErrors({ to: 'done', letter: '', open: [], extra: 1 }, formSchema('engineer')), [
    'to must be one of ["reviewer","engineer","planner","manager","operator"]', 'letter must have length >= 1', 'extra is not allowed',
  ]);
  assert.deepEqual(schemaErrors({ to: 'planner' }, formSchema('manager')), ['letter is required', 'open is required', 'task is required']);
  // A letter that hands over a result names its files; the Planner's letters hand over none.
  const report = { to: 'operator', letter: 'The report is done.', open: ['report/index.html', 'figures/a.png'], task: '' };
  assert.deepEqual(judge('manager', formSchema('manager'), report), { form: 'manager', to: 'operator', letters: ['The report is done.'], open: report.open });
  assert.deepEqual(judge('manager', formSchema('manager'), { ...report, open: [] }), { form: 'manager', to: 'operator', letters: ['The report is done.'] });
  // Only letters to the Engineer are for tasks: the Planner's question naming one is for none, so its answer finds it.
  assert.deepEqual(judge('plan', formSchema('plan'), { to: 'operator', letters: [{ letter: 'Which dataset should t1 use?', task: 't1', after: [] }] }),
    { form: 'plan', to: 'operator', letters: ['Which dataset should t1 use?'] });
  assert.deepEqual(judge('review', formSchema('review', 'planner'), { to: 'planner', letter: report.letter, open: report.open }),
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
  assert.deepEqual(judge('manager', formSchema('manager'), { to: 'engineer', letter: '继续现有任务，使用新模型。', task: 't1', open: [] }),
    { form: 'manager', to: 'engineer', letters: ['继续现有任务，使用新模型。'], tasks: [{ task: 't1', after: [] }] });
  // A letter that opens a task names it in its first line, the title the progress map shows.
  for (const form of ['manager', 'plan'] as const) assert.match(String(formSchema(form).properties!.to!.description), /first line names/);
  assert.doesNotMatch(String(formSchema('manager').properties!.to!.description), /however small: separate things asked for/);
  assert.doesNotMatch(String(formSchema('engineer').properties!.to!.description), /a task the Manager gave cannot go on as briefed/);
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
  // Every project's roles share Argus's cache, in place of the machine's.
  const argv = confine(['codex'], { backend: 'codex', role: 'engineer', form: 'engineer', prompt: '', cwd: work, access: 'read', callsDir: '', usageFile: '' });
  const cache = join(homedir(), '.cache');
  assert.deepEqual(argv.slice(argv.indexOf(cache) - 2, argv.indexOf(cache) + 1), ['--bind-try', join(root, 'argus', 'cache'), cache]);
  assert.equal(argv[argv.indexOf('XDG_CACHE_HOME') + 1], cache);
  assert.equal(argv[argv.indexOf('npm_config_cache') + 1], join(cache, 'npm'));
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
  test(`${backend}: the Manager can address an existing task through its form`, async () => {
    const result = await call(backend, { output: { to: 'engineer', letter: 'Continue the existing task.', task: 't1', open: [] } }, { role: 'manager', form: 'manager' });
    assert.deepEqual(result.handoff, { form: 'manager', to: 'engineer', letters: ['Continue the existing task.'], tasks: [{ task: 't1', after: [] }] });
  });

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
    assert.deepEqual({ ...line, ts: 0 }, { ts: 0, callId: result.callId, role: 'engineer', backend, model: result.model, ms: result.ms, tokens, cost_usd });
    assert.match(readFileSync(join(root, 'argus', 'calls', `${result.callId}.jsonl`), 'utf8'), /"(type|method)":"(turn\/completed|result|agent_settled)"/);
    assert.equal(readFileSync(join(root, 'argus', 'calls', `${result.callId}.prompt.md`), 'utf8'), JSON.stringify({ output: handoff }));
  });

  test(`${backend}: the role's steps are told as its turn goes on: what it says, runs and changes`, async () => {
    const steps: Step[] = [];
    await call(backend, { output: handoff }, { onStep: step => steps.push(step) });
    // Not what only reads, or the form the turn ends with; what a subagent runs is the role's, its words its own.
    assert.deepEqual(steps, [
      // What it thinks, as its backend sums it up.
      { kind: 'thought', text: '**Reading the parser**' },
      { kind: 'message', text: 'Working on it.' }, { kind: 'command', text: 'npm test' }, { kind: 'edit', text: 'parser.js' },
      ...backend === 'codex' || backend === 'claude' ? [{ kind: 'command', text: 'npm run lint' }] : [],
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
    // A turn that asks for another model, or for the backend's own again, runs on a new CLI, which resumes the session.
    const third = await call(backend, { output: handoff }, { session, resume: first.threadId!, model: 'another' });
    assert.deepEqual([seen(backend).run === run, seen(backend).model, third.threadId], [false, 'another', first.threadId]);
    const before = seen(backend).run;
    await call(backend, { output: handoff }, { session, resume: first.threadId! });
    assert.notEqual(seen(backend).run, before);
    await callAgent.close!('p-test/');
    for (let waited = 0; held.some(job => !job.closed); waited += 50) {
      assert.ok(waited < 10_000, 'a job outlived its session');
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.deepEqual(jobs(), ['started\n', 'started\n']);
  });
}

test('codex: the words a role writes are told as they come, none once said, and of its form its letter so far', async () => {
  const words: string[] = [];
  const kinds: Array<string | undefined> = [];
  await call('codex', { output: handoff }, { onWords: (text, kind) => { words.push(text); kinds.push(kind); } });
  assert.deepEqual(kinds.slice(0, 2), ['process', 'process']);
  assert.ok(kinds.slice(3).every(kind => kind === 'answer'));
  // Not a subagent's: its words are its own.
  assert.deepEqual(words.slice(0, 3), ['Work', 'Working on it.', '']);
  const letter = words.slice(3);
  assert.equal(letter.at(-1), handoff.letter);
  assert.ok(letter.every((text, at) => handoff.letter.startsWith(text) && text.length >= (letter[at - 1]?.length ?? 0)));
});

test('the words of a form being written are its letter so far, an escape cut short left for the next', () => {
  assert.deepEqual(['{"to":"operator","letter":"Hel', '{"to":"operator","letter":"a\\nb\\u4f', '{"to":"oper', 'Working'].map(writing),
    ['Hel', 'a\nb', '', 'Working']);
});

test('codex: a turn Codex refuses fails alone, in a session that goes on; a thread it cannot resume is none', async t => {
  const session = 'p-test/t3.engineer';
  t.after(() => callAgent.close!('p-test/'));
  const first = await call('codex', { output: handoff }, { session });
  const { run } = seen('codex');
  const refused = await call('codex', { refuse: 'invalid effort' }, { session, resume: first.threadId! });
  assert.deepEqual([refused.completed, refused.error, refused.threadId], [false, 'turn/start: invalid effort', first.threadId]);
  const next = await call('codex', { output: handoff }, { session, resume: first.threadId! });
  assert.deepEqual([next.handoff?.to, seen('codex').run], ['reviewer', run]);
  const lost = await call('codex', { output: handoff }, { resume: 'gone' });
  assert.deepEqual([lost.completed, lost.threadId, lost.error], [false, null, 'thread/resume: no rollout found for thread id gone']);
});

for (const backend of ['codex'] as const) {
  test(`${backend}: a letter reaches the turn under way in a session, and none reaches a session with no turn under way`, async t => {
    const session = `p-test/t2.${backend}`;
    t.after(() => callAgent.close!('p-test/'));
    assert.equal(await callAgent.steer!(session, 'early'), false);
    const pending = call(backend, { output: handoff, steer: true }, { session });
    for (let waited = 0; !await callAgent.steer!(session, '## From the Planner\nSwitch models.'); waited += 50) {
      assert.ok(waited < 10_000, 'the turn never took the letter');
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.deepEqual([(await pending).handoff?.to, seen(backend).steered], ['reviewer', '## From the Planner\nSwitch models.']);
    assert.equal(await callAgent.steer!(session, 'late'), false);
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

test('an Engineer whose workdir holds Argus home writes the workdir, not the record it is given to read', { skip: !sandboxed() && 'requires bubblewrap' }, async () => {
  // The test's root holds ARGUS_HOME; the project's record is given to read, as every turn's is.
  const record = join(root, 'argus', 'projects', 'p-held');
  mkdirSync(record, { recursive: true });
  const [kept, own] = [join(record, 'project.json'), join(work, 'own.txt')];
  writeFileSync(kept, 'record');
  writeFileSync(own, 'work');
  const result = await call('codex', { attackPaths: [kept, own], output: handoff }, { cwd: root, readDirs: [record] });
  assert.deepEqual(JSON.parse(result.handoff!.letters[0]!), [{ content: 'record', changed: false }, { content: 'work', changed: true }]);
  assert.equal(readFileSync(kept, 'utf8'), 'record');
});

test('a review uses disk /tmp under Argus home and cleans it when the CLI ends', { skip: !sandboxed() && 'requires bubblewrap' }, async () => {
  const { readdirSync } = await import('node:fs');
  const temps = privatePath('/tmp', work);
  const result = await call('codex', { tempProbe: true, output: { to: 'manager', letter: 'probe', open: [] } },
    { role: 'reviewer', form: 'review', access: 'read', giver: 'manager', session: 'p-review/t1.reviewer' });
  assert.equal(result.completed, true, result.error ?? 'review failed');
  assert.deepEqual(JSON.parse(result.handoff!.letters[0]!), { device: statSync(temps).dev, writable: false });
  assert.deepEqual(readdirSync(temps).filter(name => name.startsWith('review-')), []);
  assert.equal(await callAgent.steer!('p-review/t1.reviewer', 'late'), false);
  const failed = await call('codex', { tempProbe: true, fail: 'review backend failed', output: { to: 'manager', letter: 'probe', open: [] } },
    { role: 'reviewer', form: 'review', access: 'read', giver: 'manager', session: 'p-review/t1.reviewer' });
  assert.equal(failed.completed, false);
  assert.deepEqual(readdirSync(temps).filter(name => name.startsWith('review-')), []);
});

test('parallel reviewers and an already running Engineer cannot read or overwrite another review’s private /tmp', { skip: !sandboxed() && 'requires bubblewrap', timeout: 30_000 }, async t => {
  const { existsSync, readdirSync } = await import('node:fs');
  const engineerSession = 'p-isolation/t1.engineer';
  t.after(() => callAgent.close!('p-isolation/'));
  // Start the writable sandbox before the private file exists.
  const engineer = await call('codex', { output: handoff }, { session: engineerSession });
  const stop = new AbortController();
  let ready = (): void => {};
  const opened = new Promise<void>(resolve => { ready = resolve; });
  const first = call('codex', { privateTemp: true, steer: true, output: { to: 'manager', letter: 'private review', open: [] } },
    { role: 'reviewer', form: 'review', access: 'read', giver: 'manager', session: 'p-isolation/t1.reviewer', signal: stop.signal,
      onStep: step => { if (step.text === 'Private temporary file ready.') ready(); } });
  t.after(async () => { stop.abort(); await first; });
  await opened;
  const temps = privatePath('/tmp', work);
  const files = readdirSync(temps, { recursive: true }).map(String).filter(path => path.endsWith('private-content')).map(path => join(temps, path));
  assert.ok(files.every(path => path.startsWith(join(root, 'argus', 'sandbox'))));
  assert.ok(!existsSync(join(work, '.argus-review')));
  assert.equal(files.length, 1);
  assert.equal(readFileSync(files[0]!, 'utf8'), 'private review content');
  const other = await call('codex', { attackPaths: files, output: { to: 'manager', letter: 'probe', open: [] } },
    { role: 'reviewer', form: 'review', access: 'read', giver: 'manager', session: 'p-isolation/t2.reviewer' });
  assert.deepEqual(JSON.parse(other.handoff!.letters[0]!), [{ content: null, changed: false }]);
  const attack = await call('codex', { attackPaths: files, output: handoff }, { session: engineerSession, resume: engineer.threadId! });
  assert.deepEqual(JSON.parse(attack.handoff!.letters[0]!), [{ content: null, changed: false }]);
  assert.equal(readFileSync(files[0]!, 'utf8'), 'private review content');
  await callAgent.steer!('p-isolation/t1.reviewer', 'Finish');
  assert.equal((await first).completed, true);
  assert.ok(files.every(path => !existsSync(path)));
});
