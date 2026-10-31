// New reproduction of the cleared review-r23 planner-batch scenario, through real Flow.
// The controlled backend gates turns; all events and state are produced by Flow/Store.
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Flow } from '../src/flow.js';
import { Store } from '../src/store.js';
import type { CallAgent } from '../src/types.js';
import { readEvents } from '../src/serve/views.js';

const gate = () => {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
};

export async function plannerBatchReplay({ reverse = false, correctSecond = false }: { reverse?: boolean; correctSecond?: boolean } = {}) {
  const home = mkdtempSync(join(tmpdir(), 'argus-planner-batch-'));
  const store = new Store(home);
  const project = store.create({ request: '', workdir: home, backend: 'codex', model: null });
  const second = gate(), finishSecond = gate(), t2 = gate(), t2Done = gate(), batch = gate(), finishBatch = gate();
  let planner = 0;
  const prompts: string[] = [];
  const agent: CallAgent = async request => {
    const result = (to: string, letters: string[]) => ({ callId: 'controlled', threadId: 'session', completed: true, error: null,
      handoff: { form: request.form, to, letters }, model: 'fixture', usage: null, ms: 1 });
    if (request.role === 'manager') {
      if (request.prompt.includes('请求B')) return result('planner', ['计划B']);
      return result('planner', ['计划A']);
    }
    if (request.role === 'engineer') {
      if (request.session?.includes('/t2.')) { await t2.promise; t2Done.release(); }
      return result('planner', ['任务完成']);
    }
    assert.equal(request.role, 'planner');
    prompts.push(request.prompt);
    planner++;
    if (correctSecond && planner === 3) {
      assert.doesNotMatch(request.prompt, /计划B/, 'form correction does not read new inbox letters');
      request.onStep?.({ kind: 'message', text: 't1 修正表单。' });
      return result('planner', ['继续读信']);
    }
    switch (correctSecond && planner > 3 ? planner - 1 : planner) {
      case 1:
        request.onStep?.({ kind: 'message', text: '我把 A 拆成两个任务。' });
        return result('engineer', ['a1.py', 'a2.py']);
      case 2:
        second.release(); await finishSecond.promise;
        request.onStep?.({ kind: 'message', text: 't1 收到。' });
        return correctSecond ? { ...result('planner', []), handoff: null, error: 'Missing form' } : result('planner', ['继续读信']);
      case 3:
        batch.release(); await finishBatch.promise;
        request.onStep?.({ kind: 'message', text: '我来安排 B 的 b.py 任务。' });
        return result('engineer', ['b.py']);
      case 4:
        request.onStep?.({ kind: 'message', text: 'b.py 收到，B 完成。' });
        return result('planner', ['完成']);
      default: throw new Error('unexpected planner turn');
    }
  };
  const flow = new Flow(store, agent);
  try {
    await flow.say(project.id, '请求A'); await second.promise;
    if (reverse) await flow.say(project.id, '请求B');
    t2.release(); await t2Done.promise;
    // Await the task's accepted letter, not merely the backend returning it.
    await new Promise<void>(resolve => setImmediate(resolve));
    if (!reverse) await flow.say(project.id, '请求B');
    finishSecond.release(); await batch.promise;
    const during = readEvents(store, project.id);
    finishBatch.release(); await flow.settled(project.id);
    assert.match(prompts[correctSecond ? 3 : 2]!, /From the Engineer, on task t2/);
    assert.match(prompts[correctSecond ? 3 : 2]!, /From the Manager:\n计划B/);
    return { record: readEvents(store, project.id), project: store.load(project.id), during, prompts };
  } finally {
    finishSecond.release(); t2.release(); finishBatch.release();
    await flow.settled(project.id);
    rmSync(home, { recursive: true, force: true });
  }
}
