import assert from 'node:assert/strict';
import test from 'node:test';
import os from 'node:os';
import { syncBuiltinESMExports } from 'node:module';
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { delimiter, join } from 'node:path';
import { directoryLink, fileLink } from './helpers/fsLinks.js';
import { instructions, skillsHome, tools, toolsHome, wikiHome } from '../src/prompts.js';
import { withTools } from '../src/flow.js';
import { Store } from '../src/store.js';

test('standing context keeps locations, date, session lifetime and project GPU visibility', t => {
  const home = mkdtempSync(join(os.tmpdir(), 'argus-prompts-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const shared = process.env.ARGUS_SHARED;
  delete process.env.ARGUS_SHARED;
  t.after(() => { if (shared !== undefined) process.env.ARGUS_SHARED = shared; else delete process.env.ARGUS_SHARED; });
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  const old = process.env.CUDA_VISIBLE_DEVICES;
  t.after(() => { if (old === undefined) delete process.env.CUDA_VISIBLE_DEVICES; else process.env.CUDA_VISIBLE_DEVICES = old; });
  for (const backend of ['codex', 'claude', 'copilot', 'pi'] as const) {
    project.backend = backend;
    for (const visible of [undefined, '', '2,3']) {
      if (visible === undefined) delete process.env.CUDA_VISIBLE_DEVICES; else process.env.CUDA_VISIBLE_DEVICES = visible;
      const prompt = instructions('manager', project, store);
      assert.ok(prompt.includes(`Workspace: ${home}`));
      assert.ok(prompt.includes(`Argus's record of this project: ${store.dir(project.id)}`));
      assert.ok(prompt.includes(`team's in ${join(home, 'skills')}`));
      assert.match(prompt, /Today is \d{4}-\d{2}-\d{2} \(UTC\)\./);
      assert.ok(prompt.includes(['codex', 'claude'].includes(backend)
        ? 'Your session stays open between your turns' : 'Your session ends with each turn'));
      assert.ok(prompt.includes(`CUDA_VISIBLE_DEVICES=${visible === undefined ? '(unset)' : visible || '(none)'}`));
    }
  }
});

test('the machine is described with its load, free CPUs, memory and project GPUs', async t => {
  const home = mkdtempSync(join(os.tmpdir(), 'argus-machine-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  t.mock.method(os, 'cpus', () => Array(16).fill({}));
  t.mock.method(os, 'totalmem', () => 32 * 2 ** 30);
  const visible = process.env.CUDA_VISIBLE_DEVICES;
  t.after(() => { if (visible === undefined) delete process.env.CUDA_VISIBLE_DEVICES; else process.env.CUDA_VISIBLE_DEVICES = visible; });
  process.env.CUDA_VISIBLE_DEVICES = '2,3';
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); });
  for (const [load, free] of [[3.25, 13], [23, 0]] as const) {
    t.mock.method(os, 'loadavg', () => [load, 4, 5]);
    syncBuiltinESMExports();
    const expected = `16 logical CPUs (load ${load.toFixed(1)}, about ${free} free), 32 GiB of memory; project GPUs: CUDA_VISIBLE_DEVICES=2,3.`;
    assert.equal(instructions('engineer', project, store).split('\n').at(-1), expected);
    const lines: string[] = [];
    const logging = t.mock.method(console, 'log', (line: string) => lines.push(line));
    try { await import(new URL(`../skills/research/machine.mjs?load=${load}`, import.meta.url).href); }
    finally { logging.mock.restore(); }
    assert.equal(lines[0], `16 logical CPUs (load ${load.toFixed(1)}, about ${free} free)`);
  }
});

test('ARGUS_SHARED gives separate state roots one skills folder and one wiki', t => {
  const [first, second, shared] = ['a', 'b', 'shared'].map(name => mkdtempSync(join(os.tmpdir(), `argus-${name}-`)));
  t.after(() => { for (const dir of [first, second, shared]) rmSync(dir!, { recursive: true, force: true }); });
  const old = process.env.ARGUS_SHARED;
  t.after(() => { if (old === undefined) delete process.env.ARGUS_SHARED; else process.env.ARGUS_SHARED = old; });
  delete process.env.ARGUS_SHARED;
  // Unset, each state root keeps its own, and the wiki starts with its index.
  assert.equal(skillsHome(first!), join(first!, 'skills'));
  assert.ok(existsSync(join(wikiHome(first!), 'INDEX.md')));
  process.env.ARGUS_SHARED = shared;
  for (const home of [first!, second!]) {
    assert.equal(skillsHome(home), join(shared!, 'skills'));
    assert.equal(wikiHome(home), join(shared!, 'wiki'));
    const store = new Store(home);
    const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
    const prompt = instructions('engineer', project, store);
    assert.ok(prompt.includes(`team's in ${join(shared!, 'skills')}`));
    assert.ok(prompt.includes(join(shared!, 'wiki', 'INDEX.md')));
  }
});

test("the team's tools: runnable files listed by their first comment, first on every call's PATH", async t => {
  const home = mkdtempSync(join(os.tmpdir(), 'argus-tools-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const old = process.env.ARGUS_SHARED;
  t.after(() => { if (old === undefined) delete process.env.ARGUS_SHARED; else process.env.ARGUS_SHARED = old; });
  delete process.env.ARGUS_SHARED;
  const dir = toolsHome(home);
  assert.equal(dir, join(home, 'tools'));
  const name = process.platform === 'win32' ? 'digest.cmd' : 'digest';
  const comment = 'One screen on a project from its record';
  writeFileSync(join(dir, name), process.platform === 'win32'
    ? `@echo off\r\nrem ${comment}\r\n"${process.execPath}" "%~dp0payload.cjs" %*\r\n`
    : `#!/bin/sh\n# ${comment}\n"${process.execPath}" "$(dirname "$0")/payload.cjs" "$@"\n`);
  chmodSync(join(dir, name), 0o755);
  writeFileSync(join(dir, 'payload.cjs'), 'console.log(JSON.stringify(process.argv.slice(2)))\n');
  writeFileSync(join(dir, 'notes.txt'), 'not a command\n');
  mkdirSync(join(dir, 'folder'));
  await directoryLink(join(dir, 'folder'), join(dir, 'linked.cmd'));
  // A link, a file that cannot run and a folder are not tools.
  assert.deepEqual(tools(home), [[name, comment]]);
  await t.test('file links are not tools', async sub => {
    if (!await fileLink(sub, join(dir, name), join(dir, 'file-link.cmd'))) return;
    assert.deepEqual(tools(home), [[name, comment]]);
  });
  const commandEnv = { ...process.env, ...withTools({}, dir) };
  const output = spawnSync(process.platform === 'win32' ? process.env.ComSpec ?? 'cmd.exe' : '/bin/sh',
    process.platform === 'win32' ? ['/d', '/s', '/c', 'digest "中文 space"'] : ['-c', 'digest "中文 space"'], { env: commandEnv, encoding: 'utf8', windowsVerbatimArguments: process.platform === 'win32' });
  assert.ifError(output.error);
  assert.equal(output.status, 0, output.stderr);
  assert.deepEqual(JSON.parse(output.stdout.trim()), ['中文 space']);
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  const prompt = instructions('reviewer', project, store);
  assert.ok(prompt.includes(`The team's tools, commands on your PATH (${dir}):\n- ${name}: One screen on a project from its record\n`));
  assert.deepEqual(withTools({ PATH: '/usr/bin' }, dir), { PATH: `${dir}${delimiter}/usr/bin` });
  assert.deepEqual(withTools({ A: '1' }, dir).PATH?.split(delimiter)[0], dir);
  if (process.platform === 'win32') assert.deepEqual(withTools({ Path: 'native', A: '1' }, dir), { A: '1', PATH: `${dir};native` });
  else assert.deepEqual(withTools({ Path: 'literal', PATH: 'posix' }, dir), { Path: 'literal', PATH: `${dir}:posix` });
});

test("the wiki's pages are listed with their length, which no one sees from a page's name", t => {
  const home = mkdtempSync(join(os.tmpdir(), 'argus-wiki-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const old = process.env.ARGUS_SHARED;
  t.after(() => { if (old === undefined) delete process.env.ARGUS_SHARED; else process.env.ARGUS_SHARED = old; });
  delete process.env.ARGUS_SHARED;
  writeFileSync(join(wikiHome(home), 'verus.md'), '# Verus\n\n- one\n- two\n');
  writeFileSync(join(wikiHome(home), 'gif.md'), '# GIF\n');
  const store = new Store(home);
  const project = store.create({ workdir: home, request: '', backend: 'codex', model: null });
  assert.ok(instructions('engineer', project, store).includes(' Its pages: gif.md (2 lines), verus.md (5 lines).'));
});
