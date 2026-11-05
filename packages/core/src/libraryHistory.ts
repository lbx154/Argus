// The team's library (skills/, wiki/, tools/) keeps its history in git: after each turn that changed
// it, Argus commits what changed under the turn's name, so that what one task overwrote of another's
// shows, and any change can be read back or undone. The repository is `.library.git` where the
// library is, named so that git never finds it on its own: a workspace inside the state root stays
// what it is, and the roles' git commands there never reach the library's history. It tracks the
// three folders only; when the library is the state root itself, nothing else of it is in git.
//
//   git --git-dir=<library>/.library.git --work-tree=<library> log --stat

import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { sharedHome } from './prompts.js';
import { GIT_ENV, SAFE_GIT } from './serve/files.js';

export const LIBRARY_GIT = '.library.git';
const EXCLUDE = '/*\n!/skills/\n!/wiki/\n!/tools/\n__pycache__/\n*.pyc\n';
const AUTHOR = ['-c', 'user.name=Argus', '-c', 'user.email=argus@localhost', '-c', 'commit.gpgsign=false'];

/** Commit what changed in the team's library, under `label` (who changed it, on which task, and why);
 * returns the commit, or null when nothing changed or git is not there. Never throws: history is kept
 * where it can be, and its absence stops no work. */
export function keepLibrary(home: string, label: string): string | null {
  const dir = sharedHome(home);
  const repo = join(dir, LIBRARY_GIT);
  const git = (args: string[]) => spawnSync('git', [...SAFE_GIT, ...AUTHOR, `--git-dir=${repo}`, `--work-tree=${dir}`, ...args],
    { cwd: dir, env: GIT_ENV, encoding: 'utf8', timeout: 30_000 });
  try {
    if (!existsSync(repo) && git(['init', '-q']).status !== 0) return null;
    mkdirSync(join(repo, 'info'), { recursive: true });
    writeFileSync(join(repo, 'info', 'exclude'), EXCLUDE);
    if (git(['add', '-A']).status !== 0) return null;
    if (git(['diff', '--cached', '--quiet']).status === 0) return null;
    // Another Argus sharing the library may hold the index for a moment: try again once.
    if (git(['commit', '-q', '-m', label]).status !== 0 && git(['commit', '-q', '-m', label]).status !== 0) return null;
    return git(['rev-parse', '--short', 'HEAD']).stdout.trim() || null;
  } catch {
    return null;
  }
}
