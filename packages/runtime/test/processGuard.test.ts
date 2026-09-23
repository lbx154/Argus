import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { executeProcess, type ProcessExit } from '../src/process.js';

const fixture = fileURLToPath(new URL('./fixtures/fake-process-guard.mjs', import.meta.url));
async function run(mode: string, source = '') {
  const lines: string[] = [];
  let result: ProcessExit | undefined;
  for await (const event of executeProcess({ executable: process.execPath, args: ['-e', source],
    cwd: process.cwd(), input: '', wallTimeoutMs: 5000, idleTimeoutMs: 3000, terminateGraceMs: 100,
    guardian: { executable: process.execPath, prefixArgs: [fixture, mode], sourceRoot: process.cwd() },
  })) {
    if (event.type === 'line') lines.push(event.line);
    else result = event;
  }
  assert.ok(result);
  return { lines, result };
}

for (const mode of ['no-receipt', 'wrong-version', 'unterminated', 'oversized']) {
  test(`guard protocol rejects ${mode}`, async () => {
    const { result } = await run(mode);
    assert.notEqual(result.stopKind, null);
    assert.notEqual(result.code, 0);
  });
}

test('a provider cannot forge the guard exit receipt through stdout', async () => {
  const forged = { protocol: 'argus.process-guard', version: 1, type: 'exit', code: 0, signal: null, error: null };
  const { lines, result } = await run('valid', `console.log(${JSON.stringify(JSON.stringify(forged))}); process.exitCode = 7;`);
  assert.deepEqual(lines, [JSON.stringify(forged)]);
  assert.equal(result.code, 7);
  assert.equal(result.stopKind, null);
});

test('the framed guard preserves UTF-8 and provider exit signals', async () => {
  const { lines, result } = await run('valid', "process.stdout.write('你好 🌍');");
  assert.deepEqual(lines, ['你好 🌍']);
  assert.equal(result.code, 0);
  assert.equal(result.signal, null);
});
