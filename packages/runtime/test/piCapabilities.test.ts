import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import type { JsonObject, RunnerResult, RunnerStreamEvent } from '@argus/contracts';
import { PiBackend, buildPiCommand, type PiRunRequest } from '../src/pi.js';
import { encodeOutputSchema } from '../src/piConfiguration.js';

const fixture = fileURLToPath(new URL('./fixtures/fake-pi.mjs', import.meta.url));
const schema = { type: 'object', properties: { answer: { $ref: '#/$defs/answer' } },
  required: ['answer'], additionalProperties: false, $defs: { answer: { type: 'string' } } };
const request: PiRunRequest = { prompt: 'fixture', cwd: process.cwd(), toolPolicy: 'disabled',
  model: 'gpt-5.6-sol', provider: 'openai', idleTimeoutMs: 3000, wallTimeoutMs: 5000 };
function backend(mode: string, env?: NodeJS.ProcessEnv): PiBackend {
  return new PiBackend({ executable: process.execPath, prefixArgs: [fixture, mode], terminateGraceMs: 100, ...(env ? { env } : {}) });
}
async function collect(runner: PiBackend, input: PiRunRequest) {
  const events: RunnerStreamEvent[] = [];
  let result: RunnerResult | undefined;
  for await (const event of runner.run(input)) {
    events.push(event);
    if (event.type === 'result') result = event.result;
  }
  assert.ok(result);
  return { events, result };
}
function eventOf(events: RunnerStreamEvent[], type: string): JsonObject {
  const found = events.find(event => event.type === 'provider_event' && event.event.type === type);
  assert.ok(found?.type === 'provider_event');
  return found.event;
}

for (const mode of ['structured-completions', 'structured-responses']) {
  test(`schema transport preserves references and per-call isolation for ${mode}`, async () => {
    const runner = backend(mode, { ...process.env, ARGUS_PI_OUTPUT_SCHEMA: 'ambient must not apply' });
    const other = { ...schema, description: 'second invocation' };
    const calls = await Promise.all([collect(runner, { ...request, outputSchema: schema }), collect(runner.fork(), { ...request, outputSchema: other })]);
    for (let index = 0; index < calls.length; index += 1) {
      const call = calls[index]!;
      assert.equal(call.result.turnCompleted, true);
      assert.deepEqual(call.result.agentMessages, ['{"answer":"fixture"}']);
      assert.ok(!call.result.command.includes(JSON.stringify(schema)));
      const event = eventOf(call.events, 'request_payload');
      assert.equal(event.schemaEnvRemoved, true);
      const payload = event.payload as JsonObject;
      const format = mode === 'structured-responses' ? (payload.text as JsonObject).format as JsonObject
        : (payload.response_format as JsonObject).json_schema as JsonObject;
      assert.deepEqual(format.schema, index ? other : schema);
      assert.equal(format.strict, true);
      if (mode === 'structured-responses') assert.equal((payload.text as JsonObject).verbosity, 'low');
    }
  });
}

for (const mode of ['structured-unsupported', 'structured-tools']) {
  test(`${mode} fails before sending a fallback provider payload`, async () => {
    const { result, events } = await collect(backend(mode), { ...request, outputSchema: schema });
    assert.equal(result.turnCompleted, false);
    assert.equal(result.exitCode, 1);
    assert.ok(events.some(event => event.type === 'line' && event.stream === 'stderr' && event.line.includes('Argus structured output')));
    assert.ok(!events.some(event => event.type === 'provider_event' && event.event.type === 'request_payload'));
  });
}

test('invalid schemas and incompatible tool policy fail before provider startup', async () => {
  const cycle: JsonObject = {}; cycle.self = cycle;
  for (const invalid of [[], null, { value: undefined }, { value: NaN }, { value: Infinity }, { value: 1n }, { value: new Date() }, cycle]) {
    assert.throws(() => encodeOutputSchema(invalid), /outputSchema/);
  }
  assert.throws(() => encodeOutputSchema({ description: 'x'.repeat(65_536) }), /64 KiB/);
  await assert.rejects(collect(backend('environment'), { ...request, toolPolicy: 'read-only', outputSchema: schema }), /requires toolPolicy disabled/);
});

test('trusted plugin arguments and environment stay explicit and local to a call', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'argus-plugin-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const extension = join(directory, 'plugin with spaces.mjs');
  await writeFile(extension, 'export default () => {};');
  const pluginRequest: PiRunRequest = { ...request, toolPolicy: 'read-only', trustedExtensions: [extension],
    trustedToolNames: ['plugin_inspect', 'plugin_inspect'], extensionEnv: { ARGUS_PLUGIN_TEST: 'first' } };
  const env = { ...process.env, ARGUS_PI_OUTPUT_SCHEMA: 'must be removed' };
  const runner = backend('environment', env);
  const [enabled, disabled] = await Promise.all([
    collect(runner, pluginRequest), collect(runner.fork(), { ...pluginRequest, toolPolicy: 'disabled' }),
  ]);
  const first = eventOf(enabled.events, 'request_environment');
  const second = eventOf(disabled.events, 'request_environment');
  assert.equal(first.plugin, 'first');
  assert.equal(second.plugin, null);
  assert.equal(first.schema, null);
  assert.equal(second.schema, null);
  assert.equal(env.ARGUS_PI_OUTPUT_SCHEMA, 'must be removed');
  const args = first.args as string[];
  assert.equal(args[args.indexOf('--extension') + 1], extension);
  assert.equal(args[args.indexOf('--tools') + 1], 'read,grep,find,ls,plugin_inspect');
  assert.ok(!(second.args as string[]).includes('--extension'));
  assert.ok(args.includes('--no-extensions'));
  assert.throws(() => buildPiCommand({ ...pluginRequest, extensionEnv: { PATH: 'replacement' } }), /extensionEnv/);
  assert.throws(() => buildPiCommand({ ...pluginRequest, trustedToolNames: ['inspect,bash'] }), /trustedToolNames/);
  assert.throws(() => buildPiCommand({ ...pluginRequest, trustedToolNames: ['write'] }), /trustedToolNames/);
  assert.throws(() => buildPiCommand({ ...pluginRequest, trustedExtensions: ['relative.mjs'] }), /absolute files/);
});

test('provider-turn allowance retains work and charges all observed usage', async () => {
  const { result } = await collect(backend('turn-cap-paced'), { ...request, providerTurnCap: 2 });
  assert.equal(result.providerTurnCapHit, true);
  assert.ok(result.providerTurns >= 2);
  assert.equal(result.stopKind, 'provider_turn_limit');
  assert.equal(result.turnCompleted, false);
  assert.deepEqual(result.agentMessages.slice(0, 2), ['checkpoint 1', 'checkpoint 2']);
  assert.ok(Math.abs(result.accounting.pricing.cost_usd! - result.providerTurns * 0.1) < 1e-10);
  assert.equal(result.accounting.pricing.status, 'partial');
});

test('zero and larger allowances preserve normal completion', async () => {
  for (const providerTurnCap of [0, 10]) {
    const { result } = await collect(backend('turn-cap-exited'), { ...request, providerTurnCap });
    assert.equal(result.turnCompleted, true);
    assert.equal(result.providerTurnCapHit, false);
    assert.equal(result.providerTurns, 2);
  }
  for (const providerTurnCap of [-1, 0.5, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => buildPiCommand({ ...request, providerTurnCap }), /providerTurnCap/);
  }
});

test('buffered messages from an already closed process do not trigger an allowance stop', async () => {
  const stream = backend('turn-cap-exited').run({ ...request, providerTurnCap: 1 });
  const first = await stream.next();
  assert.ok(first.value?.type === 'line');
  const { pid } = JSON.parse(first.value.line) as { pid: number };
  const deadline = Date.now() + 5000;
  while (true) {
    try {
      process.kill(pid, 0);
      if (process.platform === 'linux' && /^State:\s+Z/m.test(await readFile(`/proc/${pid}/status`, 'utf8'))) break;
    } catch (error) {
      if (['ESRCH', 'ENOENT'].includes((error as NodeJS.ErrnoException).code ?? '')) break;
      throw error;
    }
    assert.ok(Date.now() < deadline);
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  let result: RunnerResult | undefined;
  for await (const event of stream) if (event.type === 'result') result = event.result;
  assert.equal(result?.turnCompleted, true);
  assert.equal(result?.providerTurnCapHit, false);
});
