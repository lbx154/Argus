import assert from 'node:assert/strict';
import test from 'node:test';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { seed, STARTER } from '../src/starter.js';
import { findExecutable, mergeEnv } from '../src/commands.js';
import { tools } from '../src/prompts.js';
const command = process.platform === 'win32' ? 'digest.cmd' : 'digest';

function place(t: test.TestContext): { home: string; starter: string } {
  const root = mkdtempSync(join(os.tmpdir(), 'argus-starter-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const old = process.env.ARGUS_SHARED;
  t.after(() => { if (old === undefined) delete process.env.ARGUS_SHARED; else process.env.ARGUS_SHARED = old; });
  delete process.env.ARGUS_SHARED;
  const starter = join(root, 'starter');
  mkdirSync(join(starter, 'skills', 'proof'), { recursive: true });
  writeFileSync(join(starter, 'skills', 'proof', 'SKILL.md'), '---\nname: proof\ndescription: Prove it\n---\n');
  mkdirSync(join(starter, 'wiki'), { recursive: true });
  writeFileSync(join(starter, 'wiki', 'INDEX.md'), '# Wiki\n\n- [verus.md](verus.md): Verus pitfalls\n- [slides.md](slides.md): decks\n');
  writeFileSync(join(starter, 'wiki', 'verus.md'), '# Verus pitfalls\n');
  mkdirSync(join(starter, 'tools'), { recursive: true });
  writeFileSync(join(starter, 'tools', command), process.platform === 'win32'
    ? '@echo off\nrem One screen on a project\necho starter\n'
    : '#!/bin/sh\n# One screen on a project\necho starter\n', { mode: 0o755 });
  return { home: join(root, 'home'), starter };
}

test("a new operator's library begins as the starter library, once", t => {
  const { home, starter } = place(t);
  assert.deepEqual(seed(home, starter), ['skills/proof', 'wiki/verus.md', `tools/${command}`]);
  assert.ok(existsSync(join(home, 'skills', 'proof', 'SKILL.md')));
  assert.deepEqual(tools(home), [[command, 'One screen on a project']], 'a tool stays runnable on this host');
  const output = spawnSync(process.platform === 'win32' ? process.env.ComSpec ?? 'cmd.exe' : '/bin/sh',
    process.platform === 'win32' ? ['/d', '/s', '/c', `""${join(home, 'tools', command)}""`] : [join(home, 'tools', command)], { encoding: 'utf8', windowsVerbatimArguments: process.platform === 'win32' });
  assert.ifError(output.error);
  assert.equal(output.status, 0, output.stderr);
  assert.equal(output.stdout.trim(), 'starter');
  const index = readFileSync(join(home, 'wiki', 'INDEX.md'), 'utf8');
  assert.match(index, /^# Wiki/);
  assert.ok(index.includes('- [verus.md](verus.md): Verus pitfalls'));
  assert.ok(!index.includes('slides.md'), 'a page not given is not indexed');
  // Started again, nothing is added; what the operator's team changed is theirs.
  writeFileSync(join(home, 'skills', 'proof', 'SKILL.md'), '---\nname: proof\ndescription: Prove it, better\n---\n');
  assert.deepEqual(seed(home, starter), []);
  assert.match(readFileSync(join(home, 'skills', 'proof', 'SKILL.md'), 'utf8'), /better/);
});

test('an upgrade adds only what is new to the starter library, and what the operator deleted stays deleted', t => {
  const { home, starter } = place(t);
  seed(home, starter);
  rmSync(join(home, 'tools', command));
  writeFileSync(join(starter, 'wiki', 'slides.md'), '# Decks\n');
  mkdirSync(join(starter, 'skills', 'render'));
  writeFileSync(join(starter, 'skills', 'render', 'SKILL.md'), '---\nname: render\ndescription: Render\n---\n');
  // One the operator already has under a new starter item's name stands.
  mkdirSync(join(home, 'skills', 'render'));
  writeFileSync(join(home, 'skills', 'render', 'SKILL.md'), '---\nname: render\ndescription: Mine\n---\n');
  assert.deepEqual(seed(home, starter), ['wiki/slides.md']);
  assert.ok(!existsSync(join(home, 'tools', command)));
  assert.match(readFileSync(join(home, 'skills', 'render', 'SKILL.md'), 'utf8'), /Mine/);
  assert.ok(readFileSync(join(home, 'wiki', 'INDEX.md'), 'utf8').includes('- [slides.md](slides.md): decks'));
});

test('a Windows upgrade adds the native starter shim without overwriting an existing user tool', { skip: process.platform !== 'win32' }, async t => {
  const { home } = place(t);
  mkdirSync(join(home, 'tools'), { recursive: true });
  // Preserve a legacy user script with implicit reads: the new Windows shim supplies UTF-8 mode.
  const old = 'from pathlib import Path\nprint(Path(__file__).with_name("probe.txt").read_text())\n';
  writeFileSync(join(home, 'tools', 'probe.txt'), '用户 digest🧪', 'utf8');
  writeFileSync(join(home, 'tools', 'argus-digest'), old);
  writeFileSync(join(home, '.starter.json'), JSON.stringify({ given: ['tools/argus-digest'] }));
  assert.ok(seed(home).includes('tools/argus-digest.cmd'));
  assert.equal(readFileSync(join(home, 'tools', 'argus-digest'), 'utf8'), old);
  assert.ok(!seed(home).includes('tools/argus-digest.cmd'));
  const cmd = process.env.ComSpec ?? 'cmd.exe';
  const args = ['/d', '/v:off', '/s', '/c', 'argus-digest'];
  const missing = spawnSync(cmd, args, { env: mergeEnv(process.env, { PATH: join(home, 'tools') }), encoding: 'utf8', windowsVerbatimArguments: true });
  assert.ifError(missing.error);
  assert.equal(missing.status, 127);
  assert.match(missing.stderr, /requires Python 3 on PATH/);
  await t.test('the installed shim calls the pre-existing Python tool when Python is available', sub => {
    if (!findExecutable('python')) { sub.skip('Python is optional; the Node-only startup and missing-Python diagnostic were checked'); return; }
    const result = spawnSync(cmd, args, { env: mergeEnv(process.env, { PATH: `${join(home, 'tools')};${process.env.PATH ?? ''}` }), encoding: 'utf8', windowsVerbatimArguments: true });
    assert.ifError(result.error);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), '用户 digest🧪');
  });
});

test('the starter library Argus ships holds skills, wiki pages listed in its index, and runnable tools', t => {
  const index = readFileSync(join(STARTER, 'wiki', 'INDEX.md'), 'utf8');
  for (const page of ['verus.md', 'slides.md']) assert.ok(index.includes(`](${page})`), page);
  assert.ok(existsSync(join(STARTER, 'skills', 'verus-query-timing', 'SKILL.md')));
  const { home } = place(t);
  seed(home);
  assert.ok(tools(home).some(([name]) => name === (process.platform === 'win32' ? 'argus-digest.cmd' : 'argus-digest')));
  // Written for any operator: no path of the machine it was made on.
  const files = readdirSync(STARTER, { withFileTypes: true, recursive: true }).filter(entry => entry.isFile()).map(entry => join(entry.parentPath, entry.name));
  for (const file of files) assert.doesNotMatch(readFileSync(file, 'utf8'), /\/fastdata\/|\/home\/v-boxiuli/, file);
});
