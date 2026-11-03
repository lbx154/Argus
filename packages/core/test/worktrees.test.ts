import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { isCheckout, makeWorktree, settleWorktree } from '../src/worktrees.js';

function repo(t: { after: (fn: () => void) => void }): string {
  const dir = mkdtempSync(join(tmpdir(), 'argus-wt-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const git = (...args: string[]) => assert.equal(spawnSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', ...args], { cwd: dir }).status, 0);
  git('init', '-q');
  writeFileSync(join(dir, 'a.txt'), 'a\n');
  git('add', '-A');
  git('commit', '-q', '-m', 'a');
  return dir;
}

test('a task worktree carries the workspace as it is, and is removed only once its changes are in the workspace', t => {
  const dir = repo(t);
  assert.ok(isCheckout(dir));
  writeFileSync(join(dir, 'a.txt'), 'edited, not committed\n');
  writeFileSync(join(dir, 'new.txt'), 'new\n');
  const wt = makeWorktree(dir, 't2');
  assert.equal(readFileSync(join(wt.dir, 'a.txt'), 'utf8'), 'edited, not committed\n');
  assert.equal(readFileSync(join(wt.dir, 'new.txt'), 'utf8'), 'new\n');
  // The workspace's own branch and index are untouched, and the worktrees folder is not its change.
  assert.match(spawnSync('git', ['status', '--porcelain'], { cwd: dir, encoding: 'utf8' }).stdout, /^ M a\.txt\n\?\? new\.txt\n$/);
  assert.deepEqual(settleWorktree(dir, wt), []);
  assert.ok(!existsSync(wt.dir), 'an unchanged worktree is removed');

  const busy = makeWorktree(dir, 't3');
  writeFileSync(join(busy.dir, 'b.txt'), 'made in the task\n');
  assert.deepEqual(settleWorktree(dir, busy), ['b.txt'], 'a change not in the workspace keeps the worktree');
  assert.ok(existsSync(busy.dir));
  writeFileSync(join(dir, 'b.txt'), 'made in the task\n');
  assert.deepEqual(settleWorktree(dir, busy), []);
  assert.ok(!existsSync(busy.dir));
});

test('a folder that is not the top of a git checkout gets no worktree', t => {
  const dir = mkdtempSync(join(tmpdir(), 'argus-plain-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.equal(isCheckout(dir), false);
});
