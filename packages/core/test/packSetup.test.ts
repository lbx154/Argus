import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { forgetFailures, packEnv, packSetups, prepare } from '../src/packSetup.js';

function pack(home: string, root: 'skill-packs' | 'skills', name: string, setup: string, script: string): string {
  const dir = join(home, root, name);
  mkdirSync(join(dir, 'setup'), { recursive: true });
  writeFileSync(join(dir, 'SKILL.md'), `---\nname: ${name}\ndescription: test pack\nversion: 1.2\nsetup: ${setup}\n---\nbody\n`);
  writeFileSync(join(dir, 'setup', 'install'), script, { mode: 0o755 });
  return dir;
}

test('an installed pack is set up once for its version, and its env reaches the roles with its home and workspace filled in', async t => {
  const home = mkdtempSync(join(tmpdir(), 'argus-pack-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  mkdirSync(join(home, 'skills'), { recursive: true });
  pack(home, 'skill-packs', 'prover', 'setup/install', '#!/bin/sh\necho run >> "$ARGUS_PACK_HOME/runs"\nmkdir -p "$ARGUS_PACK_HOME/bin"\nprintf \'PATH=${ARGUS_PACK_HOME}/bin:${PATH}\\nPROVER_ROOT=${WORKSPACE}/.prover\\n# a comment\\n\' > "$ARGUS_PACK_HOME/env"\n');
  // The team's own skill never has its setup run: only what the operator installed does.
  pack(home, 'skills', 'sneaky', 'setup/install', '#!/bin/sh\ntouch "$ARGUS_PACK_HOME/ran"\n');
  assert.deepEqual(packSetups(home).map(({ name, state }) => [name, state]), [['prover', 'pending']]);
  const [ready] = await prepare(home);
  await prepare(home);
  assert.equal(ready!.state, 'ready');
  assert.equal(readFileSync(join(ready!.home, 'runs'), 'utf8'), 'run\n');
  assert.match(ready!.home, /pack-homes\/prover\/1\.2$/);
  assert.deepEqual(packEnv([ready!], '/work/repo', { PATH: '/usr/bin' }), { PATH: `${ready!.home}/bin:/usr/bin`, PROVER_ROOT: '/work/repo/.prover' });
});

test('a failed setup is not run again by the same process, and is tried again by the next', async t => {
  const home = mkdtempSync(join(tmpdir(), 'argus-pack-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  pack(home, 'skill-packs', 'broken', 'setup/install', '#!/bin/sh\necho tried >> "$ARGUS_PACK_HOME/runs"\necho no toolchain >&2\nexit 3\n');
  const [failed] = await prepare(home);
  assert.equal(failed!.state, 'failed');
  assert.match(readFileSync(failed!.log, 'utf8'), /no toolchain/);
  await prepare(home);
  assert.equal(readFileSync(join(failed!.home, 'runs'), 'utf8'), 'tried\n');
  assert.deepEqual(packEnv([failed!], '/w', {}), {});
  forgetFailures(home);
  assert.equal(packSetups(home)[0]!.state, 'pending');
});

test("a setup outside the pack's own folder is not run", async t => {
  const home = mkdtempSync(join(tmpdir(), 'argus-pack-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  pack(home, 'skill-packs', 'escape', '../../outside', '#!/bin/sh\nexit 0\n');
  assert.deepEqual(packSetups(home), []);
});

test('a setup script the store kept without its executable bit runs with the interpreter its #! line names', async t => {
  const home = mkdtempSync(join(tmpdir(), 'argus-pack-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const dir = pack(home, 'skill-packs', 'plain', 'setup/install', '#!/usr/bin/env bash\n[[ -n "$ARGUS_PACK_HOME" ]] && echo "X=1" > "$ARGUS_PACK_HOME/env"\n');
  const { chmodSync } = await import('node:fs');
  chmodSync(join(dir, 'setup', 'install'), 0o644);
  const [ready] = await prepare(home);
  assert.equal(ready!.state, 'ready', readFileSync(ready!.log, 'utf8'));
  assert.deepEqual(packEnv([ready!], '/w', {}), { X: '1' });
});
