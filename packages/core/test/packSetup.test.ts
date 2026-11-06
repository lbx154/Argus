import assert from 'node:assert/strict';
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';
import test from 'node:test';
import { forgetFailures, packEnv, packSetups, prepare } from '../src/packSetup.js';
import { directoryLink, fileLink } from './helpers/fsLinks.js';

// The store downloads regular files without execute bits. Node is the one runtime Argus requires.
const node = (body: string): string => `#!/usr/bin/env node\n${body}\n`;
function pack(home: string, root: 'skill-packs' | 'skills', name: string, setup: string, script: string): string {
  const dir = join(home, root, name);
  mkdirSync(join(dir, 'setup'), { recursive: true });
  writeFileSync(join(dir, 'SKILL.md'), `---\r\nname: ${name}\r\ndescription: test pack\r\nversion: 1.2\r\nsetup: ${setup}\r\n---\r\nbody\r\n`);
  writeFileSync(join(dir, 'setup', 'install'), script, { mode: 0o644 });
  return dir;
}
function place(t: test.TestContext): string {
  const home = mkdtempSync(join(tmpdir(), 'argus pack 中文 '));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  return home;
}

test('an installed pack is set up once for its version, and its env reaches the roles with its home and workspace filled in', async t => {
  const home = place(t);
  const script = node(`const fs = require('node:fs');
fs.appendFileSync(process.env.ARGUS_PACK_HOME + '/runs', 'run\\n');
fs.writeFileSync(process.env.ARGUS_PACK_HOME + '/env', 'PATH=\u0024{ARGUS_PACK_HOME}/bin\u0024{PATH_SEPARATOR}\u0024{PATH}\\r\\nPROVER_ROOT=\u0024{WORKSPACE}/.prover\\r\\n# a comment\\r\\n');`);
  pack(home, 'skill-packs', 'prover', 'setup/install', script);
  // The team's own skill never has its setup run: only what the operator installed does.
  pack(home, 'skills', 'sneaky', 'setup/install', script);
  assert.deepEqual(packSetups(home).map(({ name, state }) => [name, state]), [['prover', 'pending']]);
  const [[ready]] = await Promise.all([prepare(home), prepare(home)]);
  await prepare(home);
  assert.equal(ready!.state, 'ready', readFileSync(ready!.log, 'utf8'));
  assert.equal(readFileSync(join(ready!.home, 'runs'), 'utf8'), 'run\n');
  assert.equal(ready!.home, join(home, 'pack-homes', 'prover', '1.2'));
  assert.deepEqual(packEnv([ready!], '/work/repo 中文', { PATH: '/usr/bin' }), { PATH: `${ready!.home}/bin${delimiter}/usr/bin`, PROVER_ROOT: '/work/repo 中文/.prover' });
  const dir = join(home, 'skill-packs', 'prover');
  writeFileSync(join(dir, 'SKILL.md'), '---\nversion: 2\nsetup: setup/install\n---\n');
  assert.equal((await prepare(home))[0]!.state, 'ready');
  assert.equal(readFileSync(join(home, 'pack-homes', 'prover', '2', 'runs'), 'utf8'), 'run\n');
  assert.equal(readFileSync(join(ready!.home, 'runs'), 'utf8'), 'run\n');
});

test('a failed setup is not run again by the same process, and is tried again by the next', async t => {
  const home = place(t);
  pack(home, 'skill-packs', 'broken', 'setup/install', node(`const fs = require('node:fs'); fs.appendFileSync(process.env.ARGUS_PACK_HOME + '/runs', 'tried\\n'); console.error('no toolchain'); process.exit(3);`));
  const [failed] = await prepare(home);
  assert.equal(failed!.state, 'failed');
  assert.match(readFileSync(failed!.log, 'utf8'), /no toolchain/);
  assert.match(readFileSync(join(failed!.home, '.failed'), 'utf8'), /exit 3/);
  await prepare(home);
  assert.equal(readFileSync(join(failed!.home, 'runs'), 'utf8'), 'tried\n');
  assert.deepEqual(packEnv([failed!], '/w', {}), {});
  forgetFailures(home);
  assert.equal(packSetups(home)[0]!.state, 'pending');
});

test("a setup outside the pack's lexical folder is not run", t => {
  const home = place(t);
  pack(home, 'skill-packs', 'escape', '../../outside', node('process.exit(0)'));
  assert.deepEqual(packSetups(home), []);
});

test('Windows rejects setup sources reached through directory or file links', { skip: process.platform !== 'win32' }, async t => {
  const home = place(t);
  const dir = pack(home, 'skill-packs', 'linked', 'setup/install', node('process.exit(0)'));
  rmSync(join(dir, 'setup'), { recursive: true });
  const outside = join(home, 'outside');
  mkdirSync(outside);
  writeFileSync(join(outside, 'install'), node('process.exit(0)'));
  await directoryLink(outside, join(dir, 'setup'));
  await directoryLink(dir, join(home, 'skill-packs', 'linked-root'));
  assert.deepEqual(packSetups(home), []);
  await t.test('file links are not setup sources', async sub => {
    rmSync(join(dir, 'setup'), { recursive: true });
    mkdirSync(join(dir, 'setup'));
    if (!await fileLink(sub, join(outside, 'install'), join(dir, 'setup', 'install'))) return;
    assert.deepEqual(packSetups(home), []);
  });
});

test('a store Node script runs without execute bits (Windows does not require a shebang)', async t => {
  const home = place(t);
  const dir = pack(home, 'skill-packs', 'plain', 'setup/install.cjs', 'unused');
  writeFileSync(join(dir, 'setup', 'install.cjs'), `${process.platform === 'win32' ? '' : '#!/usr/bin/env node\n'}require('node:fs').writeFileSync(process.env.ARGUS_PACK_HOME + '/env', 'X=1\\n');`);
  const [ready] = await prepare(home);
  assert.equal(ready!.state, 'ready', readFileSync(ready!.log, 'utf8'));
  assert.deepEqual(packEnv([ready!], '/w', {}), { X: '1' });
});

test('interpreter launch failures and missing setup files are recorded, not silently swallowed or retried', async t => {
  const home = place(t);
  pack(home, 'skill-packs', 'missing-interpreter', 'setup/install', '#!/usr/bin/env argus-no-such-interpreter\n');
  pack(home, 'skill-packs', 'missing-script', 'setup/gone', 'unused');
  const failed = await prepare(home);
  assert.ok(failed.every(pack => pack.state === 'failed'));
  assert.match(readFileSync(failed.find(pack => pack.name === 'missing-interpreter')!.log, 'utf8'), process.platform === 'win32' ? /argus-no-such-interpreter is unavailable/ : /argus-no-such-interpreter.*ENOENT/);
  assert.match(readFileSync(failed.find(pack => pack.name === 'missing-script')!.log, 'utf8'), /ENOENT/);
  assert.deepEqual(await prepare(home), failed);
});

test('POSIX keeps executable, default-sh and non-executable shebang setup dispatch', { skip: process.platform === 'win32' }, async t => {
  const home = place(t);
  for (const [name, head, mode] of [
    ['executable', '#!/bin/sh\n', 0o755], ['plain', '', 0o644], ['shell', '#!/bin/sh\n', 0o644],
    ['bash', '#!/usr/bin/env bash\n[[ -n "$BASH_VERSION" ]] || exit 3\n', 0o644],
  ] as const) {
    const dir = pack(home, 'skill-packs', name, 'setup/install', `${head}echo X=1 > "$ARGUS_PACK_HOME/env"\n`);
    chmodSync(join(dir, 'setup', 'install'), mode);
  }
  const ready = await prepare(home);
  assert.equal(ready.length, 4);
  for (const pack of ready) {
    assert.equal(pack.state, 'ready', readFileSync(pack.log, 'utf8'));
    assert.deepEqual(packEnv([pack], '/w', {}), { X: '1' });
  }
});

test('Windows batch setup paths are literal, including spaces, Unicode and shell metacharacters', { skip: process.platform !== 'win32' }, async t => {
  const root = place(t);
  const home = join(root, '中文 space %PATH% & (pack)!');
  mkdirSync(home);
  const dir = pack(home, 'skill-packs', 'batch', 'setup/install.cmd', 'unused');
  writeFileSync(join(dir, 'setup', 'install.cmd'), '@echo off\r\n>"%ARGUS_PACK_HOME%\\env" echo X=1\r\n');
  const [ready] = await prepare(home);
  assert.equal(ready!.state, 'ready', readFileSync(ready!.log, 'utf8'));
  assert.deepEqual(packEnv([ready!], '/w', {}), { X: '1' });
});

test('Windows reports a missing POSIX interpreter rather than pretending to support shell setup', { skip: process.platform !== 'win32' }, async t => {
  const home = place(t);
  const old = process.env.PATH;
  process.env.PATH = '';
  t.after(() => { if (old === undefined) delete process.env.PATH; else process.env.PATH = old; });
  pack(home, 'skill-packs', 'shell', 'setup/install', '#!/bin/sh\necho X=1\n');
  const [failed] = await prepare(home);
  assert.equal(failed!.state, 'failed');
  assert.match(readFileSync(failed!.log, 'utf8'), /interpreter sh is unavailable.*Node setup/);
});

test('pack PATH expansion is native, case-aware on Windows, composes packs and keeps dollar sequences literal', t => {
  const home = place(t);
  const ready = ['first', 'second'].map(name => {
    const dir = join(home, name);
    mkdirSync(dir);
    writeFileSync(join(dir, 'env'), 'Path=${ARGUS_PACK_HOME}${PATH_SEPARATOR}${PATH}\r\nVALUE="${WORKSPACE}"\r\n');
    return { name, version: '1', home: dir, state: 'ready' as const, log: '' };
  });
  const env = packEnv(ready, '/w/$&/中文 space', process.platform === 'win32' ? { Path: 'native' } : { Path: 'native', PATH: 'posix' });
  assert.equal(env.VALUE, '/w/$&/中文 space');
  if (process.platform === 'win32') assert.deepEqual(env, { PATH: `${ready[1]!.home};${ready[0]!.home};native`, VALUE: '/w/$&/中文 space' });
  else assert.equal(env.Path, `${ready[1]!.home}:posix`);
});
