import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { Snapshot } from '@argus/contracts';
import type { AgentRequest, CallAgent } from '../src/types.js';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';

/** A cockpit whose Manager answers at once, or, to a message that says `<hang>`, once it is cancelled. */
async function setup(t: TestContext) {
  const home = await mkdtemp(join(tmpdir(), 'argus-projects-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const web = join(home, 'web');
  await mkdir(web);
  await writeFile(join(web, 'manifest.webmanifest'), '{"name":"Argus"}');
  const requests: AgentRequest[] = [];
  const callAgent: CallAgent = async request => {
    requests.push(request);
    if (request.prompt.includes('<hang>')) await new Promise(resolve => request.signal?.addEventListener('abort', resolve, { once: true }));
    const handoff = request.signal?.aborted ? null : { form: request.form, to: 'operator', letters: ['Hello.'] };
    return { callId: 'c', threadId: 'thread', completed: Boolean(handoff), error: handoff ? null : 'interrupted', handoff, model: null, usage: null, ms: 1 };
  };
  const store = new Store(home);
  const served = await serve({ store, callAgent, backend: 'codex', model: null, port: 0, web });
  t.after(() => served.close());
  const call = (path: string, init: RequestInit = {}) => fetch(served.url + path, init);
  const send = (method: string, path: string, body?: unknown) =>
    call(path, { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) });
  const get = async <T = Record<string, unknown>>(path: string): Promise<T> => {
    const response = await call(path);
    assert.equal(response.status, 200, `${path}: ${await response.clone().text()}`);
    return await response.json() as T;
  };
  const create = async (body = {}): Promise<string> => (await (await send('POST', '/api/daemons', body)).json() as { sid: string }).sid;
  /** A Manager turn under way on the project until the returned function cancels it. */
  const busy = async (sid: string): Promise<() => Promise<void>> => {
    const reply = send('POST', `/api/projects/${sid}/message/stream`, { text: '<hang>', request_id: 'r' });
    for (let tries = 0; !(await get<Snapshot>(`/api/projects/${sid}/snapshot`)).daemon.alive; tries++) {
      assert.ok(tries < 200, 'the turn never started');
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    return async () => {
      await send('POST', `/api/projects/${sid}/message/cancel`, { request_id: 'r' });
      await (await reply).text();
      while ((await get<Snapshot>(`/api/projects/${sid}/snapshot`)).daemon.alive) await new Promise(resolve => setTimeout(resolve, 10));
    };
  };
  return { home, store, requests, call, send, get, create, busy };
}

test('a project works in an existing directory, set when it is made or later, while nothing is under way on it', async t => {
  const { home, store, send, get, create, busy } = await setup(t);
  const repo = join(home, 'repo');
  await mkdir(repo);
  assert.ok((await get<{ capabilities: string[] }>('/api/meta')).capabilities.includes('project.workdir.v1'));
  const sid = await create({ workdir: repo });
  assert.equal(store.load(sid).workdir, repo);
  assert.equal((await send('POST', '/api/daemons', { workdir: join(home, 'missing') })).status, 400);

  const other = join(home, 'other');
  await mkdir(other);
  const fresh = await create();
  assert.equal(store.load(fresh).workdir, join(home, 'workspaces', fresh));
  store.save({ ...store.load(fresh), sessions: { manager: 'thread' } });
  for (const workdir of ['', join(home, 'missing'), join(home, 'web', 'manifest.webmanifest')]) {
    assert.equal((await send('POST', `/api/projects/${fresh}/workdir`, { workdir })).status, 400, workdir);
  }
  assert.deepEqual(await (await send('POST', `/api/projects/${fresh}/workdir`, { workdir: other })).json(), { ok: true, workdir: other });
  // The Manager's session worked in the old directory: it starts afresh.
  assert.deepEqual([store.load(fresh).workdir, store.load(fresh).sessions], [other, {}]);
  assert.equal((await get<Snapshot>(`/api/projects/${fresh}/snapshot`)).session.workdir, other);

  const cancel = await busy(fresh);
  assert.equal((await send('POST', `/api/projects/${fresh}/workdir`, { workdir: repo })).status, 409);
  await cancel();
  assert.equal((await send('POST', `/api/projects/${fresh}/workdir`, { workdir: repo })).status, 200);
  assert.equal((await send('POST', '/api/projects/p-00000000/workdir', { workdir: repo })).status, 404);
});
