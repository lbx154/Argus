import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import type { RunnerResult, RunnerStreamEvent } from '@argus/contracts';
import { PiBackend, buildPiCommand, type PiRunRequest, type PiBackendOptions } from '../src/pi.js';

const fixturePath = fileURLToPath(new URL('./fixtures/fake-pi.mjs', import.meta.url));
const request: PiRunRequest = { prompt: '你好 🌍', cwd: process.cwd(), toolPolicy: 'disabled', wallTimeoutMs: 5_000, idleTimeoutMs: 2_000 };
function backend(mode: string, options: PiBackendOptions = {}): PiBackend {
  return new PiBackend({ executable: process.execPath, prefixArgs: [fixturePath, mode], terminateGraceMs: 100, ...options });
}
async function collect(runner: PiBackend, input: PiRunRequest = request): Promise<{ result: RunnerResult; events: RunnerStreamEvent[] }> {
  const events: RunnerStreamEvent[] = [];
  let result: RunnerResult | undefined;
  for await (const event of runner.run(input)) {
    events.push(event);
    if (event.type === 'result') result = event.result;
  }
  assert.ok(result, 'stream must terminate with a receipt');
  assert.equal(events.filter(event => event.type === 'result').length, 1);
  return { result, events };
}
async function assertTerminated(pid: number): Promise<void> {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try { process.kill(pid, 0); } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ESRCH') return;
      throw error;
    }
    // Minimal containers may leave reaping to a slow PID 1; a zombie has exited.
    if (process.platform === 'linux') {
      try { if (/^State:\s+Z/m.test(await readFile(`/proc/${pid}/status`, 'utf8'))) return; }
      catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return; throw error; }
    }
    await sleep(20);
  }
  assert.fail(`owned process ${pid} is still running`);
}

test('chunked UTF-8, CRLF, unterminated final line and stdin prompt round-trip', { timeout: 10_000 }, async () => {
  const { result, events } = await collect(backend('chunked'));
  assert.equal(result.turnCompleted, true);
  assert.equal(result.turnFailed, false);
  assert.equal(result.fatalError, null);
  assert.equal(result.threadId, 'pi-fixture-session');
  assert.deepEqual(result.agentMessages, [`answer: ${request.prompt}`]);
  assert.equal(result.providerTurns, 1);
  assert.equal(result.toolActivityObserved, true);
  assert.equal(result.stderrLineCount, 1);
  assert.equal(result.stdoutLineCount, 7);
  assert.equal(result.jsonEventCount, 7);
  assert.ok(!result.command.includes(request.prompt));
  const echo = events.find(item => item.type === 'provider_event' && item.event.type === 'request');
  assert.ok(echo?.type === 'provider_event');
  assert.equal(echo.event.prompt, request.prompt);
  assert.deepEqual(echo.event.args, buildPiCommand(request));
});

test('tool policy, model/provider and resume are explicit, with ambient resources disabled', () => {
  const args = buildPiCommand({ ...request, toolPolicy: 'read-only', model: 'model', provider: 'provider',
    sessionDirectory: '/tmp/argus sessions', resumeSession: 'previous' });
  assert.ok(args.includes('--no-extensions'));
  assert.equal(args[args.indexOf('--tools') + 1], 'read,grep,find,ls');
  assert.equal(args[args.indexOf('--model') + 1], 'provider/model');
  assert.equal(args[args.indexOf('--session') + 1], 'previous');
  assert.throws(() => buildPiCommand({ ...request, resumeSession: 'previous' }), /sessionDirectory/);
  assert.throws(() => buildPiCommand({ ...request, prompt: '' }), /prompt/);
});

for (const mode of ['incomplete', 'nonzero']) {
  test(`does not report success after ${mode} exit`, async () => {
    const { result } = await collect(backend(mode));
    assert.equal(result.turnCompleted, false);
    assert.equal(result.turnFailed, true);
    assert.ok(result.fatalError);
    assert.equal(result.exitCode, mode === 'nonzero' ? 7 : 0);
  });
}

test('spawn failure produces a terminal receipt', async () => {
  const { result } = await collect(new PiBackend({ executable: 'argus-test-nonexistent-command' }));
  assert.equal(result.stopKind, 'transport_error');
  assert.equal(result.turnFailed, true);
  assert.match(result.fatalError ?? '', /ENOENT/);
});

test('pre-aborted request never spawns', async () => {
  const { result, events } = await collect(backend('hang'), { ...request, signal: AbortSignal.abort() });
  assert.equal(result.stopKind, 'cancelled');
  assert.equal(events.length, 1);
  assert.equal(result.exitCode, null);
});

test('cancellation terminates a process that ignores SIGTERM', { timeout: 10_000 }, async () => {
  const controller = new AbortController();
  let pid: number | undefined;
  let result: RunnerResult | undefined;
  for await (const event of backend('hang').run({ ...request, signal: controller.signal })) {
    if (event.type === 'provider_event' && event.event.type === 'ready') {
      pid = Number(event.event.pid);
      controller.abort();
    }
    if (event.type === 'result') result = event.result;
  }
  assert.ok(pid);
  assert.equal(result?.stopKind, 'cancelled');
  await assertTerminated(pid);
});

test('closing a consumer also cleans up its process', { timeout: 10_000 }, async () => {
  let pid: number | undefined;
  for await (const event of backend('hang').run(request)) {
    if (event.type === 'provider_event' && event.event.type === 'ready') { pid = Number(event.event.pid); break; }
  }
  assert.ok(pid);
  await assertTerminated(pid);
});

test('idle timeout fires while a provider is silent', { timeout: 10_000 }, async () => {
  const { result } = await collect(backend('hang'), { ...request, idleTimeoutMs: 500 });
  assert.equal(result.stopKind, 'idle_timeout');
});
test('wall deadline fires even when output keeps arriving', { timeout: 10_000 }, async () => {
  const { result } = await collect(backend('heartbeat'), { ...request, wallTimeoutMs: 700 });
  assert.equal(result.stopKind, 'wall_timeout');
});

for (const mode of ['line-limit', 'queue-limit', 'text-limit']) {
  test(`bounds memory for ${mode}`, { timeout: 10_000 }, async () => {
    const options = mode === 'line-limit' ? { maxLineBytes: 100 } : mode === 'queue-limit' ? { maxBufferedBytes: 1024 } : {};
    const { result } = await collect(backend(mode, options), { ...request, maxRetainedTextBytes: 100 });
    assert.equal(result.stopKind, 'output_limit');
    assert.equal(result.turnCompleted, false);
  });
}

for (const mode of ['descendant', 'inherited-pipe']) {
  test(`reclaims POSIX ${mode} after parent exit`, { skip: process.platform === 'win32', timeout: 10_000 }, async () => {
    const { result, events } = await collect(backend(mode));
    const event = events.find(item => item.type === 'provider_event' && item.event.type === 'descendant');
    assert.ok(event?.type === 'provider_event');
    await assertTerminated(Number(event.event.pid));
    assert.equal(result.stopKind, mode === 'inherited-pipe' ? 'transport_error' : null);
  });
}

test('independent forks do not share parser state', async () => {
  const original = backend('success');
  const [first, second] = await Promise.all([
    collect(original, { ...request, prompt: 'first' }),
    collect(original.fork(), { ...request, prompt: 'second' }),
  ]);
  assert.deepEqual(first.result.agentMessages, ['answer: first']);
  assert.deepEqual(second.result.agentMessages, ['answer: second']);
});
