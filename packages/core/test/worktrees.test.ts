import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { confine, sandboxed } from '../src/agent/sandbox.js';
import { forgetWorktree, isCheckout, makeWorktree } from '../src/worktrees.js';

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

test('a worktree stays as long as its task keeps it; Argus forgets it once the task deleted it', t => {
  const dir = repo(t);
  const wt = makeWorktree(dir, 't2');
  // Still there, even unchanged: whether it is still needed is the task's to judge.
  assert.equal(forgetWorktree(dir, wt.dir), false);
  assert.ok(existsSync(wt.dir));
  rmSync(wt.dir, { recursive: true, force: true });
  assert.equal(forgetWorktree(dir, wt.dir), true);
  assert.doesNotMatch(spawnSync('git', ['worktree', 'list'], { cwd: dir, encoding: 'utf8' }).stdout, /t2/);
});

test('under bubblewrap a task deletes its own worktree, as it works in it', { skip: !sandboxed() && 'requires bubblewrap' }, t => {
  const dir = repo(t);
  const wt = makeWorktree(dir, 't2');
  const argv = confine(['git', 'worktree', 'remove', '--force', wt.dir], {
    backend: 'codex', role: 'engineer', form: 'engineer', prompt: '', cwd: wt.dir, writeDirs: [dir], access: 'write', callsDir: '', usageFile: '',
  });
  const done = spawnSync(argv[0]!, argv.slice(1), { encoding: 'utf8' });
  assert.equal(done.status, 0, done.stderr);
  assert.ok(!existsSync(wt.dir));
  assert.equal(forgetWorktree(dir, wt.dir), true);
});

test('a folder that is not the top of a git checkout gets no worktree', t => {
  const dir = mkdtempSync(join(tmpdir(), 'argus-plain-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.equal(isCheckout(dir), false);
});
