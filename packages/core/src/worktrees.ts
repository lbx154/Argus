// A task that begins while another works in the workspace gets a git worktree of its own, so that
// tasks side by side do not overwrite each other's files. Argus makes it from the workspace as it is
// then, uncommitted and new files included. Whether to bring its changes back to the workspace, and
// whether to keep or delete the worktree, are the task's to decide; Argus only forgets one deleted.

import { execFileSync } from 'node:child_process';
import { appendFileSync, existsSync, mkdirSync, readFileSync, realpathSync } from 'node:fs';
import { join, resolve } from 'node:path';

/** Where a workspace keeps its tasks' worktrees, hidden from the operator's file view. */
export const WORKTREES = '.argus-worktrees';

// Git as the operator's shell would run it, never steered by a caller's GIT_DIR or index.
function git(cwd: string, args: string[], env: Record<string, string> = {}): string {
  const clean = Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith('GIT_')));
  return execFileSync('git', ['-c', 'gc.auto=0', ...args], { cwd, env: { ...clean, ...env }, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
}

/** Whether `workdir` is the top of a git checkout, the one place a task worktree can be made from. */
export function isCheckout(workdir: string): boolean {
  // The native realpath expands Windows short names (RUNNER~1), which git's answer never carries.
  try { return realpathSync.native(git(workdir, ['rev-parse', '--show-toplevel'])) === realpathSync.native(workdir); } catch { return false; }
}

/** The tree of a checkout's files as they are, uncommitted and new ones included, written without
 * touching its index. The index it uses is kept in the checkout's git directory, so each snapshot
 * rehashes only the files that changed since the last. */
function snapshot(dir: string): string {
  const index = { GIT_INDEX_FILE: resolve(dir, git(dir, ['rev-parse', '--git-path', 'argus-snapshot-index'])) };
  git(dir, ['add', '-A', '.'], index);
  return git(dir, ['write-tree'], index);
}

/** A commit of `dir`'s files as they are now, uncommitted and new ones included, on its HEAD; the
 * checkout's branch and index are untouched. */
export function freeze(dir: string, message: string): string {
  let parent: string[] = [];
  try { parent = ['-p', git(dir, ['rev-parse', '--verify', 'HEAD'])]; } catch { /* a repository with no commit yet */ }
  return git(dir, ['-c', 'user.name=Argus', '-c', 'user.email=argus@localhost', 'commit-tree', snapshot(dir), ...parent, '-m', message]);
}

/** A worktree for task `id` at `<workdir>/.argus-worktrees/<id>`, checked out at a commit of the
 * workspace as it is now (the branch and index of the workspace untouched); its commit is `base`. */
export function makeWorktree(workdir: string, id: string): { dir: string; base: string } {
  const common = resolve(workdir, git(workdir, ['rev-parse', '--git-common-dir']));
  const exclude = join(common, 'info', 'exclude');
  mkdirSync(join(common, 'info'), { recursive: true });
  if (!(existsSync(exclude) && readFileSync(exclude, 'utf8').split('\n').includes(`/${WORKTREES}/`))) appendFileSync(exclude, `\n/${WORKTREES}/\n`);
  const base = freeze(workdir, `Argus: the workspace as task ${id} began`);
  const dir = join(workdir, WORKTREES, id);
  git(workdir, ['worktree', 'add', '--detach', dir, base]);
  return { dir, base };
}

/** Whether the task deleted its worktree; if so, git's record of it is dropped too. */
export function forgetWorktree(workdir: string, dir: string): boolean {
  if (existsSync(dir)) return false;
  git(workdir, ['worktree', 'prune']);
  return true;
}
