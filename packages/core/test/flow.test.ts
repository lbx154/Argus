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
import { refusedPrompt, SKILLS } from '../src/prompts.js';

/** One scripted turn: the role expected, and the letter it ends with (no `to`: none). */
interface Step {
  role: Role;
  to?: string;
  letter?: string;
  /** The Planner's letters, one task each. */
  letters?: string[];
  /** The turn ends after the turns already under way have. */
  later?: boolean;
  /** The turn ends once this has settled. */
  waits?: Promise<unknown>;
  /** Addressees the hand-off's check must refuse before the scripted one passes. */
  refuse?: string[];
  /** The operator interrupts the process during this turn. */
  interrupt?: boolean;
  /** The agent layer throws this instead of returning. */
  throws?: string;
  /** The turn's result arrives a moment after its form was checked, as the raw output is written. */
  slow?: boolean;
}

function agent(steps: Step[], controller?: AbortController): { callAgent: CallAgent; requests: AgentRequest[] } {
  const requests: AgentRequest[] = [];
  const callAgent: CallAgent = async request => {
    requests.push(request);
    const step = steps.shift();
    assert.ok(step, `no turn scripted for the ${request.role}`);
    assert.equal(request.role, step.role);
    if (step.later) await new Promise(resolve => setImmediate(resolve));
    await step.waits;
    for (const to of step.refuse ?? []) assert.ok(request.check?.({ form: request.form, to, letters: ['l'] }), `to=${to} passed`);
    if (step.interrupt) controller?.abort();
    if (step.throws) throw new Error(step.throws);
    const handoff = step.to === undefined ? null
      : { form: request.form, to: step.to, letters: step.letters ?? [step.letter ?? `${step.role} to ${step.to}`] };
    if (handoff) assert.equal(request.check?.(handoff) ?? null, null);
    if (step.slow) await new Promise(resolve => setImmediate(resolve));
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

/** The operator's message or answer, and the project once the work it set going rests. */
const say = async (flow: Flow, id: string, text: string) => { await flow.say(id, text); return flow.settled(id); };
const answer = (flow: Flow, id: string, text: string) => { flow.answer(id, text); return flow.settled(id); };

const brief = 'Build the CSV summary tool.\nsummary.py prints the column totals.';
const toEngineer: Step = { role: 'manager', to: 'engineer', letter: brief };
const report: Step = { role: 'manager', to: 'operator', letter: 'summary.py prints the totals; checked against data.csv.' };

test('the Manager answers directly', async t => {
  const { home, store, flow, id, requests, events } = await setup(t, [{ role: 'manager', to: 'operator', letter: '42' }]);
  const project = await say(flow, id, 'What is 6 times 7?');
  assert.equal(project.status, 'idle');
  assert.deepEqual(store.load(id), project);
  const [request] = requests;
  assert.deepEqual([request?.form, request?.access, request?.readDirs, request?.writeDirs],
    ['manager', 'write', [store.dir(id), SKILLS], [join(home, 'skills')]]);
  assert.match(request?.prompt ?? '', /^You are the Argus Manager[\s\S]*## Where things are[\s\S]*## Message\nWhat is 6 times 7\?$/);
  const readme = join(SKILLS, 'research', 'README.md');
  assert.ok(request?.prompt.includes(`\n- research: ${readFileSync(readme, 'utf8').split('\n')[0]!.replace(/^# /, '')} (${readme})\n`));
  assert.deepEqual(events().map(event => event.type), ['message', 'call.start', 'call.end', 'handoff', 'status']);
  assert.deepEqual(events()[3], { ...events()[3], role: 'manager', to: 'operator', letter: '42' });
});

test("every role is shown the skills, its own in place of Argus's of the same name, and may write them when it writes", async t => {
  const { home, store, flow, id, requests } = await setup(t, [
    { role: 'manager', to: 'planner', letter: brief },
    { role: 'planner', to: 'engineer', letter: 'Parse data.csv' },
    { role: 'engineer', to: 'reviewer', letter: 'parsed' },
    { role: 'reviewer', to: 'operator', letter: 'Is the first row a header?' },
  ]);
  const own = join(home, 'skills');
  for (const [name, purpose] of [['quant', 'Backtest a trading idea without lookahead'], ['research', 'Research as this operator wants it']]) {
    mkdirSync(join(own, name!), { recursive: true });
    writeFileSync(join(own, name!, 'README.md'), `# ${purpose}\n`);
  }
  await say(flow, id, 'Summarise data.csv');
  assert.deepEqual(requests.map(({ role, access }) => [role, access]), [['manager', 'write'], ['planner', 'read'], ['engineer', 'write'], ['reviewer', 'read']]);
  for (const { prompt, readDirs, writeDirs } of requests) {
    // A role that only reads is given the skills to write as well; its sandbox lets it read them.
    assert.deepEqual([readDirs, writeDirs], [[store.dir(id), SKILLS], [own]]);
    assert.ok(prompt.includes(`\n- quant: Backtest a trading idea without lookahead (${join(own, 'quant', 'README.md')})\n`));
    assert.ok(prompt.includes(`\n- research: Research as this operator wants it (${join(own, 'research', 'README.md')})\n`));
    assert.ok(prompt.includes(`\n- software: `) && !prompt.includes(join(SKILLS, 'research')));
    assert.ok(prompt.includes(`\nThe skills are yours: when your work teaches you something later projects can use, write a new skill in ${own} or improve one there.\n`));
  }
});

test("the Manager's letter opens a task, whose Reviewer writes its judgment back for the Manager to report", async t => {
  const { flow, id, requests, events } = await setup(t, [
    toEngineer,
    { role: 'engineer', to: 'reviewer', letter: 'summary.py added; run python summary.py data.csv' },
    { role: 'reviewer', to: 'manager', letter: 'Accepted: the totals match the data.' },
    report,
  ]);
  const project = await say(flow, id, 'Make me a CSV summary tool');
  assert.equal(project.status, 'idle');
  assert.deepEqual(project.tasks.map(({ id, title, from, status, note }) => [id, title, from, status, note]), [
    ['t1', 'Build the CSV summary tool.', 'manager', 'closed', { from: 'reviewer', text: 'Accepted: the totals match the data.' }],
  ]);
  const [, engineer, reviewer, manager] = requests;
  assert.deepEqual([engineer?.form, engineer?.access], ['engineer', 'write']);
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
  assert.equal((await say(flow, id, 'Make me a CSV summary tool')).status, 'idle');
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
  const asking = await say(flow, id, 'Make me a CSV summary tool');
  assert.equal(asking.status, 'asking');
  assert.deepEqual(asking.question, { role: 'engineer', task: 't1', question });
  // The operator may talk to the Manager meanwhile.
  steps.push({ role: 'manager', to: 'operator', letter: 'The team needs your choice first.' });
  assert.equal((await say(flow, id, 'Also export a PDF')).status, 'asking');
  assert.equal(requests.length, 3);
  steps.push({ role: 'engineer', to: 'reviewer', letter: 'rounded' }, { role: 'reviewer', to: 'manager', letter: 'ok' }, report);
  const rested = await answer(flow, id, 'round');
  assert.deepEqual([rested.status, rested.question], ['idle', null]);
  assert.equal(requests[3]?.resume, 'engineer-2');
  assert.equal(requests[3]?.prompt, `## From the operator\nYou asked the operator: ${question}\nThey answered: round`);
  assert.throws(() => flow.answer(id, 'again'), /no open question/);
  assert.equal(store.load(id).status, 'idle');
});

const refusal = 'Argus refused your hand-off: The turn ended without the hand-off form.\nEnd your turn with the form again, corrected.';

test('a refused hand-off goes back to its writer once, in its session, with why', async t => {
  const { flow, id, requests } = await setup(t, [
    toEngineer, { role: 'engineer' }, { role: 'engineer', to: 'reviewer' }, { role: 'reviewer', to: 'manager' }, report,
  ]);
  assert.equal((await say(flow, id, 'Make me a CSV summary tool')).status, 'idle');
  assert.deepEqual([requests[2]?.resume, requests[2]?.prompt], ['engineer-2', refusal]);
});

test('a hand-off refused again is a failed call: recorded, and nothing runs after it', async t => {
  const steps: Step[] = [toEngineer, { role: 'engineer' }, { role: 'engineer' }];
  const { flow, id, requests, events } = await setup(t, steps);
  const failed = await say(flow, id, 'Make me a CSV summary tool');
  assert.equal(failed.status, 'failed');
  assert.equal(failed.error, "The engineer's hand-off was refused: The turn ended without the hand-off form.");
  assert.equal(requests.length, 3);
  assert.deepEqual(events().slice(-3).map(event => event.type), ['call.start', 'call.end', 'status']);
  // The operator's next message continues the work, in the Engineer's session.
  steps.push({ role: 'manager', to: 'operator', letter: 'Trying again.' }, { role: 'engineer', to: 'reviewer' }, { role: 'reviewer', to: 'manager' }, report);
  assert.equal((await say(flow, id, 'try again')).status, 'idle');
  assert.deepEqual([requests[4]?.resume, requests[4]?.prompt], ['engineer-2', 'Continue the task.']);
});

test('a call the agent layer cannot make is a failed call too', async t => {
  const { flow, id, requests } = await setup(t, [{ role: 'manager', throws: 'spawn codex ENOENT' }]);
  const project = await say(flow, id, 'hello');
  assert.deepEqual([project.status, project.error, requests.length], ['failed', 'spawn codex ENOENT', 1]);
});

test('the Planner hands out tasks as earlier ones end, and writes to the Manager when the goal is met', async t => {
  const { flow, id, requests } = await setup(t, [
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
  const project = await say(flow, id, 'Summarise data.csv, in stages');
  assert.equal(project.status, 'idle');
  assert.deepEqual(project.tasks.map(task => [task.id, task.title, task.from, task.status]),
    [['t1', 'Parse data.csv', 'planner', 'closed'], ['t2', 'Write the totals report', 'planner', 'closed']]);
  const [, planner, parse, , replan, report2, , , manager] = requests;
  assert.deepEqual([planner?.form, planner?.access], ['plan', 'read']);
  assert.match(planner?.prompt ?? '', /## Tasks\n\(none yet\)\n\n## Since your last turn\nFrom the Manager:\nBuild the CSV summary tool\.\nsummary\.py prints the column totals\.$/);
  assert.match(parse?.prompt ?? '', /## Task t1, from the Planner\nParse data\.csv\nCheck: the parser tests pass\.$/);
  assert.equal(replan?.resume, 'planner-2');
  assert.equal(replan?.prompt, '## Tasks\n- t1 [closed] Parse data.csv\n\n## Since your last turn\nFrom the Reviewer, on task t1 (Parse data.csv):\nAccepted: the parser reads every row.');
  assert.match(report2?.prompt ?? '', /## Earlier tasks\n- t1 Parse data\.csv\. Its last word, from the Reviewer: Accepted: the parser reads every row\.\n\n## Task t2, from the Planner\n/);
  assert.equal(manager?.prompt, 'From the Planner:\nThe summary tool is built and checked.');
  assert.deepEqual(project.inbox, []);
});

test('tasks the Planner opens together run side by side, and it reads their letters together', async t => {
  const steps: Step[] = [
    { role: 'manager', to: 'planner', letter: brief },
    { role: 'planner', to: 'engineer', letters: ['Parse data.csv', 'Parse data2.csv'] },
    { role: 'engineer', to: 'operator', letter: 'Which delimiter?' },
    // The operator answers one question at a time.
    { role: 'engineer', later: true, to: 'reviewer', letter: 'parsed 2', refuse: ['operator'] },
  ];
  const { flow, id, requests } = await setup(t, steps);
  const asking = await say(flow, id, 'Summarise data.csv and data2.csv');
  assert.deepEqual([asking.status, asking.tasks.map(task => task.holder)], ['asking', ['engineer', 'reviewer']]);
  assert.match(requests[3]?.prompt ?? '', /## Running beside it, in the same workspace\n- t1 Parse data\.csv\n\n## Task t2, from the Planner\nParse data2\.csv$/);
  steps.push(
    { role: 'engineer', to: 'reviewer', letter: 'parsed 1' },
    { role: 'reviewer', to: 'planner', letter: 'Accepted 2.' },
    { role: 'reviewer', to: 'planner', letter: 'Accepted 1.' },
    { role: 'planner', to: 'manager', letter: 'Both files are parsed.' },
    report,
  );
  assert.equal((await answer(flow, id, 'comma')).status, 'idle');
  assert.equal(requests[7]?.prompt, '## Tasks\n- t1 [closed] Parse data.csv\n- t2 [closed] Parse data2.csv\n\n## Since your last turn\n'
    + 'From the Reviewer, on task t2 (Parse data2.csv):\nAccepted 2.\n\nFrom the Reviewer, on task t1 (Parse data.csv):\nAccepted 1.');
});

test('the operator talks to the Manager while the team works, and what it hands on runs beside the work', async t => {
  let release = (): void => {};
  const held = new Promise<void>(resolve => { release = resolve; });
  const { flow, id, requests, events } = await setup(t, [
    toEngineer,
    { role: 'engineer', waits: held, to: 'reviewer', letter: 'summary.py added' },
    { role: 'manager', to: 'engineer', letter: 'Export the totals as a PDF' },
    { role: 'engineer', to: 'reviewer', letter: 'PDF added' },
    { role: 'reviewer', to: 'manager', letter: 'Accepted: the PDF.' },
    { role: 'manager', to: 'operator', letter: 'The PDF is ready; the summary tool is on its way.' },
    { role: 'reviewer', to: 'manager', letter: 'Accepted.' },
    report,
  ]);
  await flow.say(id, 'Make me a CSV summary tool');
  // The Engineer is still on t1 when the operator writes again: the Manager answers at once.
  const reply = await flow.say(id, 'Also export the totals as a PDF');
  assert.deepEqual([reply?.to, flow.store.load(id).tasks.map(task => [task.id, task.status, task.holder])],
    ['engineer', [['t1', 'open', 'engineer'], ['t2', 'open', 'engineer']]]);
  // t2 goes through to its report while t1 is still held.
  while (requests.length < 6) await new Promise(resolve => setImmediate(resolve));
  release();
  const project = await flow.settled(id);
  assert.deepEqual([project.status, project.tasks.map(task => task.status)], ['idle', ['closed', 'closed']]);
  assert.deepEqual(requests.map(request => request.role),
    ['manager', 'engineer', 'manager', 'engineer', 'reviewer', 'manager', 'reviewer', 'manager']);
  // Each message is recorded when the Manager takes it up, so the letter after it is its reply.
  const conversation = events().filter(event => event.type === 'message' || (event.type === 'handoff' && event.role === 'manager'));
  assert.deepEqual(conversation.map(event => 'letter' in event ? event.letter : 'text' in event ? event.text : ''), [
    'Make me a CSV summary tool', brief, 'Also export the totals as a PDF', 'Export the totals as a PDF',
    'The PDF is ready; the summary tool is on its way.', report.letter,
  ]);
});

test("a letter for the Manager outlasts its failed turn, and reaches it when the work resumes", async t => {
  const { home, flow, id, events } = await setup(t, [
    toEngineer,
    { role: 'engineer', to: 'reviewer', letter: 'summary.py added' },
    { role: 'reviewer', to: 'manager', letter: 'Accepted.' },
    { role: 'manager', throws: 'the relay is down' },
  ]);
  const failed = await say(flow, id, 'Make me a CSV summary tool');
  assert.deepEqual([failed.status, failed.forManager], ['failed', ['From the Reviewer, on task t1 (Build the CSV summary tool.):\nAccepted.']]);
  // The failed call is recorded as ended, so nothing shows it as under way.
  assert.deepEqual(events().filter(event => event.type === 'call.end').at(-1), { ...events().filter(event => event.type === 'call.end').at(-1),
    role: 'manager', completed: false, error: 'the relay is down' });
  const next = agent([report]);
  const again = new Flow(new Store(home), next.callAgent);
  again.resume(id);
  const project = await again.settled(id);
  assert.deepEqual([project.status, project.forManager], ['idle', []]);
  assert.equal(next.requests[0]?.prompt, 'From the Reviewer, on task t1 (Build the CSV summary tool.):\nAccepted.');
  // Taken, it is gone from the record too: a process started after this one never hands it on again.
  assert.deepEqual(new Store(home).load(id).forManager, []);
});

test("the operator's message outlasts a Manager turn the process stopped, and reaches it when the work resumes", async t => {
  const controller = new AbortController();
  const { home, flow, id, events } = await setup(t, [{ role: 'manager', interrupt: true }], controller);
  const stopped = await say(flow, id, 'What is 6 times 7?');
  assert.deepEqual(stopped.forManager, ['What is 6 times 7?']);
  const next = agent([{ role: 'manager', to: 'operator', letter: '42' }]);
  const again = new Flow(new Store(home), next.callAgent);
  again.resume(id);
  const project = await again.settled(id);
  assert.equal(next.requests[0]?.prompt.endsWith('## Message\nWhat is 6 times 7?'), true);
  assert.deepEqual(project.forManager, []);
  // Its words are in the conversation once, and the reply follows them.
  assert.deepEqual(events().filter(event => event.type === 'message' || event.type === 'handoff').map(event => event.type), ['message', 'handoff']);
});

test('a message the operator withdraws during its Manager turn is not handed on again', async t => {
  const { flow, id } = await setup(t, [{ role: 'manager', interrupt: true }]);
  const withdrawn = new AbortController();
  withdrawn.abort();
  assert.equal(await flow.say(id, 'never mind', withdrawn.signal), null);
  assert.deepEqual((await flow.settled(id)).forManager, []);
});

test('a failed call stays failed when a letter from the team reaches the Manager: nothing retries it', async t => {
  const { flow, id, requests } = await setup(t, [
    { role: 'manager', to: 'planner', letter: brief },
    { role: 'planner', to: 'engineer', letters: ['Parse data.csv', 'Parse data2.csv'] },
    { role: 'engineer', throws: 'spawn codex ENOENT' },
    { role: 'engineer', later: true, to: 'manager', letter: 'data2.csv parsed' },
    { role: 'manager', to: 'planner', letter: 'Go on with the totals.' },
  ]);
  const project = await say(flow, id, 'Summarise data.csv and data2.csv');
  assert.deepEqual([project.status, project.error], ['failed', 'spawn codex ENOENT']);
  assert.deepEqual(requests.map(request => request.role), ['manager', 'planner', 'engineer', 'engineer', 'manager']);
});

test('a second question that arrives as the first is asked goes back to its writer', async t => {
  const { flow, id, requests } = await setup(t, [
    { role: 'manager', to: 'planner', letter: brief },
    { role: 'planner', to: 'engineer', letters: ['Parse data.csv', 'Parse data2.csv'] },
    { role: 'engineer', to: 'operator', letter: 'Which delimiter?', slow: true },
    { role: 'engineer', to: 'operator', letter: 'Which encoding?', slow: true },
    { role: 'engineer', to: 'reviewer', letter: 'UTF-8, as the file says' },
  ]);
  const project = await say(flow, id, 'Summarise data.csv and data2.csv');
  assert.deepEqual([project.status, project.question], ['asking', { role: 'engineer', task: 't1', question: 'Which delimiter?' }]);
  assert.equal(requests.at(-1)?.prompt, refusedPrompt('The operator has yet to answer another question: carry on without them, or write to someone else.'));
  assert.equal(project.tasks[1]?.holder, 'reviewer');
});

test("the Planner hears only of its own tasks: a letter on the Manager's goes back to its writer", async t => {
  const { flow, id } = await setup(t, [
    toEngineer,
    { role: 'engineer', to: 'reviewer', letter: 'summary.py added', refuse: ['planner'] },
    { role: 'reviewer', to: 'manager', letter: 'Accepted.', refuse: ['planner'] },
    report,
  ]);
  const project = await say(flow, id, 'Make me a CSV summary tool');
  assert.deepEqual([project.status, project.inbox], ['idle', []]);
});

test('a task that cannot go on as briefed goes back to the Planner, and on to the Manager', async t => {
  const { flow, id, requests } = await setup(t, [
    { role: 'manager', to: 'planner', letter: brief },
    { role: 'planner', to: 'engineer', letter: 'Parse data.csv' },
    { role: 'engineer', to: 'planner', letter: 'The data has no totals column.' },
    { role: 'planner', to: 'manager', letter: 'Nothing to total: the operator must name a column.' },
    { role: 'manager', to: 'operator', letter: 'The data cannot give totals; tell me which column to sum.' },
  ]);
  const project = await say(flow, id, 'Summarise data.csv, in stages');
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
  const interrupted = await say(flow, id, 'Make me a CSV summary tool');
  assert.equal(requests.length, 3);
  assert.equal(interrupted.status, 'working');
  const store = new Store(home);
  assert.deepEqual(store.load(id).tasks[0]?.holder, 'reviewer');
  const next = agent([{ role: 'manager', to: 'operator', letter: 'Carrying on.' }, { role: 'reviewer', to: 'manager', letter: 'ok' }, report]);
  const project = await say(new Flow(store, next.callAgent), id, 'continue');
  assert.equal(project.status, 'idle');
  assert.equal(next.requests[0]?.resume, 'manager-1');
  assert.equal(next.requests[1]?.resume, undefined);
  assert.match(next.requests[1]?.prompt ?? '', /^You are the Argus Reviewer[\s\S]*## From the Engineer\nsummary\.py added$/);
});

test("the projects are what is named as one: a file the desktop leaves beside them is none", async t => {
  const { store, id } = await setup(t, []);
  writeFileSync(join(store.home, 'projects', '.DS_Store'), '');
  assert.deepEqual(store.list().map(project => project.id), [id]);
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
