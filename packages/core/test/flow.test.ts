import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AgentRequest, CallAgent, Role } from '../src/types.js';
import { Store, type FlowEvent } from '../src/store.js';
import { Flow } from '../src/flow.js';
import { WAYS } from '../src/prompts.js';

/** One scripted turn: the role expected, and the letter it ends with (no `to`: none). */
interface Step {
  role: Role;
  to?: string;
  letter?: string;
  /** Addressees the hand-off's check must refuse before the scripted one passes. */
  refuse?: string[];
  /** The operator interrupts the process during this turn. */
  interrupt?: boolean;
  /** The agent layer throws this instead of returning. */
  throws?: string;
}

function agent(steps: Step[], controller?: AbortController): { callAgent: CallAgent; requests: AgentRequest[] } {
  const requests: AgentRequest[] = [];
  const callAgent: CallAgent = async request => {
    requests.push(request);
    const step = steps.shift();
    assert.ok(step, `no turn scripted for the ${request.role}`);
    assert.equal(request.role, step.role);
    for (const to of step.refuse ?? []) assert.ok(request.check?.({ form: request.form, to, letter: 'l' }), `to=${to} passed`);
    if (step.interrupt) controller?.abort();
    if (step.throws) throw new Error(step.throws);
    const handoff = step.to === undefined ? null : { form: request.form, to: step.to, letter: step.letter ?? `${step.role} to ${step.to}` };
    if (handoff) assert.equal(request.check?.(handoff) ?? null, null);
    return {
      callId: `call-${requests.length}`, threadId: request.resume ?? `${request.role}-${requests.length}`, completed: !step.interrupt,
      error: step.interrupt ? 'interrupted' : handoff ? null : 'The turn ended without the hand-off form.', handoff, model: 'fake', usage: null, ms: 1,
    };
  };
  return { callAgent, requests };
}

async function setup(t: TestContext, steps: Step[], controller?: AbortController) {
  const home = await mkdtemp(join(tmpdir(), 'argus-flow-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const { callAgent, requests } = agent(steps, controller);
  const flow = new Flow(store, callAgent, controller ? { signal: controller.signal } : {});
  const { id } = store.create({ workdir: home, request: 'the request', backend: 'codex', model: null });
  const events = (): FlowEvent[] => readFileSync(join(store.dir(id), 'events.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line) as FlowEvent);
  return { home, store, flow, id, requests, events };
}

const brief = 'Build the CSV summary tool.\nsummary.py prints the column totals.';
const toEngineer: Step = { role: 'manager', to: 'engineer', letter: brief };
const report: Step = { role: 'manager', to: 'operator', letter: 'summary.py prints the totals; checked against data.csv.' };

test('the Manager answers directly', async t => {
  const { store, flow, id, requests, events } = await setup(t, [{ role: 'manager', to: 'operator', letter: '42' }]);
  const project = await flow.say(id, 'What is 6 times 7?');
  assert.equal(project.status, 'idle');
  assert.deepEqual(store.load(id), project);
  const [request] = requests;
  assert.deepEqual([request?.form, request?.access, request?.readDirs], ['manager', 'write', [store.dir(id), WAYS]]);
  assert.match(request?.prompt ?? '', /^You are the Argus Manager[\s\S]*## Writing for people[\s\S]*## Message\nWhat is 6 times 7\?$/);
  const readme = join(WAYS, 'research', 'README.md');
  const research = `- research: ${readFileSync(readme, 'utf8').split('\n')[0]!.replace(/^# /, '')} (${readme})`;
  assert.ok(request?.prompt.split('Ways of working, which every role can read:\n')[1]?.includes(`${research}\n`));
  assert.deepEqual(events().map(event => event.type), ['message', 'call.start', 'call.end', 'handoff', 'status']);
  assert.deepEqual(events()[3], { ...events()[3], role: 'manager', to: 'operator', letter: '42' });
});

test('every role hears what the model calls have cost, as the budget is kept', async t => {
  const { store, flow, id, requests } = await setup(t, [{ role: 'manager', to: 'operator', letter: 'ok' }]);
  writeFileSync(join(store.dir(id), 'usage.jsonl'), '{"cost_usd":0.25}\n{"cost_usd":null}\n{"cost_usd":0.05}\n{"cost_');
  await flow.say(id, 'hello');
  assert.match(requests[0]?.prompt ?? '', /\nModel calls so far: 4, \$0\.30, as Argus records them in usage\.jsonl\./);
});

test("the operator's ways of working, under the state root, are listed and read beside Argus's own", async t => {
  const { home, store, flow, id, requests } = await setup(t, [{ role: 'manager', to: 'operator', letter: 'ok' }]);
  const mine = join(home, 'ways', 'quant');
  mkdirSync(mine, { recursive: true });
  writeFileSync(join(mine, 'README.md'), '# Backtest a trading idea without lookahead\n');
  await flow.say(id, 'hello');
  assert.deepEqual(requests[0]?.readDirs, [store.dir(id), WAYS, join(home, 'ways')]);
  assert.ok(requests[0]?.prompt.includes(`\n- quant: Backtest a trading idea without lookahead (${join(mine, 'README.md')})\n`));
});

test("the Manager's letter opens a task, whose Reviewer writes its judgment back for the Manager to report", async t => {
  const { store, flow, id, requests, events } = await setup(t, [
    toEngineer,
    { role: 'engineer', to: 'reviewer', letter: 'summary.py added; run python summary.py data.csv' },
    { role: 'reviewer', to: 'manager', letter: 'Accepted: the totals match the data.' },
    report,
  ]);
  const project = await flow.say(id, 'Make me a CSV summary tool');
  assert.equal(project.status, 'idle');
  assert.deepEqual(project.tasks.map(({ id, title, from, status, note }) => [id, title, from, status, note]), [
    ['t1', 'Build the CSV summary tool.', 'manager', 'closed', { from: 'reviewer', text: 'Accepted: the totals match the data.' }],
  ]);
  const [, engineer, reviewer, manager] = requests;
  assert.deepEqual([engineer?.form, engineer?.access, engineer?.readDirs], ['engineer', 'write', [store.dir(id), WAYS]]);
  assert.match(engineer?.prompt ?? '', /^You are the Argus Engineer[\s\S]*## Where things are\n[\s\S]*## Task t1, from the Manager\nBuild the CSV summary tool\.\nsummary\.py prints the column totals\.$/);
  assert.deepEqual([reviewer?.form, reviewer?.access], ['review', 'read']);
  assert.match(reviewer?.prompt ?? '', /^You are the Argus Reviewer[\s\S]*## From the Engineer\nsummary\.py added; run python summary\.py data\.csv$/);
  assert.equal(manager?.resume, 'manager-1');
  assert.equal(manager?.prompt, 'From the Reviewer, on task t1 (Build the CSV summary tool.):\nAccepted: the totals match the data.');
  assert.deepEqual(events().map(event => event.type), [
    'message', 'call.start', 'call.end', 'handoff', 'task.added', 'call.start', 'call.end', 'handoff',
    'call.start', 'call.end', 'handoff', 'task.closed', 'call.start', 'call.end', 'handoff', 'status',
  ]);
});

test('a review returns the work once, and both roles continue their sessions', async t => {
  const { flow, id, requests } = await setup(t, [
    toEngineer,
    { role: 'engineer', to: 'reviewer', letter: 'v1' },
    { role: 'reviewer', to: 'engineer', letter: 'Totals skip the last row.' },
    { role: 'engineer', to: 'reviewer', letter: 'v2 counts the last row' },
    { role: 'reviewer', to: 'manager', letter: 'Accepted.' },
    report,
  ]);
  assert.equal((await flow.say(id, 'Make me a CSV summary tool')).status, 'idle');
  const [, , , engineer, reviewer] = requests;
  assert.equal(engineer?.resume, 'engineer-2');
  assert.equal(engineer?.prompt, '## From the Reviewer\nTotals skip the last row.');
  assert.equal(reviewer?.resume, 'reviewer-3');
  assert.match(reviewer?.prompt ?? '', /^The Engineer has worked on the task since your last judgment\.[\s\S]*\n\n## From the Engineer\nv2 counts the last row$/);
});

test("the Engineer's question stops the work until the operator answers", async t => {
  const question = 'Round the totals, or reject the bad rows?';
  const steps: Step[] = [toEngineer, { role: 'engineer', to: 'operator', letter: question }];
  const { store, flow, id, requests } = await setup(t, steps);
  const asking = await flow.say(id, 'Make me a CSV summary tool');
  assert.equal(asking.status, 'asking');
  assert.deepEqual(asking.question, { role: 'engineer', task: 't1', question });
  // The operator may talk to the Manager meanwhile; the Engineer works one task at a time.
  steps.push({ role: 'manager', to: 'operator', letter: 'The team needs your choice first.', refuse: ['engineer'] });
  assert.equal((await flow.say(id, 'Also export a PDF')).status, 'asking');
  assert.equal(requests.length, 3);
  steps.push({ role: 'engineer', to: 'reviewer', letter: 'rounded' }, { role: 'reviewer', to: 'manager', letter: 'ok' }, report);
  const rested = await flow.answer(id, 'round');
  assert.deepEqual([rested.status, rested.question], ['idle', null]);
  assert.equal(requests[3]?.resume, 'engineer-2');
  assert.equal(requests[3]?.prompt, `## From the operator\nYou asked the operator: ${question}\nThey answered: round`);
  await assert.rejects(flow.answer(id, 'again'), /no open question/);
  assert.equal(store.load(id).status, 'idle');
});

const refusal = 'Argus refused your hand-off: The turn ended without the hand-off form.\nEnd your turn with the form again, corrected.';

test('a refused hand-off goes back to its writer once, in its session, with why', async t => {
  const { flow, id, requests } = await setup(t, [
    toEngineer, { role: 'engineer' }, { role: 'engineer', to: 'reviewer' }, { role: 'reviewer', to: 'manager' }, report,
  ]);
  assert.equal((await flow.say(id, 'Make me a CSV summary tool')).status, 'idle');
  assert.deepEqual([requests[2]?.resume, requests[2]?.prompt], ['engineer-2', refusal]);
});

test('a hand-off refused again is a failed call: recorded, and nothing runs after it', async t => {
  const steps: Step[] = [toEngineer, { role: 'engineer' }, { role: 'engineer' }];
  const { flow, id, requests, events } = await setup(t, steps);
  const failed = await flow.say(id, 'Make me a CSV summary tool');
  assert.equal(failed.status, 'failed');
  assert.equal(failed.error, "The engineer's hand-off was refused: The turn ended without the hand-off form.");
  assert.equal(requests.length, 3);
  assert.deepEqual(events().slice(-3).map(event => event.type), ['call.start', 'call.end', 'status']);
  // The operator's next message continues the work, in the Engineer's session.
  steps.push({ role: 'manager', to: 'operator', letter: 'Trying again.' }, { role: 'engineer', to: 'reviewer' }, { role: 'reviewer', to: 'manager' }, report);
  assert.equal((await flow.say(id, 'try again')).status, 'idle');
  assert.deepEqual([requests[4]?.resume, requests[4]?.prompt], ['engineer-2', 'Continue the task.']);
});

test('a call the agent layer cannot make is a failed call too', async t => {
  const { flow, id, requests } = await setup(t, [{ role: 'manager', throws: 'spawn codex ENOENT' }]);
  const project = await flow.say(id, 'hello');
  assert.deepEqual([project.status, project.error, requests.length], ['failed', 'spawn codex ENOENT', 1]);
});

test('the Planner hands out one task at a time, and writes to the Manager when the goal is met', async t => {
  const { store, flow, id, requests } = await setup(t, [
    { role: 'manager', to: 'planner', letter: brief },
    { role: 'planner', to: 'engineer', letter: 'Parse data.csv\nCheck: the parser tests pass.' },
    { role: 'engineer', to: 'reviewer', letter: 'parsed' },
    { role: 'reviewer', to: 'planner', letter: 'Accepted: the parser reads every row.' },
    { role: 'planner', to: 'engineer', letter: 'Write the totals report' },
    { role: 'engineer', to: 'reviewer', letter: 'reported' },
    { role: 'reviewer', to: 'planner', letter: 'Accepted.' },
    { role: 'planner', to: 'manager', letter: 'The summary tool is built and checked.' },
    report,
  ]);
  const project = await flow.say(id, 'Summarise data.csv, in stages');
  assert.equal(project.status, 'idle');
  assert.deepEqual(project.tasks.map(task => [task.id, task.title, task.from, task.status]),
    [['t1', 'Parse data.csv', 'planner', 'closed'], ['t2', 'Write the totals report', 'planner', 'closed']]);
  const [, planner, parse, , replan, report2, , , manager] = requests;
  assert.deepEqual([planner?.form, planner?.access, planner?.readDirs], ['plan', 'read', [store.dir(id), WAYS]]);
  assert.match(planner?.prompt ?? '', /## Tasks\n\(none yet\)\n\n## Since your last turn\nFrom the Manager:\nBuild the CSV summary tool\.\nsummary\.py prints the column totals\.$/);
  assert.match(parse?.prompt ?? '', /## Task t1, from the Planner\nParse data\.csv\nCheck: the parser tests pass\.$/);
  assert.equal(replan?.resume, 'planner-2');
  // The fake calls cost nothing Argus records; a real one adds its line to usage.jsonl.
  assert.equal(replan?.prompt, 'Model calls so far: 0, $0.00, as Argus records them in usage.jsonl. An operator\'s budget is kept in this figure; there is nothing more to verify about it.\n\n'
    + '## Tasks\n- t1 [closed] Parse data.csv\n\n## Since your last turn\nFrom the Reviewer, on task t1 (Parse data.csv):\nAccepted: the parser reads every row.');
  assert.match(report2?.prompt ?? '', /## Earlier tasks\n- t1 Parse data\.csv\. Its last word, from the Reviewer: Accepted: the parser reads every row\.\n\n## Task t2, from the Planner\n/);
  assert.equal(manager?.prompt, 'From the Planner:\nThe summary tool is built and checked.');
  assert.deepEqual(project.inbox, []);
});

test('a task that cannot go on as briefed goes back to the Planner, and on to the Manager', async t => {
  const { flow, id, requests } = await setup(t, [
    { role: 'manager', to: 'planner', letter: brief },
    { role: 'planner', to: 'engineer', letter: 'Parse data.csv' },
    { role: 'engineer', to: 'planner', letter: 'The data has no totals column.' },
    { role: 'planner', to: 'manager', letter: 'Nothing to total: the operator must name a column.' },
    { role: 'manager', to: 'operator', letter: 'The data cannot give totals; tell me which column to sum.' },
  ]);
  const project = await flow.say(id, 'Summarise data.csv, in stages');
  assert.equal(project.status, 'idle');
  assert.deepEqual(project.tasks.map(task => [task.status, task.note]), [['closed', { from: 'engineer', text: 'The data has no totals column.' }]]);
  assert.match(requests[3]?.prompt ?? '', /- t1 \[closed\] Parse data\.csv\n\n## Since your last turn\nFrom the Engineer, on task t1 \(Parse data\.csv\):\nThe data has no totals column\.$/);
  assert.equal(requests[4]?.resume, 'manager-1');
  assert.equal(requests[4]?.prompt, 'From the Planner:\nNothing to total: the operator must name a column.');
});

test('a restarted process continues from project.json', async t => {
  const controller = new AbortController();
  const { home, flow, id, requests } = await setup(t, [
    toEngineer,
    { role: 'engineer', to: 'reviewer', letter: 'summary.py added' },
    { role: 'reviewer', interrupt: true },
  ], controller);
  const interrupted = await flow.say(id, 'Make me a CSV summary tool');
  assert.equal(requests.length, 3);
  assert.equal(interrupted.status, 'working');
  const store = new Store(home);
  assert.deepEqual(store.load(id).tasks[0]?.holder, 'reviewer');
  const next = agent([{ role: 'manager', to: 'operator', letter: 'Carrying on.' }, { role: 'reviewer', to: 'manager', letter: 'ok' }, report]);
  const project = await new Flow(store, next.callAgent).say(id, 'continue');
  assert.equal(project.status, 'idle');
  assert.equal(next.requests[0]?.resume, 'manager-1');
  assert.equal(next.requests[1]?.resume, undefined);
  assert.match(next.requests[1]?.prompt ?? '', /^You are the Argus Reviewer[\s\S]*## From the Engineer\nsummary\.py added$/);
});

test('one process owns the state root; a dead owner is replaced', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-owner-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const release = store.own();
  assert.throws(() => store.own(), new RegExp(`in use by process ${process.pid}`));
  release();
  const { pid } = spawnSync(process.execPath, ['-e', '']);
  writeFileSync(join(home, 'owner.json'), JSON.stringify({ pid }));
  store.own()();
  assert.throws(() => readFileSync(join(home, 'owner.json')), { code: 'ENOENT' });
});

test('a save outlasts another program holding project.json a moment', { skip: process.platform !== 'win32' && 'Windows only' }, async t => {
  const { store, id } = await setup(t, []);
  // Windows refuses to replace a file another program has open without delete sharing, as a
  // virus scanner or an indexer may.
  const holder = spawn('powershell', ['-NoProfile', '-Command', `$file = [IO.File]::Open('${join(store.dir(id), 'project.json')}', 'Open', 'Read', 'Read');`
    + ` [Console]::WriteLine('held'); Start-Sleep -Milliseconds 500; $file.Close()`]);
  await once(holder.stdout, 'data');
  store.save({ ...store.load(id), request: 'changed' });
  assert.equal(store.load(id).request, 'changed');
});
