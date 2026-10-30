// A stop and a letter put into a turn under way, as they race: from a backend review's reproductions.
import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { readFileSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AgentRequest, AgentResult, CallAgent } from '../src/types.js';
import { Store, type FlowEvent, type ManagerLetter } from '../src/store.js';

/** The letters waiting for the Manager, as their words. */
const texts = (letters: ManagerLetter[] = []) => letters.map(letter => typeof letter === 'string' ? letter : letter.text);
import { Flow } from '../src/flow.js';
import { conversationRows, progressMap, readRecord, snapshot, type Live } from '../src/serve/views.js';

type Turn = (request: AgentRequest) => Promise<{ to: string; letters: string[]; tasks?: { task: string; after: string[] }[] } | null>;

function agent(turns: Turn[]) {
  const requests: AgentRequest[] = [];
  const callAgent: CallAgent = async request => {
    requests.push(request);
    const turn = turns.shift();
    assert.ok(turn, `no turn scripted for the ${request.role}`);
    const out = await turn(request);
    const aborted = request.signal?.aborted ?? false;
    const handoff = out && !aborted ? { form: request.form, ...out } : null;
    return {
      callId: `c${requests.length}`, threadId: `${request.role}-${requests.length}`, completed: !aborted && !!handoff,
      error: aborted ? 'Run cancelled.' : handoff ? null : 'none', handoff, model: 'fake', usage: null, ms: 1,
    } as AgentResult;
  };
  return { callAgent, requests };
}

async function setup(t: TestContext, turns: Turn[], controller?: AbortController) {
  const home = await mkdtemp(join(tmpdir(), 'argus-stop-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const { callAgent, requests } = agent(turns);
  const flow = new Flow(store, callAgent, controller ? { signal: controller.signal } : {});
  const { id } = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  const said = (): string[] => readFileSync(join(store.dir(id), 'events.jsonl'), 'utf8').trim().split('\n')
    .map(line => JSON.parse(line) as FlowEvent).flatMap(event => event.type === 'message' ? [event.text] : []);
  return { store, flow, id, requests, said, callAgent };
}

const until = async (ok: () => boolean): Promise<void> => { while (!ok()) await new Promise(resolve => setImmediate(resolve)); };
const aborted: Turn = request => new Promise(resolve => request.signal!.addEventListener('abort', () => resolve(null)));

test("a message waiting behind a Manager turn when the work stops is in the conversation, and reaches the Manager on Resume", async t => {
  const controller = new AbortController();
  const { flow, id, store, said, requests } = await setup(t, [aborted], controller);
  void flow.say(id, 'First');
  await until(() => requests.length === 1);
  const second = flow.say(id, 'Second: use the newer model');
  flow.stop(id);
  controller.abort();
  assert.equal(await second, null);
  await flow.settled(id);
  assert.deepEqual([said(), texts(store.load(id).forManager)], [['First', 'Second: use the newer model'], ['First', 'Second: use the newer model']]);
  const { callAgent, requests: asked } = agent([async () => ({ to: 'operator', letters: ['On the first'] }), async () => ({ to: 'operator', letters: ['On the second'] })]);
  const resumed = new Flow(store, callAgent);
  resumed.resume(id);
  await resumed.settled(id);
  assert.match(asked[1]!.prompt, /Second/);
  assert.deepEqual(said(), ['First', 'Second: use the newer model']);
});

test("a letter for a task that closes while it is being put into the turn goes back to the Planner", async t => {
  let finish = (): void => {};
  const done = new Promise<void>(resolve => { finish = resolve; });
  const { flow, id, store, callAgent, requests } = await setup(t, [
    async () => ({ to: 'planner', letters: ['A baseline'] }),
    async () => ({ to: 'engineer', letters: ['Train the baseline'] }),
    async () => { await done; return { to: 'planner', letters: ['Baseline trained.'] }; },
    async () => ({ to: 'planner', letters: ['The operator wants the newer model.'] }),
    async () => ({ to: 'engineer', letters: ['Use the newer model.'], tasks: [{ task: 't1', after: [] }] }),
    async () => ({ to: 'planner', letters: ['Retrain on the newer model next.'] }),
  ]);
  // The turn ends as the letter is put in, and the backend answers that it did not take it.
  Object.assign(callAgent, { steer: async () => { finish(); await until(() => store.load(id).tasks[0]?.status === 'closed'); return false; } });
  await flow.say(id, 'Train a baseline');
  await until(() => requests.length === 3);
  await flow.say(id, 'Use the newer model');
  const project = await flow.settled(id);
  assert.deepEqual([project.tasks[0]!.status, project.tasks[0]!.unread], ['closed', undefined]);
  assert.match(requests.at(-1)!.prompt, /Your letter to task t1, which closed before it was read:\nUse the newer model\./);
});

test('a letter put into a turn that is then stopped is read at the next turn', async t => {
  const controller = new AbortController();
  let taken = false;
  const { flow, id, store, callAgent, requests } = await setup(t, [
    async () => ({ to: 'planner', letters: ['A baseline'] }),
    async () => ({ to: 'engineer', letters: ['Train the baseline'] }),
    aborted,
    async () => ({ to: 'planner', letters: ['The operator wants the newer model.'] }),
    async () => ({ to: 'engineer', letters: ['Use the newer model.'], tasks: [{ task: 't1', after: [] }] }),
  ], controller);
  Object.assign(callAgent, { steer: async () => { taken = true; return true; } });
  await flow.say(id, 'Train a baseline');
  await until(() => requests.length === 3);
  await flow.say(id, 'Use the newer model');
  await until(() => taken);
  flow.stop(id);
  controller.abort();
  await flow.settled(id);
  assert.deepEqual(store.load(id).tasks[0]!.unread, [{ from: 'planner', text: 'Use the newer model.' }]);
  const { callAgent: next, requests: asked } = agent([async () => ({ to: 'planner', letters: ['Done on the newer model.'] }), async () => ({ to: 'planner', letters: ['Wait.'] })]);
  const resumed = new Flow(store, next);
  resumed.resume(id);
  await resumed.settled(id);
  assert.match(asked[0]!.prompt, /Use the newer model\./);
});

test('an answer on a stopped project goes to its asker, and hands no stopped message to the Manager', async t => {
  const controller = new AbortController();
  const { flow, id, store, requests } = await setup(t, [
    async () => ({ to: 'planner', letters: ['A and B'] }),
    async () => ({ to: 'engineer', letters: ['A', 'B'] }),
    async () => ({ to: 'operator', letters: ['Which data set for A?'] }),
    aborted,
    aborted,
  ], controller);
  await flow.say(id, 'Do two things');
  await until(() => requests.length === 4 && store.load(id).questions.length === 1);
  void flow.say(id, 'Rewrite it all in Rust');
  await until(() => requests.length === 5);
  flow.stop(id);
  controller.abort();
  await flow.settled(id);
  const stopped = store.load(id);
  const { callAgent, requests: asked } = agent([]);
  const later = new Flow(store, callAgent);
  later.answer(id, stopped.questions[0]!, 'data.csv');
  const project = await later.settled(id);
  assert.deepEqual([asked.length, project.status, project.tasks.length, texts(project.forManager)], [0, 'stopped', 2, ['Rewrite it all in Rust']]);
});

// Frontend review reproduction: stopped during "sleep 30", the next message re-ran it first.
test('a message to a stopped project is answered on its own, in place of the one the stop interrupted', async t => {
  const controller = new AbortController();
  const { flow, id, store, requests } = await setup(t, [aborted], controller);
  void flow.say(id, 'Run sleep 30');
  await until(() => requests.length === 1);
  flow.stop(id);
  controller.abort();
  await flow.settled(id);
  const { callAgent, requests: asked } = agent([async () => ({ to: 'operator', letters: ['ok'] })]);
  const later = new Flow(store, callAgent);
  await later.say(id, 'Answer in one word');
  const project = await later.settled(id);
  assert.deepEqual([asked.map(request => request.prompt.includes('Answer in one word')), project.status, texts(project.forManager)],
    [[true], 'stopped', []]);
});

// Product review reproduction: told "do X instead" after a stop, Resume still carried out the stopped
// message; a backend review saw it "replying" for ever.
test('a message that sets a stopped team working again takes the place of the one the stop interrupted', async t => {
  const controller = new AbortController();
  const { flow, id, store, requests } = await setup(t, [aborted], controller);
  void flow.say(id, 'A: analyse it');
  await until(() => requests.length === 1);
  flow.stop(id);
  controller.abort();
  await flow.settled(id);
  const respond: Turn = async request => request.role === 'engineer' ? { to: 'manager', letters: ['X done'] }
    : request.prompt.includes('B: do X') ? { to: 'engineer', letters: ['Do X'] } : { to: 'operator', letters: ['Reply'] };
  const { callAgent, requests: asked } = agent(Array.from({ length: 6 }, () => respond));
  const later = new Flow(store, callAgent);
  await later.say(id, 'B: do X instead');
  const project = await later.settled(id);
  assert.deepEqual([project.status, texts(project.forManager)], ['idle', []]);
  assert.ok(!asked.some(request => request.role === 'manager' && request.prompt.includes('A: analyse it')));
  // The conversation says so: the stopped message was replaced, and nothing is still replying to it.
  const rows = conversationRows(readRecord(store, id), project, { since: null, calls: [], requests: [] });
  assert.deepEqual(rows.flatMap(row => row.type === 'ui.argus' && row.status ? [row.status] : []), ['replaced']);
});

const idle: Live = { since: null, calls: [], requests: [] };
const reply = (text: string): Turn => async () => ({ to: 'operator', letters: [text] });
const replies = (store: Store, id: string, live = idle) => conversationRows(readRecord(store, id), store.load(id), live)
  .filter(row => row.type === 'ui.argus' && row.status).map(row => [row.request_id, row.status]);
const cards = (store: Store, id: string) => progressMap(store, store.load(id), 0).tasks.map(card => [card.objective, card.status]);

test('a message a later one took the place of leaves the map, the original request too', async t => {
  const controller = new AbortController();
  const { flow, id, store, requests } = await setup(t, [aborted], controller);
  void flow.say(id, 'Train the big model on all the data.', { request_id: 'A' });
  await until(() => requests.length === 1);
  flow.stop(id);
  controller.abort();
  await flow.settled(id);
  const next = new Flow(store, agent([reply('Here is a short summary instead.')]).callAgent);
  await next.say(id, 'Never mind, just summarise.', { request_id: 'B' });
  await next.settled(id);
  const resumed = new Flow(store, agent([]).callAgent);
  resumed.resume(id);
  await resumed.settled(id);
  assert.deepEqual([store.load(id).status, replies(store, id)], ['idle', [['A', 'replaced']]]);
  assert.deepEqual(cards(store, id), [['Never mind, just summarise.', 'done']]);
});

test('a later message a stop left unanswered stays off the map after Resume once another took its place', async t => {
  const { store, id } = await setup(t, []);
  const one = new Flow(store, agent([
    async () => ({ to: 'engineer', letters: ['Build the CSV tool'] }), async () => ({ to: 'manager', letters: ['Built.'] }), reply('Done.'),
  ]).callAgent);
  await one.say(id, 'Build a CSV tool', { request_id: 'A' });
  await one.settled(id);
  const controller = new AbortController();
  const second = agent([aborted]);
  const flow = new Flow(store, second.callAgent, { signal: controller.signal });
  void flow.say(id, 'Also chart the totals.', { request_id: 'C' });
  await until(() => second.requests.length === 1);
  flow.stop(id);
  controller.abort();
  await flow.settled(id);
  const next = new Flow(store, agent([reply('Only the summary then.')]).callAgent);
  await next.say(id, 'Forget the chart.', { request_id: 'D' });
  await next.settled(id);
  const resumed = new Flow(store, agent([]).callAgent);
  resumed.resume(id);
  await resumed.settled(id);
  assert.equal(store.load(id).status, 'idle');
  assert.ok(!cards(store, id).some(([objective]) => objective === 'Also chart the totals.'));
});

test('a message sent after another to a stopped project waits on that one, not for Resume', async t => {
  const controller = new AbortController();
  const { flow: first, id, store, requests } = await setup(t, [aborted], controller);
  void first.say(id, 'A', { request_id: 'A' });
  await until(() => requests.length === 1);
  first.stop(id);
  controller.abort();
  await first.settled(id);
  let release = (): void => {};
  const held = new Promise<void>(resolve => { release = resolve; });
  const scripted = agent([async () => { await held; return { to: 'operator', letters: ['On B'] }; }, reply('On C')]);
  const flow = new Flow(store, scripted.callAgent);
  const b = flow.say(id, 'B', { request_id: 'B' });
  await until(() => scripted.requests.length === 1);
  const c = flow.say(id, 'C', { request_id: 'C' });
  const live: Live = { since: 1, calls: [{ role: 'manager', task: undefined, answers: 'B' }], requests: ['B', 'C'] };
  assert.deepEqual(replies(store, id, live), [['A', 'replaced'], ['B', 'running'], ['C', 'waiting']]);
  release();
  await Promise.all([b, c]);
  await flow.settled(id);
  assert.equal(scripted.requests.length, 2);
});

test('a stop while a Manager letter is put into a turn under way keeps the project stopped', async t => {
  let steered = (): void => {};
  let release = (): void => {};
  const inSteer = new Promise<void>(resolve => { steered = resolve; });
  const held = new Promise<boolean>(resolve => { release = () => resolve(true); });
  const controller = new AbortController();
  const { flow, id, store, callAgent, requests } = await setup(t, [
    async () => ({ to: 'engineer', letters: ['Build it'] }),
    aborted,
    async () => ({ to: 'engineer', letters: ['Use the newer model too.'], tasks: [{ task: 't1', after: [] }] }),
  ], controller);
  Object.assign(callAgent, { steer: () => { steered(); return held; } });
  await flow.say(id, 'Build it', { request_id: 'A' });
  await until(() => requests.length === 2);
  const said = flow.say(id, 'Use the newer model', { request_id: 'B' });
  await inSteer;
  flow.stop(id);
  controller.abort();
  release();
  await said;
  await flow.settled(id);
  const project = store.load(id);
  assert.deepEqual([project.status, requests.length], ['stopped', 3]);
  assert.equal(snapshot(store, project, idle).daemon.resumable, true);
});
