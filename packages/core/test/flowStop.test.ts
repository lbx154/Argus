// A stop and a letter put into a turn under way, as they race: from a backend review's reproductions.
import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { readFileSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AgentRequest, AgentResult, CallAgent } from '../src/types.js';
import { Store, type FlowEvent } from '../src/store.js';
import { Flow } from '../src/flow.js';

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
  assert.deepEqual([said(), store.load(id).forManager], [['First', 'Second: use the newer model'], ['First', 'Second: use the newer model']]);
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
  assert.deepEqual([asked.length, project.status, project.tasks.length, project.forManager], [0, 'stopped', 2, ['Rewrite it all in Rust']]);
});
