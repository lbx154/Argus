import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Store } from '../src/store.js';
import { Flow } from '../src/flow.js';
import { applyRoleRuntimes } from '../src/roleRuntime.js';
import { BACKENDS, ROLES, type AgentRequest, type Backend } from '../src/types.js';
import { formSchema } from '../src/agent/handoff.js';
import { cursor, opencode, grok, qoder, openCodeEnvironment } from '../src/agent/headless.js';
import { claudeModels, parseNativeCatalog } from '../src/agent/models.js';
import { instructions } from '../src/prompts.js';

// This tests every ordered pair at the configuration/dispatch boundary. agent.test.ts
// separately runs each of the nine transports against an isolated subprocess fixture.
// Neither test is described as a real provider/account integration test.
test('all 81 ordered CLI pairs preserve four independent routes across a complete delivery and restart', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-cli-matrix-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  for (const first of BACKENDS) for (const second of BACKENDS) await t.test(`${first} → ${second}`, async () => {
    const project = store.create({ workdir: '', request: '', backend: first, model: null });
    const backend: Record<(typeof ROLES)[number], Backend> = { manager: first, planner: second, engineer: first, reviewer: second };
    applyRoleRuntimes(project, Object.fromEntries(ROLES.map(role => [role, { backend: backend[role], model: `${backend[role]}/${role}-model`, effort: `${role}-effort` }])));
    store.save(project);
    const requests: AgentRequest[] = [];
    const recipients = ['planner', 'engineer', 'reviewer', 'planner', 'manager', 'operator'];
    const flow = new Flow(store, async request => {
      requests.push(request);
      return { callId: `c${requests.length}`, threadId: `${request.backend}:${request.role}`, completed: true, error: null, ms: 1,
        model: request.model ?? null, usage: null, handoff: { form: request.form, to: recipients[requests.length - 1]!, letters: ['matrix task'] } };
    });
    await flow.say(project.id, 'Dispatch all four roles');
    const finished = await flow.settled(project.id);
    assert.equal(finished.status, 'idle');
    assert.equal(requests.length, 6);
    for (const request of requests) {
      assert.equal(request.backend, backend[request.role]);
      assert.equal(request.model, `${backend[request.role]}/${request.role}-model`);
      assert.equal(request.effort, `${request.role}-effort`);
      if (request.resume) assert.equal(request.resume, `${request.backend}:${request.role}`, 'never resume another role/CLI session');
    }
    const next: AgentRequest[] = [];
    const restarted = new Flow(new Store(home), async request => {
      next.push(request); return { callId: 'restart', threadId: request.resume ?? null, completed: true, error: null, ms: 1,
        model: request.model ?? null, usage: null, handoff: { form: request.form, to: 'operator', letters: ['resumed'] } };
    });
    await restarted.say(project.id, 'Continue'); await restarted.settled(project.id);
    assert.equal(next[0]!.backend, first);
    assert.equal(next[0]!.resume, `${first}:manager`);
    assert.equal(store.load(project.id).roleConfig?.reviewer?.backend, second);
  });
});

test('legacy CLI dialects retain explicit models and native resume flags, never Claude/Pi-only protocol flags', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-headless-flags-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const adapters = { cursor, opencode, grok, qoder };
  for (const [backend, adapter] of Object.entries(adapters)) {
    const request: AgentRequest = { backend: backend as Backend, role: 'reviewer', form: 'review', access: 'read',
      cwd: home, model: `${backend}/exact-model`, effort: 'high', resume: 'owned-session', prompt: 'Review', callsDir: home, usageFile: join(home, 'usage.jsonl') };
    const conversation = adapter(request, { confined: false, schema: formSchema('review'), stem: join(home, backend) });
    const argv = conversation.args;
    assert.equal(argv.includes('--replay-user-messages'), false);
    assert.equal(argv.includes('--mode') && argv[argv.indexOf('--mode') + 1] === 'json', false);
    assert.equal(argv[argv.indexOf(backend === 'opencode' ? '--session' : '--resume') + 1], 'owned-session');
    assert.equal(argv[argv.indexOf('--model') + 1], backend === 'cursor' ? `${backend}/exact-model[effort=high]` : `${backend}/exact-model`);
    if (backend === 'qoder') { assert.ok(!argv.includes('--verbose')); assert.ok(argv.includes('--reasoning-effort')); assert.ok(argv.includes('Read,Glob,Grep')); }
    if (backend === 'grok') { assert.ok(argv.includes('--prompt-file')); assert.ok(argv.includes('read_file,grep,list_dir')); assert.ok(!argv.includes('--yolo')); }
    if (backend === 'cursor') { assert.ok(argv.includes('ask')); assert.ok(!argv.includes('--force')); }
    if (backend === 'opencode') {
      const config = JSON.parse(conversation.env!.OPENCODE_CONFIG_CONTENT!);
      assert.deepEqual(config.agent['argus-role'].permission, { '*': 'deny', read: 'allow', glob: 'allow', grep: 'allow' });
    }
  }
  assert.equal(JSON.parse(openCodeEnvironment(false).OPENCODE_CONFIG_CONTENT!).agent['argus-role'].permission.edit, 'allow');
});

test('role instructions distinguish host hand-offs from CLI subagents and use the role’s own session lifetime', async t => {
  const home = await mkdtemp(join(tmpdir(), 'argus-role-instructions-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: '', request: '', backend: 'codex', model: null });
  applyRoleRuntimes(project, { engineer: { backend: 'dsh', model: null } });
  for (const role of ROLES) {
    assert.match(instructions(role, project, store), /Do not use your CLI's internal subagent/);
    assert.match(instructions(role, project, store), /Engineer has its own write-enabled workspace call/);
  }
  assert.match(instructions('engineer', project, store), /Your session ends with each turn/);
  assert.match(instructions('manager', project, store), /Your session stays open between your turns/);
});

test('CLI model catalogs accept explicit machine IDs and never turn help/auth errors into fake models', () => {
  assert.deepEqual(parseNativeCatalog('provider/one\nprovider/two\n', true).map(row => row.model), ['provider/one', 'provider/two']);
  assert.deepEqual(parseNativeCatalog('{"models":[{"id":"qoder-max","name":"Max","default":true}]}'), [{ model: 'qoder-max', name: 'Max', default: true }]);
  assert.deepEqual(parseNativeCatalog('Authentication required. Please sign in.\nAvailable models:\n'), []);
  assert.deepEqual(parseNativeCatalog('Models\nhttps://example.invalid\n'), []);
  assert.deepEqual(claudeModels({ account: { email: 'not-for-the-browser' }, models: [
    { value: 'sonnet', displayName: 'Custom mapped model', supportedEffortLevels: ['low', 'high'], privateMetadata: 'not-for-the-browser' },
  ] }), [{ model: 'sonnet', default: false, name: 'Custom mapped model', efforts: ['low', 'high'] }]);
});
