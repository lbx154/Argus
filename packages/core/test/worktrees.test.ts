import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { isCheckout, makeWorktree, releaseWorktree } from '../src/worktrees.js';

function repo(t: { after: (fn: () => void) => void }): string {
  const dir = mkdtempSync(join(tmpdir(), 'argus-wt-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const git = (...args: string[]) => assert.equal(spawnSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', ...args], { cwd: dir }).status, 0);
  git('init', '-q');
  // Windows git turns LF into CRLF on checkout by default; these tests compare the bytes.
  git('config', 'core.autocrlf', 'false');
  writeFileSync(join(dir, 'a.txt'), 'a\n');
  git('add', '-A');
  git('commit', '-q', '-m', 'a');
  return dir;
}

test('a task worktree carries the workspace as it is, untouched', t => {
  const dir = repo(t);
  assert.ok(isCheckout(dir));
  writeFileSync(join(dir, 'a.txt'), 'edited, not committed\n');
  writeFileSync(join(dir, 'new.txt'), 'new\n');
  const wt = makeWorktree(dir, 't2');
  assert.equal(readFileSync(join(wt.dir, 'a.txt'), 'utf8'), 'edited, not committed\n');
  assert.equal(readFileSync(join(wt.dir, 'new.txt'), 'utf8'), 'new\n');
  // The workspace's own branch and index are untouched, and the worktrees folder is not its change.
  assert.match(spawnSync('git', ['status', '--porcelain'], { cwd: dir, encoding: 'utf8' }).stdout, /^ M a\.txt\n\?\? new\.txt\n$/);
});

test('at close a worktree goes once the workspace holds its every change, and stays while it does not', t => {
  const dir = repo(t);
  writeFileSync(join(dir, 'evidence.log'), 'shared, copied into the worktree\n');
  const wt = makeWorktree(dir, 't2');
  const listed = () => spawnSync('git', ['worktree', 'list'], { cwd: dir, encoding: 'utf8' }).stdout.includes('t2');
  // Its work: an edit, a new file and a deletion; the copied evidence is no change of its own.
  writeFileSync(join(wt.dir, 'a.txt'), 'task edit\n');
  writeFileSync(join(wt.dir, 'result.txt'), 'result\n');
  rmSync(join(wt.dir, 'evidence.log'));
  assert.equal(releaseWorktree(dir, wt.dir, wt.base), false);
  writeFileSync(join(dir, 'a.txt'), 'task edit\n');
  writeFileSync(join(dir, 'result.txt'), 'result\n');
  assert.equal(releaseWorktree(dir, wt.dir, wt.base), false, 'the deletion is not in the workspace yet');
  rmSync(join(dir, 'evidence.log'));
  // Meanwhile another task changed the workspace elsewhere: no reason to keep this worktree.
  writeFileSync(join(dir, 'other.txt'), 'another task\n');
  assert.equal(releaseWorktree(dir, wt.dir, wt.base), true);
  assert.ok(!existsSync(wt.dir));
  assert.ok(!listed());
  // A worktree the task deleted itself is only forgotten.
  const gone = makeWorktree(dir, 't3');
  rmSync(gone.dir, { recursive: true, force: true });
  assert.equal(releaseWorktree(dir, gone.dir, gone.base), true);
  // One the task removed with git, whose folder its sandbox's mount kept, leaves nothing behind,
  // even work the workspace lacks: removing it was the task's call.
  const remnant = makeWorktree(dir, 't4');
  writeFileSync(join(remnant.dir, 'scratch.txt'), 'discarded\n');
  rmSync(join(dir, '.git', 'worktrees', 't4'), { recursive: true, force: true });
  assert.equal(releaseWorktree(dir, remnant.dir, remnant.base), true);
  assert.ok(!existsSync(remnant.dir));
});

test('a folder that is not the top of a git checkout gets no worktree', t => {
  const dir = mkdtempSync(join(tmpdir(), 'argus-plain-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.equal(isCheckout(dir), false);
});
