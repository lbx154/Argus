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

test('at close a worktree goes, whatever is still in it, and git forgets it', t => {
  const dir = repo(t);
  const wt = makeWorktree(dir, 't2');
  writeFileSync(join(wt.dir, 'not-brought-back.txt'), 'the task\'s to bring back before closing\n');
  releaseWorktree(dir, wt.dir);
  assert.ok(!existsSync(wt.dir));
  assert.doesNotMatch(spawnSync('git', ['worktree', 'list'], { cwd: dir, encoding: 'utf8' }).stdout, /t2/);
  assert.ok(!existsSync(join(dir, 'not-brought-back.txt')));
  // One the task removed itself, or left as a folder git no longer knows, goes the same way.
  const gone = makeWorktree(dir, 't3');
  rmSync(join(dir, '.git', 'worktrees', 't3'), { recursive: true, force: true });
  releaseWorktree(dir, gone.dir);
  assert.ok(!existsSync(gone.dir));
});

test('a folder that is not the top of a git checkout gets no worktree', t => {
  const dir = mkdtempSync(join(tmpdir(), 'argus-plain-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.equal(isCheckout(dir), false);
});
