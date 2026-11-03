// A task that begins while another works in the workspace gets a git worktree of its own, so that
// tasks side by side do not overwrite each other's files. Argus makes it from the workspace as it is
// then, uncommitted and new files included. Bringing the changes back to the workspace, and deleting
// the worktree once nothing in it is needed, is the task's own work: Argus only forgets one deleted.

import { execFileSync } from 'node:child_process';
import { appendFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
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
  try { return realpathSync(git(workdir, ['rev-parse', '--show-toplevel'])) === realpathSync(workdir); } catch { return false; }
}

/** A worktree for task `id` at `<workdir>/.argus-worktrees/<id>`, checked out at a commit of the
 * workspace as it is now (the branch and index of the workspace untouched); its commit is `base`. */
export function makeWorktree(workdir: string, id: string): { dir: string; base: string } {
  const common = resolve(workdir, git(workdir, ['rev-parse', '--git-common-dir']));
  const exclude = join(common, 'info', 'exclude');
  mkdirSync(join(common, 'info'), { recursive: true });
  if (!(existsSync(exclude) && readFileSync(exclude, 'utf8').split('\n').includes(`/${WORKTREES}/`))) appendFileSync(exclude, `\n/${WORKTREES}/\n`);
  const scratch = mkdtempSync(join(tmpdir(), 'argus-snapshot-'));
  try {
    const index = { GIT_INDEX_FILE: join(scratch, 'index') };
    git(workdir, ['add', '-A', '.'], index);
    const tree = git(workdir, ['write-tree'], index);
    let parent: string[] = [];
    try { parent = ['-p', git(workdir, ['rev-parse', '--verify', 'HEAD'])]; } catch { /* a repository with no commit yet */ }
    const base = git(workdir, ['-c', 'user.name=Argus', '-c', 'user.email=argus@localhost', 'commit-tree', tree, ...parent, '-m', `Argus: the workspace as task ${id} began`]);
    const dir = join(workdir, WORKTREES, id);
    git(workdir, ['worktree', 'add', '--detach', dir, base]);
    return { dir, base };
  } finally { rmSync(scratch, { recursive: true, force: true }); }
}

/** Whether the task deleted its worktree; if so, git's record of it is dropped too. */
export function forgetWorktree(workdir: string, dir: string): boolean {
  if (existsSync(dir)) return false;
  git(workdir, ['worktree', 'prune']);
  return true;
}
