import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { spawnSync } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AgentRequest, CallAgent, HandOff, Role } from '../src/types.js';
import { Store, type FlowEvent } from '../src/store.js';
import { Flow } from '../src/flow.js';

/** One scripted turn: the role expected, and what it hands off (no `to`: no hand-off). */
interface Step {
  role: Role;
  to?: string;
  fields?: Record<string, unknown>;
  text?: string;
  /** Hand-offs the form's check must refuse before the scripted one passes. */
  refuse?: Omit<HandOff, 'form' | 'summary'>[];
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
    for (const refused of step.refuse ?? []) {
      assert.ok(request.check?.({ form: request.form, summary: 's', ...refused }), `${JSON.stringify(refused)} passed`);
    }
    if (step.interrupt) controller?.abort();
    if (step.throws) throw new Error(step.throws);
    const handoff = step.to === undefined ? null
      : { form: request.form, to: step.to, summary: `${step.role} to ${step.to}`, fields: { assumptions: [], ...step.fields } };
    if (handoff) assert.equal(request.check?.(handoff), null);
    return {
      callId: `call-${requests.length}`, threadId: request.resume ?? `${request.role}-${requests.length}`, completed: !step.interrupt,
      error: step.interrupt ? 'interrupted' : null, handoff, text: step.text ?? '', model: 'fake', usage: null, ms: 1,
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

const ticket = { goal: 'Build the CSV summary tool', acceptance: ['summary.py prints the column totals'] };

test('the Manager answers directly', async t => {
  const { store, flow, id, requests, events } = await setup(t, [{ role: 'manager', text: '42' }]);
  const project = await flow.say(id, 'What is 6 times 7?');
  assert.equal(project.status, 'idle');
  assert.deepEqual(store.load(id), project);
  const [request] = requests;
  assert.equal(request?.form, 'manager');
  assert.equal(request?.access, 'write');
  assert.deepEqual(request?.readDirs, [store.dir(id)]);
  assert.match(request?.prompt ?? '', /^You are the Argus Manager[\s\S]*## Writing for people[\s\S]*## Message\nWhat is 6 times 7\?$/);
  assert.deepEqual(events().map(event => event.type), ['message', 'call.start', 'call.end', 'message', 'status']);
  assert.deepEqual(events()[3], { ...events()[3], from: 'manager', text: '42' });
});

test('a bounded ticket goes from the Engineer to the Reviewer, who accepts it', async t => {
  const { flow, id, requests, events } = await setup(t, [
    { role: 'manager', to: 'team', fields: ticket, text: 'The team has it.',
      refuse: [{ to: 'team', fields: { goal: 'g' } }, { to: 'manager', fields: ticket }, { to: 'operator', fields: {} }] },
    { role: 'engineer', to: 'reviewer', fields: { note: 'summary.py added; run python summary.py data.csv' },
      refuse: [{ to: 'done', fields: { note: 'n' } }] },
    { role: 'reviewer', to: 'done', fields: { note: 'Totals checked against the data.' } },
  ]);
  const project = await flow.say(id, 'Make me a CSV summary tool');
  assert.equal(project.status, 'done');
  assert.deepEqual(project.tasks.map(task => [task.id, task.status, task.title]), [['t1', 'done', 'Build the CSV summary tool']]);
  assert.equal(project.ticket?.task, 't1');
  const [, engineer, reviewer] = requests;
  assert.deepEqual([engineer?.form, engineer?.access, engineer?.readDirs], ['engineer', 'write', undefined]);
  assert.match(engineer?.prompt ?? '', /^You are the Argus Engineer[\s\S]*## Ticket\nBuild the CSV summary tool\n\nAcceptance:\n- summary\.py prints the column totals$/);
  assert.deepEqual([reviewer?.form, reviewer?.access], ['review', 'read']);
  assert.match(reviewer?.prompt ?? '', /^You are the Argus Reviewer[\s\S]*## From the Engineer\nsummary\.py added; run python summary\.py data\.csv$/);
  assert.deepEqual(events().map(event => event.type), [
    'message', 'call.start', 'call.end', 'handoff', 'message', 'task.added',
    'call.start', 'call.end', 'handoff', 'call.start', 'call.end', 'handoff', 'task.settled', 'status',
  ]);
});

test('a review returns the work once, and both roles continue their sessions', async t => {
  const { flow, id, requests } = await setup(t, [
    { role: 'manager', to: 'team', fields: ticket },
    { role: 'engineer', to: 'reviewer', fields: { note: 'v1' } },
    { role: 'reviewer', to: 'engineer', fields: { note: 'Totals skip the last row.' } },
    { role: 'engineer', to: 'reviewer', fields: { note: 'v2 counts the last row' } },
    { role: 'reviewer', to: 'done', fields: { note: 'ok' } },
  ]);
  assert.equal((await flow.say(id, 'Make me a CSV summary tool')).status, 'done');
  const [, , , engineer, reviewer] = requests;
  assert.equal(engineer?.resume, 'engineer-2');
  assert.equal(engineer?.prompt, '## From the Reviewer\nTotals skip the last row.');
  assert.equal(reviewer?.resume, 'reviewer-3');
  assert.match(reviewer?.prompt ?? '', /^The Engineer has worked on the task since your last judgment\.[\s\S]*\n\n## From the Engineer\nv2 counts the last row$/);
});

test("the Engineer's question stops the work until the operator answers", async t => {
  const ask = { question: 'Round the totals or reject bad rows?', options: [{ label: 'round' }, { label: 'reject' }] };
  const steps: Step[] = [
    { role: 'manager', to: 'team', fields: ticket },
    { role: 'engineer', to: 'operator', fields: { note: '', ask },
      refuse: [{ to: 'operator', fields: { note: '', ask: { question: 'Which one?', options: [{ label: 'a' }] } } }, { to: 'reviewer', fields: { note: '', ask } }] },
  ];
  const { store, flow, id, requests } = await setup(t, steps);
  const asking = await flow.say(id, 'Make me a CSV summary tool');
  assert.equal(asking.status, 'asking');
  assert.deepEqual(asking.question, { role: 'engineer', task: 't1', ...ask });
  assert.equal(requests.length, 2);
  // The operator may talk to the Manager meanwhile; no new work starts while the ticket is open.
  steps.push({ role: 'manager', text: 'The team needs your choice first.', refuse: [{ to: 'team', fields: ticket }] });
  assert.equal((await flow.say(id, 'Also export a PDF')).status, 'asking');
  assert.equal(requests.length, 3);
  steps.push({ role: 'engineer', to: 'reviewer', fields: { note: 'rounded' } }, { role: 'reviewer', to: 'done', fields: { note: 'ok' } });
  const done = await flow.answer(id, 'round');
  assert.equal(done.status, 'done');
  assert.equal(done.question, null);
  assert.equal(requests[3]?.resume, 'engineer-2');
  assert.equal(requests[3]?.prompt, '## From the operator\nYou asked the operator: Round the totals or reject bad rows?\nThey answered: round');
  await assert.rejects(flow.answer(id, 'again'), /no open question/);
  assert.equal(store.load(id).status, 'done');
});

test('a turn without a hand-off is a failed call: recorded, and nothing runs after it', async t => {
  const steps: Step[] = [{ role: 'manager', to: 'team', fields: ticket }, { role: 'engineer' }];
  const { flow, id, requests, events } = await setup(t, steps);
  const failed = await flow.say(id, 'Make me a CSV summary tool');
  assert.equal(failed.status, 'failed');
  assert.equal(failed.error, "The engineer's turn ended without a hand-off.");
  assert.equal(failed.sessions['t1.engineer'], undefined);
  assert.equal(requests.length, 2);
  assert.deepEqual(events().slice(-3).map(event => event.type), ['call.start', 'call.end', 'status']);
  // The operator's next message continues the work; the Engineer starts afresh.
  steps.push({ role: 'manager', text: 'Trying again.' }, { role: 'engineer', to: 'reviewer', fields: { note: 'n' } }, { role: 'reviewer', to: 'done', fields: { note: 'ok' } });
  assert.equal((await flow.say(id, 'try again')).status, 'done');
  assert.equal(requests[3]?.resume, undefined);
  assert.match(requests[3]?.prompt ?? '', /^You are the Argus Engineer/);
});

test('a call the agent layer cannot make is a failed call too', async t => {
  const { flow, id, requests } = await setup(t, [{ role: 'manager', throws: 'spawn codex ENOENT' }]);
  const project = await flow.say(id, 'hello');
  assert.deepEqual([project.status, project.error, requests.length], ['failed', 'spawn codex ENOENT', 1]);
});

test('a standing ticket is never done by itself: the Planner decides what comes next', async t => {
  const { flow, id, requests } = await setup(t, [
    { role: 'manager', to: 'team', fields: { ...ticket, lifetime: 'standing' } },
    { role: 'engineer', to: 'reviewer', fields: { note: 'n' } },
    { role: 'reviewer', to: 'done', fields: { note: 'ok' } },
    { role: 'planner', to: 'engineer', refuse: [{ to: 'done', fields: {} }] },
  ]);
  const project = await flow.say(id, 'Keep the CSV summary tool improving');
  assert.equal(project.status, 'idle');
  assert.match(requests[3]?.prompt ?? '', /## Since your last turn\nTask t1 is done: reviewer to done$/);
});

test('the Planner schedules two dependent tasks and closes the ticket', async t => {
  const tasks = [
    { key: 'parse', title: 'Parse', objective: 'Parse data.csv', acceptance_check: 'the parser tests pass' },
    { key: 'report', title: 'Report', objective: 'Write the totals report', deps: ['parse'] },
  ];
  const { flow, id, requests } = await setup(t, [
    { role: 'manager', to: 'team', fields: { ...ticket, workflow_mode: 'staged' } },
    { role: 'planner', to: 'engineer', fields: { tasks },
      refuse: [{ to: 'engineer', fields: { tasks: [...tasks].reverse() } }, { to: 'done', fields: { tasks } }, { to: 'engineer', fields: { retire: [{ id: 't9', reason: 'r' }] } }] },
    { role: 'engineer', to: 'reviewer', fields: { note: 'parsed' } },
    { role: 'reviewer', to: 'done', fields: { note: 'ok' } },
    { role: 'engineer', to: 'reviewer', fields: { note: 'reported' } },
    { role: 'reviewer', to: 'done', fields: { note: 'ok' } },
    { role: 'planner', to: 'done' },
  ]);
  const project = await flow.say(id, 'Summarise data.csv, in stages');
  assert.equal(project.status, 'done');
  assert.deepEqual(project.tasks.map(task => [task.id, task.deps, task.status]), [['t1', [], 'done'], ['t2', ['t1'], 'done']]);
  const [, planner, parse, , report, , replan] = requests;
  assert.deepEqual([planner?.form, planner?.access, planner?.readDirs?.length], ['plan', 'read', 1]);
  assert.match(planner?.prompt ?? '', /## Tasks\n\(none yet\)\n\n## Since your last turn\nThe Manager filed the ticket above: plan its work\.$/);
  assert.match(parse?.prompt ?? '', /## Task t1: Parse\nParse data\.csv\nCheck: the parser tests pass$/);
  assert.equal(report?.resume, undefined);
  assert.equal(replan?.resume, 'planner-2');
  assert.match(replan?.prompt ?? '', /^## Ticket\n[\s\S]*- t1 \[done\] Parse\n- t2 \[done, after t1\] Report\n\n## Since your last turn\nTask t1 is done: reviewer to done\n\nTask t2 is done: reviewer to done$/);
  assert.deepEqual(project.inbox, []);
});

test('work handed to the Planner holds its dependents back, and the Planner can hand it to the Manager', async t => {
  const tasks = [{ key: 'parse', title: 'Parse', objective: 'Parse data.csv' }, { key: 'report', title: 'Report', objective: 'Report', deps: ['parse'] }];
  const { flow, id, requests } = await setup(t, [
    { role: 'manager', to: 'team', fields: { ...ticket, workflow_mode: 'staged' } },
    { role: 'planner', to: 'engineer', fields: { tasks } },
    { role: 'engineer', to: 'planner', fields: { note: 'The data has no totals column.' } },
    { role: 'planner', to: 'manager', fields: { retire: [{ id: 't2', reason: 'Nothing to report without totals.' }] } },
    { role: 'manager', text: 'The data cannot give totals; tell me which column to sum.' },
  ]);
  const project = await flow.say(id, 'Summarise data.csv, in stages');
  assert.equal(project.status, 'idle');
  assert.deepEqual(project.tasks.map(task => task.status), ['returned', 'retired']);
  assert.match(requests[3]?.prompt ?? '', /- t1 \[returned\] Parse\n- t2 \[open, with the Engineer, after t1\] Report\n\n## Since your last turn\nThe Engineer handed task t1 \(Parse\) to you: engineer to planner\n\nThe data has no totals column\.$/);
  assert.equal(requests[4]?.resume, 'manager-1');
  assert.equal(requests[4]?.prompt, 'The Planner handed the work to you: planner to manager');
});

test('a restarted process continues from project.json', async t => {
  const controller = new AbortController();
  const { home, flow, id, requests } = await setup(t, [
    { role: 'manager', to: 'team', fields: ticket },
    { role: 'engineer', to: 'reviewer', fields: { note: 'summary.py added' } },
    { role: 'reviewer', interrupt: true },
  ], controller);
  const interrupted = await flow.say(id, 'Make me a CSV summary tool');
  assert.equal(requests.length, 3);
  assert.equal(interrupted.status, 'working');
  const store = new Store(home);
  assert.deepEqual(store.load(id).tasks[0]?.holder, 'reviewer');
  const next = agent([{ role: 'manager', text: 'Carrying on.' }, { role: 'reviewer', to: 'done', fields: { note: 'ok' } }]);
  const project = await new Flow(store, next.callAgent).say(id, 'continue');
  assert.equal(project.status, 'done');
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
