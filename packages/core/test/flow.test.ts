import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AgentRequest, CallAgent, Role } from '../src/types.js';
import { Store, type FlowEvent, type Question } from '../src/store.js';
import { Flow } from '../src/flow.js';
import { purpose, renameSkills, SKILLS } from '../src/prompts.js';

/** One scripted turn: the role expected, and the letter it ends with (no `to`: none). */
interface Step {
  role: Role;
  to?: string;
  letter?: string;
  /** The Planner's letters, one task each. */
  letters?: string[];
  /** For each of the Planner's letters, the tasks its task waits for. */
  after?: string[][];
  /** The files of a result the Manager hands the operator. */
  open?: string[];
  /** The turn ends after the turns already under way have. */
  later?: boolean;
  /** The turn ends once this has settled. */
  waits?: Promise<unknown>;
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
    if (step.later) await new Promise(resolve => setImmediate(resolve));
    await step.waits;
    if (step.interrupt) controller?.abort();
    if (step.throws) throw new Error(step.throws);
    const handoff = step.to === undefined ? null
      : { form: request.form, to: step.to, letters: step.letters ?? [step.letter ?? `${step.role} to ${step.to}`],
          ...step.after ? { after: step.after } : {}, ...step.open ? { open: step.open } : {} };
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
const answer = (flow: Flow, id: string, question: Question, text: string) => { flow.answer(id, question, text); return flow.settled(id); };

const brief = 'Build the CSV summary tool.\nsummary.py prints the column totals.';
const toEngineer: Step = { role: 'manager', to: 'engineer', letter: brief };
const report: Step = { role: 'manager', to: 'operator', letter: 'summary.py prints the totals; checked against data.csv.' };

test("the project's request is the message that first set the team working, not a greeting before it", async t => {
  const { flow, id } = await setup(t, [
    { role: 'manager', to: 'operator', letter: 'Hello! What shall we work on?' },
    toEngineer,
    { role: 'engineer', to: 'reviewer', letter: 'summary.py added' },
    { role: 'reviewer', to: 'manager', letter: 'Accepted.' },
    report,
    { role: 'manager', to: 'engineer', letter: 'Export the totals as a PDF' },
    { role: 'engineer', to: 'reviewer', letter: 'PDF added' },
    { role: 'reviewer', to: 'manager', letter: 'Accepted.' },
    report,
  ]);
  // A project made on the page has no request till its first message.
  flow.edit(id, project => { project.request = ''; });
  assert.equal((await say(flow, id, 'Hello')).request, 'Hello');
  assert.equal((await say(flow, id, 'Make me a CSV summary tool')).request, 'Make me a CSV summary tool');
  assert.equal((await say(flow, id, 'Also export a PDF')).request, 'Make me a CSV summary tool');
});

test('a result the Manager hands the operator keeps its files in the record, beside its letter, each as it was', async t => {
  const open = ['summary.py', 'plot.png', 'missing.md'];
  const { home, store, flow, id, events } = await setup(t, [{ ...report, open }, { ...report, open }, { ...report, open }]);
  writeFileSync(join(home, 'summary.py'), 'print(1)\n');
  writeFileSync(join(home, 'plot.png'), 'PNG\0');
  await say(flow, id, 'Make me a CSV summary tool');
  await say(flow, id, 'Send it again');
  writeFileSync(join(home, 'summary.py'), 'print(2)\n');
  const project = await say(flow, id, 'Print the totals instead');
  const handoffs = events().flatMap(event => event.type === 'handoff' ? [event] : []);
  assert.deepEqual(handoffs.map(event => [event.letter, event.open]), [[report.letter, open], [report.letter, open], [report.letter, open]]);
  // A text file is copied as delivered; a binary one is named by its size and time; a missing one is left out.
  const [first, again, changed] = handoffs.map(event => event.snapshots ?? []);
  assert.deepEqual(first?.map(({ path, size, stored }) => [path, size, stored]), [['summary.py', 9, true], ['plot.png', 4, false]]);
  // A file the same as its last copy keeps its version; a changed one has a new copy.
  assert.deepEqual(again, first);
  const versions = [first![0]!.version, changed![0]!.version];
  assert.match(versions[1]!, /^[0-9a-f]{32}$/);
  assert.notEqual(versions[1], versions[0]);
  assert.deepEqual(project.delivered, { 'summary.py': versions });
  assert.deepEqual(versions.map(version => readFileSync(join(store.dir(id), 'delivered', version), 'utf8')), ['print(1)\n', 'print(2)\n']);
});

test('the Manager answers directly', async t => {
  const { home, store, flow, id, requests, events } = await setup(t, [{ role: 'manager', to: 'operator', letter: '42' }]);
  const project = await say(flow, id, 'What is 6 times 7?');
  assert.equal(project.status, 'idle');
  assert.deepEqual(store.load(id), project);
  const [request] = requests;
  assert.deepEqual([request?.form, request?.access, request?.readDirs, request?.writeDirs],
    ['manager', 'write', [store.dir(id), SKILLS], [join(home, 'skills')]]);
  // The role's standing instructions go apart from the turn's message, with every turn.
  assert.equal(request?.prompt, '## Message\nWhat is 6 times 7?');
  assert.match(request?.instructions ?? '', /^You are the Argus Manager[\s\S]*## Where things are\n/);
  const skill = join(SKILLS, 'research', 'SKILL.md');
  assert.ok(request?.instructions?.includes('\n- research: Research that ends in a claim others should trust: the pitfalls past projects hit, '
    + `and what good evidence looks like (${skill})\n`));
  // A project with no work rests, and an answer leaves it resting.
  assert.deepEqual(events().map(event => event.type), ['message', 'call.start', 'call.end', 'handoff']);
  assert.deepEqual(events()[3], { ...events()[3], role: 'manager', to: 'operator', letter: '42' });
});

test('a skill from before SKILL.md, a README.md whose first line said what it was for, becomes one, once', async t => {
  const { home } = await setup(t, []);
  const dir = join(home, 'skills', 'quant');
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'README.md'), '# Backtest a trading idea: without lookahead\n\nRun check.mjs.\n');
  renameSkills(home);
  renameSkills(home);
  assert.equal(readFileSync(join(dir, 'SKILL.md'), 'utf8'), '---\nname: quant\ndescription: "Backtest a trading idea: without lookahead"\n---\n\nRun check.mjs.\n');
  assert.deepEqual([existsSync(join(dir, 'README.md')), purpose(dir)], [false, 'Backtest a trading idea: without lookahead']);
});

test("every role is shown the skills, its own in place of Argus's of the same name, and may write them when it writes", async t => {
  const { home, store, flow, id, requests } = await setup(t, [
    { role: 'manager', to: 'planner', letter: brief },
    { role: 'planner', to: 'engineer', letter: 'Parse data.csv' },
    { role: 'engineer', to: 'reviewer', letter: 'parsed' },
    { role: 'reviewer', to: 'operator', letter: 'Is the first row a header?' },
  ]);
  const own = join(home, 'skills');
  // A description as it stands or in quotes, as a skill for Codex or Claude Code has it.
  for (const [name, description] of [['quant', 'Backtest a trading idea without lookahead'], ['research', '"Research as this operator wants it"']]) {
    mkdirSync(join(own, name!), { recursive: true });
    writeFileSync(join(own, name!, 'SKILL.md'), `---\nname: ${name}\ndescription: ${description}\n---\n\nHow.\n`);
  }
  await say(flow, id, 'Summarise data.csv');
  assert.deepEqual(requests.map(({ role, access }) => [role, access]), [['manager', 'write'], ['planner', 'read'], ['engineer', 'write'], ['reviewer', 'read']]);
  for (const { instructions: prompt = '', readDirs, writeDirs } of requests) {
    // A role that only reads is given the skills to write as well; its sandbox lets it read them.
    assert.deepEqual([readDirs, writeDirs], [[store.dir(id), SKILLS], [own]]);
    assert.ok(prompt.includes(`\n- quant: Backtest a trading idea without lookahead (${join(own, 'quant', 'SKILL.md')})\n`));
    assert.ok(prompt.includes(`\n- research: Research as this operator wants it (${join(own, 'research', 'SKILL.md')})\n`));
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
  assert.equal(engineer?.prompt, '## Task t1, from the Manager\nBuild the CSV summary tool.\nsummary.py prints the column totals.');
  assert.match(engineer?.instructions ?? '', /^You are the Argus Engineer[\s\S]*## Where things are\n/);
  assert.deepEqual([reviewer?.form, reviewer?.access], ['review', 'read']);
  assert.match(reviewer?.prompt ?? '', /^## Task t1[\s\S]*## From the Engineer\nsummary\.py added; run python summary\.py data\.csv$/);
  assert.match(reviewer?.instructions ?? '', /^You are the Argus Reviewer/);
  assert.equal(manager?.resume, 'manager-1');
  assert.equal(manager?.prompt, 'From the Reviewer, on task t1 (Build the CSV summary tool.):\nAccepted: the totals match the data.');
  assert.deepEqual(events().map(event => event.type), [
    'message', 'call.start', 'call.end', 'handoff', 'task.added', 'status', 'call.start', 'call.end', 'handoff',
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

test("the Engineer's question holds up its task until the operator answers", async t => {
  const question = 'Round the totals, or reject the bad rows?';
  const steps: Step[] = [toEngineer, { role: 'engineer', to: 'operator', letter: question }];
  const { store, flow, id, requests } = await setup(t, steps);
  const asking = await say(flow, id, 'Make me a CSV summary tool');
  assert.equal(asking.status, 'asking');
  assert.deepEqual(asking.questions, [{ role: 'engineer', task: 't1', question }]);
  // The operator may talk to the Manager meanwhile.
  steps.push({ role: 'manager', to: 'operator', letter: 'The team needs your choice first.' });
  assert.equal((await say(flow, id, 'Also export a PDF')).status, 'asking');
  assert.equal(requests.length, 3);
  steps.push({ role: 'engineer', to: 'reviewer', letter: 'rounded' }, { role: 'reviewer', to: 'manager', letter: 'ok' }, report);
  const rested = await answer(flow, id, asking.questions[0]!, 'round');
  assert.deepEqual([rested.status, rested.questions], ['idle', []]);
  assert.equal(requests[3]?.resume, 'engineer-2');
  assert.equal(requests[3]?.prompt, `## From the operator\nYou asked the operator: ${question}\nThey answered: round`);
  assert.throws(() => flow.answer(id, asking.questions[0]!, 'again'), /no such open question/);
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
  assert.match(planner?.prompt ?? '', /## Tasks\nThe next task opened is t1\.\n\n## Since your last turn\nFrom the Manager:\nBuild the CSV summary tool\.\nsummary\.py prints the column totals\.$/);
  assert.match(parse?.prompt ?? '', /## Task t1, from the Planner\nParse data\.csv\nCheck: the parser tests pass\.$/);
  assert.equal(replan?.resume, 'planner-2');
  assert.equal(replan?.prompt, '## Tasks\n- t1 [closed] Parse data.csv\nThe next task opened is t2.\n\n## Since your last turn\nFrom the Reviewer, on task t1 (Parse data.csv):\nAccepted: the parser reads every row.');
  assert.match(report2?.prompt ?? '', /## Earlier tasks\n- t1 Parse data\.csv\. Its last word, from the Reviewer: Accepted: the parser reads every row\.\n\n## Task t2, from the Planner\n/);
  assert.equal(manager?.prompt, 'From the Planner:\nThe summary tool is built and checked.');
  assert.deepEqual(project.inbox, []);
});

test("tasks the Planner opens together run side by side; one's question holds up only that one", async t => {
  let release = (): void => {};
  const held = new Promise<void>(resolve => { release = resolve; });
  const steps: Step[] = [
    { role: 'manager', to: 'planner', letter: brief },
    { role: 'planner', to: 'engineer', letters: ['Parse data.csv', 'Parse data2.csv'] },
    { role: 'engineer', to: 'operator', letter: 'Which delimiter?' },
    { role: 'engineer', waits: held, to: 'reviewer', letter: 'parsed 2' },
    { role: 'reviewer', to: 'planner', letter: 'Accepted 2.' },
    // The Planner reads t2's letter at once, while t1 waits for its answer.
    { role: 'planner', to: 'planner', letter: 'Nothing to open till t1 ends.' },
  ];
  const { flow, id, requests } = await setup(t, steps);
  await flow.say(id, 'Summarise data.csv and data2.csv');
  while (!flow.store.load(id).questions.length) await new Promise(resolve => setImmediate(resolve));
  // t1 waits for the answer while t2 goes on.
  assert.equal(flow.store.load(id).status, 'working');
  release();
  const asking = await flow.settled(id);
  assert.deepEqual([asking.status, asking.tasks.map(task => task.status), asking.inbox], ['asking', ['open', 'closed'], []]);
  assert.match(requests[3]?.prompt ?? '', /## Running beside it, in the same workspace\n- t1 Parse data\.csv\n\n## Task t2, from the Planner\nParse data2\.csv$/);
  assert.equal(requests[5]?.prompt, '## Tasks\n- t1 [open, with the Engineer] Parse data.csv\n- t2 [closed] Parse data2.csv\nThe next task opened is t3.\n\n'
    + '## Since your last turn\nFrom the Reviewer, on task t2 (Parse data2.csv):\nAccepted 2.');
  steps.push(
    { role: 'engineer', to: 'reviewer', letter: 'parsed 1' },
    { role: 'reviewer', to: 'planner', letter: 'Accepted 1.' },
    { role: 'planner', to: 'manager', letter: 'Both files are parsed.' },
    report,
  );
  assert.equal((await answer(flow, id, asking.questions[0]!, 'comma')).status, 'idle');
  assert.equal(requests[8]?.prompt, '## Tasks\n- t1 [closed] Parse data.csv\n- t2 [closed] Parse data2.csv\nThe next task opened is t3.\n\n'
    + '## Since your last turn\nFrom the Reviewer, on task t1 (Parse data.csv):\nAccepted 1.');
});

test('a task the Planner opens waits for the tasks it names, and starts once they have all closed', async t => {
  const { flow, id, requests, events } = await setup(t, [
    { role: 'manager', to: 'planner', letter: brief },
    { role: 'planner', to: 'engineer', letters: ['Train the baseline', 'Train the method', 'Compare the two'], after: [[], [], ['t1', 't2', 't9', 't3']] },
    { role: 'engineer', to: 'planner', letter: 'baseline trained' },
    { role: 'engineer', later: true, to: 'planner', letter: 'method trained' },
    // The Planner reads the first letter while the method still trains.
    { role: 'planner', to: 'planner', letter: 'The comparison waits for the method.' },
    { role: 'engineer', to: 'planner', letter: 'The method beats the baseline.', open: ['results/compare.md'] },
    { role: 'planner', to: 'planner', letter: 'The comparison runs.' },
    { role: 'planner', to: 'manager', letter: 'Done.' },
    report,
  ]);
  const project = await say(flow, id, 'Does the method beat the baseline?');
  // Only tasks opened before it are waited for: a later or unknown one is none.
  assert.deepEqual(project.tasks.map(task => [task.id, task.after ?? []]), [['t1', []], ['t2', []], ['t3', ['t1', 't2']]]);
  const turns = requests.map(request => request.role === 'engineer' ? /## Task (t\d)/.exec(request.prompt)?.[1] : request.role);
  assert.deepEqual(turns, ['manager', 'planner', 't1', 't2', 'planner', 't3', 'planner', 'planner', 'manager']);
  assert.equal(requests[4]?.prompt.split('\n\n## Since')[0], '## Tasks\n- t1 [closed] Train the baseline\n- t2 [open, with the Engineer] Train the method\n'
    + '- t3 [open, waits for t2] Compare the two\nThe next task opened is t4.');
  // The letter that ends a task with a result names its files, kept in the record beside it.
  assert.deepEqual(events().flatMap(event => event.type === 'handoff' && event.task === 't3' ? [event.open] : []), [['results/compare.md']]);
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
  // The turn the stop cut off goes on in its session.
  assert.deepEqual([next.requests[0]?.resume, next.requests[0]?.prompt], ['manager-1', 'What is 6 times 7?']);
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

test('a failed turn holds up only its task: the rest goes on, and the project shows it failed once nothing else does', async t => {
  const steps: Step[] = [
    { role: 'manager', to: 'planner', letter: brief },
    { role: 'planner', to: 'engineer', letters: ['Parse data.csv', 'Parse data2.csv'] },
    { role: 'engineer', throws: 'spawn codex ENOENT' },
    { role: 'engineer', later: true, to: 'planner', letter: 'data2.csv parsed' },
    { role: 'planner', to: 'planner', letter: 'Waiting for data.csv.' },
  ];
  const { flow, id, requests } = await setup(t, steps);
  const failed = await say(flow, id, 'Summarise data.csv and data2.csv');
  assert.deepEqual([failed.status, failed.error, failed.failures], ['failed', 'spawn codex ENOENT', [{ role: 'engineer', task: 't1', error: 'spawn codex ENOENT' }]]);
  assert.deepEqual(failed.tasks.map(task => task.status), ['open', 'closed']);
  // The operator resumes the work: the failed task goes on, and nothing else is done again.
  steps.push({ role: 'engineer', to: 'planner', letter: 'data.csv parsed' }, { role: 'planner', to: 'manager', letter: 'Both parsed.' }, report);
  flow.resume(id);
  const project = await flow.settled(id);
  assert.deepEqual([project.status, project.error, project.failures], ['idle', null, []]);
  assert.deepEqual(requests.map(request => request.role), ['manager', 'planner', 'engineer', 'engineer', 'planner', 'engineer', 'planner', 'manager']);
});

test('questions wait side by side, and each answer goes to its own asker', async t => {
  const steps: Step[] = [
    { role: 'manager', to: 'planner', letter: brief },
    { role: 'planner', to: 'engineer', letters: ['Parse data.csv', 'Parse data2.csv'] },
    { role: 'engineer', to: 'operator', letter: 'Which delimiter?' },
    { role: 'engineer', to: 'operator', letter: 'Which encoding?' },
  ];
  const { flow, id, requests } = await setup(t, steps);
  const asking = await say(flow, id, 'Summarise data.csv and data2.csv');
  assert.deepEqual([asking.status, asking.questions], ['asking', [
    { role: 'engineer', task: 't1', question: 'Which delimiter?' }, { role: 'engineer', task: 't2', question: 'Which encoding?' },
  ]]);
  steps.push({ role: 'engineer', to: 'reviewer', letter: 'parsed 2' }, { role: 'reviewer', to: 'planner', letter: 'Accepted 2.' },
    { role: 'planner', to: 'planner', letter: 'Waiting for t1.' });
  const one = await answer(flow, id, asking.questions[1]!, 'UTF-8');
  assert.deepEqual([one.status, one.questions.map(question => question.task)], ['asking', ['t1']]);
  assert.equal(requests[4]?.prompt, '## From the operator\nYou asked the operator: Which encoding?\nThey answered: UTF-8');
});

test("a task's turns name its giver, so the Planner hears only of the tasks it gave", async t => {
  const { flow, id, requests } = await setup(t, [
    toEngineer,
    { role: 'engineer', to: 'reviewer', letter: 'summary.py added' },
    { role: 'reviewer', to: 'manager', letter: 'Accepted.' },
    report,
  ]);
  await say(flow, id, 'Make me a CSV summary tool');
  assert.deepEqual(requests.map(request => request.giver), [undefined, 'manager', 'manager', undefined]);
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
  assert.match(requests[3]?.prompt ?? '', /- t1 \[closed\] Parse data\.csv\nThe next task opened is t2\.\n\n## Since your last turn\nFrom the Engineer, on task t1 \(Parse data\.csv\):\nThe data has no totals column\.$/);
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
  // The Reviewer the restart cut off goes on in its session, with the letter it was reading.
  assert.equal(next.requests[1]?.resume, 'reviewer-3');
  assert.match(next.requests[1]?.prompt ?? '', /## From the Engineer\nsummary\.py added$/);
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
