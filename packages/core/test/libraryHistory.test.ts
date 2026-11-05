import assert from 'node:assert/strict';
import test from 'node:test';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { keepLibrary, LIBRARY_GIT } from '../src/libraryHistory.js';
import { skillsHome, wikiHome } from '../src/prompts.js';

const git = (dir: string, ...args: string[]) => spawnSync('git', [`--git-dir=${join(dir, LIBRARY_GIT)}`, `--work-tree=${dir}`, ...args], { cwd: dir, encoding: 'utf8' });
const log = (dir: string): string[] => git(dir, 'log', '--format=%s').stdout.trim().split('\n').filter(Boolean);
const tracked = (dir: string): string[] => git(dir, 'ls-files').stdout.trim().split('\n').filter(Boolean);

test("the team's library keeps its history: one commit per turn that changed it, the library alone", t => {
  const home = mkdtempSync(join(os.tmpdir(), 'argus-history-'));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  const old = process.env.ARGUS_SHARED;
  t.after(() => { if (old === undefined) delete process.env.ARGUS_SHARED; else process.env.ARGUS_SHARED = old; });
  delete process.env.ARGUS_SHARED;
  // The state root holds more than the library: none of that is kept.
  mkdirSync(join(home, 'projects', 'p-1'), { recursive: true });
  writeFileSync(join(home, 'projects', 'p-1', 'project.json'), '{}');
  writeFileSync(join(home, 'web-token'), 'secret');
  writeFileSync(join(wikiHome(home), 'verus.md'), '# Verus\n\n- for with continue is rejected\n');
  mkdirSync(join(skillsHome(home), 'probe'));
  writeFileSync(join(skillsHome(home), 'probe', 'SKILL.md'), '---\nname: probe\ndescription: Probe\n---\n');
  assert.ok(keepLibrary(home, 'engineer on t3 (p-1): found the continue rejection'));
  assert.deepEqual(tracked(home).sort(), ['skills/probe/SKILL.md', 'wiki/INDEX.md', 'wiki/verus.md']);
  // Git never finds the library's history on its own: a workspace inside the state root is not a checkout of it.
  mkdirSync(join(home, 'workspaces', 'w'), { recursive: true });
  assert.notEqual(spawnSync('git', ['rev-parse', '--git-dir'], { cwd: join(home, 'workspaces', 'w'), encoding: 'utf8', env: { ...process.env, GIT_CEILING_DIRECTORIES: os.tmpdir() } }).status, 0);
  // A turn that changed nothing adds nothing; the next change is its own commit, overwrites included.
  assert.equal(keepLibrary(home, 'reviewer on t3 (p-1): accepted'), null);
  writeFileSync(join(wikiHome(home), 'verus.md'), '# Verus\n\n- for with continue is accepted after the patch\n');
  assert.ok(keepLibrary(home, 'engineer on t4 (p-1): corrected the continue page'));
  assert.deepEqual(log(home), ['engineer on t4 (p-1): corrected the continue page', 'engineer on t3 (p-1): found the continue rejection']);
});
