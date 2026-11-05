import assert from 'node:assert/strict';
import test from 'node:test';
import os from 'node:os';
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { seed, STARTER } from '../src/starter.js';

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
  writeFileSync(join(starter, 'tools', 'digest'), '#!/bin/sh\n# One screen on a project\n', { mode: 0o755 });
  return { home: join(root, 'home'), starter };
}

test("a new operator's library begins as the starter library, once", t => {
  const { home, starter } = place(t);
  assert.deepEqual(seed(home, starter), ['skills/proof', 'wiki/verus.md', 'tools/digest']);
  assert.ok(existsSync(join(home, 'skills', 'proof', 'SKILL.md')));
  assert.ok(statSync(join(home, 'tools', 'digest')).mode & 0o111, 'a tool stays executable');
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
  rmSync(join(home, 'tools', 'digest'));
  writeFileSync(join(starter, 'wiki', 'slides.md'), '# Decks\n');
  mkdirSync(join(starter, 'skills', 'render'));
  writeFileSync(join(starter, 'skills', 'render', 'SKILL.md'), '---\nname: render\ndescription: Render\n---\n');
  // One the operator already has under a new starter item's name stands.
  mkdirSync(join(home, 'skills', 'render'));
  writeFileSync(join(home, 'skills', 'render', 'SKILL.md'), '---\nname: render\ndescription: Mine\n---\n');
  assert.deepEqual(seed(home, starter), ['wiki/slides.md']);
  assert.ok(!existsSync(join(home, 'tools', 'digest')));
  assert.match(readFileSync(join(home, 'skills', 'render', 'SKILL.md'), 'utf8'), /Mine/);
  assert.ok(readFileSync(join(home, 'wiki', 'INDEX.md'), 'utf8').includes('- [slides.md](slides.md): decks'));
});

test('the starter library Argus ships holds skills, wiki pages listed in its index, and runnable tools', () => {
  const index = readFileSync(join(STARTER, 'wiki', 'INDEX.md'), 'utf8');
  for (const page of ['verus.md', 'slides.md']) assert.ok(index.includes(`](${page})`), page);
  assert.ok(existsSync(join(STARTER, 'skills', 'verus-query-timing', 'SKILL.md')));
  assert.ok(statSync(join(STARTER, 'tools', 'argus-digest')).mode & 0o111);
  // Written for any operator: no path of the machine it was made on.
  const files = readdirSync(STARTER, { withFileTypes: true, recursive: true }).filter(entry => entry.isFile()).map(entry => join(entry.parentPath, entry.name));
  for (const file of files) assert.doesNotMatch(readFileSync(file, 'utf8'), /\/fastdata\/|\/home\/v-boxiuli/, file);
});
