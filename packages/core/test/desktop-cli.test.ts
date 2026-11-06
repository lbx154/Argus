import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';
import test from 'node:test';
import { launcher, notInstalled } from '../src/agent/sandbox.js';
import { callAgent } from '../src/agent/call.js';
import { Store } from '../src/store.js';
import { serve } from '../src/serve/index.js';
import { BACKENDS } from '../src/types.js';

const windows = process.platform === 'win32';

test('Windows desktop/core shim forwarding grammar uses the same positive and negative fixtures', { skip: !windows }, async t => {
  const root = await mkdtemp(join(tmpdir(), 'argus shared shim 中文 '));
  t.after(() => rm(root, { recursive: true, force: true }));
  const rows = JSON.parse(await readFile(new URL('./fixtures/windows-shims.json', import.meta.url), 'utf8')) as { name: string; source: string; target: string | null; prefix?: string[] }[];
  const shim = join(root, 'custom.cmd');
  for (const row of rows) {
    await writeFile(shim, row.source);
    if (!row.target) { assert.throws(() => launcher(shim), /without a shell/, row.name); continue; }
    const target = join(root, row.target);
    await mkdir(join(target, '..'), { recursive: true });
    await writeFile(target, 'console.log(JSON.stringify(process.argv.slice(2)))\n');
    const native = row.target.endsWith('.exe');
    assert.deepEqual(launcher(shim), [...native ? [target] : [process.execPath, target], ...row.prefix!], row.name);
    if (!native) assert.deepEqual(JSON.parse(execFileSync(process.execPath, [target, ...row.prefix!, '%PATH%', '&literal'], { encoding: 'utf8' })), [...row.prefix!, '%PATH%', '&literal']);
  }
});

test('Windows selected custom CLI overrides a different PATH CLI for Settings, models and all backend turns', { skip: !windows }, async t => {
  const root = await mkdtemp(join(tmpdir(), 'argus desktop chosen 中文 '));
  const previous = { ...process.env };
  t.after(async () => {
    await callAgent.close?.('desktop-choice/');
    for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key];
    Object.assign(process.env, previous);
    await rm(root, { recursive: true, force: true });
  });
  const bin = join(root, 'PATH first');
  const selected = join(root, '操作者选择 custom & %PATH%');
  const work = join(root, 'work');
  for (const dir of [bin, selected, work]) await mkdir(dir);
  Object.assign(process.env, { HOME: root, USERPROFILE: root, ARGUS_HOME: join(root, 'state'),
    CODEX_HOME: join(root, 'codex'), COPILOT_HOME: join(root, 'copilot'), PI_CODING_AGENT_DIR: join(root, 'pi'),
    Path: `${bin}${delimiter}${process.env.PATH ?? ''}` });
  const marker = join(root, 'invocations.jsonl');
  const fixture = new URL('./fixtures/fake-agent.mjs', import.meta.url).href;
  for (const backend of BACKENDS) {
    const script = join(selected, `not-the-backend-name-${backend}.mjs`);
    const chosen = join(selected, `自定义-${backend}.cmd`);
    const preamble = (label: string) => `import {appendFileSync} from 'node:fs'; appendFileSync(${JSON.stringify(marker)},JSON.stringify({label:${JSON.stringify(label)},file:process.argv[1],pid:process.pid,args:process.argv.slice(2)})+'\\n');\n`;
    await writeFile(script, preamble(`selected-${backend}`) + `await import(${JSON.stringify(fixture)});\n`);
    await writeFile(chosen, `@echo off\r\n"node" "%~dp0not-the-backend-name-${backend}.mjs" %*\r\n`);
    await writeFile(join(bin, `${backend}.mjs`), preamble(`PATH-${backend}`) + 'console.log("wrong CLI"); process.exit(93);\n');
    await writeFile(join(bin, `${backend}.cmd`), `@echo off\r\n"node" "%~dp0${backend}.mjs" %*\r\n`);
    process.env[`ARGUS_${backend.toUpperCase()}_BIN`] = chosen;
    assert.equal(notInstalled(backend), null);
  }
  const store = new Store(join(root, 'state'));
  await mkdir(join(root, 'state'), { recursive: true });
  await writeFile(join(root, 'state', 'credentials.json'), JSON.stringify(Object.fromEntries(BACKENDS.map((backend, index) => [
    index % 2 ? `argus_${backend}_bin` : `ARGUS_${backend.toUpperCase()}_BIN`, join(bin, `${backend}.mjs`),
  ]))));
  // Neither credential registrations nor mixed-case single-turn environment
  // values can replace the desktop's confirmed executable. Ordinary PATH
  // and other child environment values retain their existing contract.
  assert.ok(launcher('codex', { ARGUS_CODEX_BIN: join(bin, 'codex.mjs'), argus_codex_bin: '' }).includes(join(selected, 'not-the-backend-name-codex.mjs')));
  const server = await serve({ store, callAgent, backend: 'codex', model: '', port: 0, token: 'fixture-only', web: work });
  t.after(() => server.close());
  const get = async (path: string) => (await fetch(server.url + path, { headers: { authorization: 'Bearer fixture-only' } })).json();
  const setup = await get('/api/system/setup') as { backends: { backend: string; installed: boolean; version: string | null }[] };
  assert.deepEqual(setup.backends.map(row => [row.backend, row.installed, row.version]), BACKENDS.map(backend => [backend, true, 'fake-cli 1.0']));
  for (const backend of ['codex', 'copilot'] as const) {
    const project = store.create({ request: '', workdir: work, backend, model: null, effort: '' });
    const models = await get(`/api/projects/${project.id}/model-choices`) as { options: { model: string }[] };
    assert.ok(models.options.some(row => row.model === 'fake-default'), backend);
  }
  for (const backend of BACKENDS) {
    const result = await callAgent({ backend, role: 'manager', form: 'manager', cwd: work, access: 'write',
      session: `desktop-choice/${backend}`, callsDir: join(root, 'calls'), usageFile: join(root, 'usage.jsonl'),
      env: { [`ARGUS_${backend.toUpperCase()}_BIN`]: join(bin, `${backend}.mjs`), [`argus_${backend}_bin`]: '', PATH: bin, ORDINARY_TEST_VALUE: 'literal child value' },
      prompt: JSON.stringify({ output: { to: 'operator', letter: `selected ${backend}`, open: [], task: '' } }) });
    assert.equal(result.completed, true, `${backend}: ${result.error}`);
    assert.equal(result.handoff?.letters[0], `selected ${backend}`);
    await callAgent.close?.(`desktop-choice/${backend}`);
  }
  const lines = (await readFile(marker, 'utf8')).trim().split('\n').map(line => JSON.parse(line) as { label: string; file: string; args: string[] });
  assert.ok(lines.length >= 10);
  assert.ok(lines.every(row => row.label.startsWith('selected-') && row.file.startsWith(selected)), JSON.stringify(lines));
  for (const backend of BACKENDS) assert.ok(lines.some(row => row.label === `selected-${backend}` && row.args.includes('--version')));
  // An explicit choice that later disappears must not silently run the PATH CLI.
  for (const value of [join(selected, 'missing-custom.cmd'), '', 'pi']) {
    process.env.ARGUS_PI_BIN = value;
    assert.match(notInstalled('pi') ?? '', /Configured pi executable/);
    assert.throws(() => launcher('pi'), /not found|absolute path/);
    const failed = await callAgent({ backend: 'pi', role: 'manager', form: 'manager', cwd: work, access: 'write',
      callsDir: join(root, 'calls'), usageFile: join(root, 'usage.jsonl'), prompt: 'must not run PATH' });
    assert.equal(failed.completed, false);
    assert.match(failed.error ?? '', /Configured pi executable/);
  }
  const after = await readFile(marker, 'utf8');
  assert.ok(!after.includes('PATH-'), 'a PATH fallback must never execute');
});

test('POSIX default launcher ignores Windows desktop overrides and retains normal PATH execution', { skip: windows }, async t => {
  const root = await mkdtemp(join(tmpdir(), 'argus desktop posix '));
  t.after(() => rm(root, { recursive: true, force: true }));
  const saved = { PATH: process.env.PATH, ARGUS_PI_BIN: process.env.ARGUS_PI_BIN };
  t.after(() => { for (const [key, value] of Object.entries(saved)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
  const file = join(root, 'pi');
  await writeFile(file, '#!/bin/sh\nprintf "posix unchanged\\n"\n'); await chmod(file, 0o755);
  process.env.PATH = root; process.env.ARGUS_PI_BIN = join(root, 'not-present.cmd');
  assert.deepEqual(launcher('pi'), [file]);
  assert.equal(notInstalled('pi'), null);
  assert.equal(execFileSync(launcher('pi')[0]!, [], { encoding: 'utf8' }).trim(), 'posix unchanged');
});
