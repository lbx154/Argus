// A project published to a remote the operator names: Argus pushes the workspace's branches there, and its
// own page of where the work stands to a branch of its own, so that whoever opens the remote sees the
// campaign without anyone watching the run. Argus pushes from the host, with the host's git credentials:
// no role's sandbox holds them, and no role has to remember to push.

import { execFile } from 'node:child_process';

/** The remote's address: `owner/repo` is a GitHub repository; anything else is a git URL or path as given. */
export const remoteUrl = (remote: string): string =>
  /^[\w.-]+\/[\w.-]+$/.test(remote) ? `https://github.com/${remote}.git` : remote;

/** Whether `remote` can name a remote: a GitHub `owner/repo`, a URL, or an absolute path. */
export const isRemote = (remote: string): boolean =>
  /^[\w.-]+\/[\w.-]+$/.test(remote) || /^(https?|ssh|git|file):\/\/\S+$/.test(remote) || /^[\w.-]+@[\w.-]+:\S+$/.test(remote) || remote.startsWith('/');

/** The branch Argus's own page goes to; the team's branches are never touched by it. */
export const PROGRESS_BRANCH = 'argus-progress';
const PROGRESS_REF = 'refs/argus/progress';

export interface Published {
  /** The branches the remote took (new or moved on). */
  pushed: string[];
  /** The branches it refused, each with the reason it gave: one that went back in history is never forced. */
  rejected: string[];
  /** Why nothing could be pushed, when nothing was. */
  error?: string;
}

function git(cwd: string, args: string[], input?: string, env?: Record<string, string>): Promise<{ code: number; out: string; err: string }> {
  return new Promise(resolve => {
    const child = execFile('git', args, { cwd, env: { ...process.env, GIT_TERMINAL_PROMPT: '0', ...env }, maxBuffer: 16 * 1024 * 1024, timeout: 10 * 60_000 },
      (error, out, err) => resolve({ code: error ? (typeof error.code === 'number' ? error.code : 1) : 0, out: String(out), err: String(err) }));
    if (input !== undefined) child.stdin?.end(input);
  });
}

/** Commits `files` as the next version of Argus's page, on a ref of its own beside the workspace's branches;
 * the working tree and the index are not touched. Nothing is committed when nothing changed. */
async function commitPage(cwd: string, files: Record<string, string>, when: string): Promise<void> {
  const entries: string[] = [];
  for (const [name, text] of Object.entries(files).sort(([a], [b]) => a.localeCompare(b))) {
    const blob = await git(cwd, ['hash-object', '-w', '--stdin'], text);
    if (blob.code) throw new Error(`git hash-object: ${blob.err.trim()}`);
    entries.push(`100644 blob ${blob.out.trim()}\t${name}`);
  }
  const tree = await git(cwd, ['mktree'], `${entries.join('\n')}\n`);
  if (tree.code) throw new Error(`git mktree: ${tree.err.trim()}`);
  const parent = await git(cwd, ['rev-parse', '--verify', '--quiet', `${PROGRESS_REF}^{commit}`]);
  const before = parent.code ? null : parent.out.trim();
  if (before && (await git(cwd, ['rev-parse', `${before}^{tree}`])).out.trim() === tree.out.trim()) return;
  const who = { GIT_AUTHOR_NAME: 'Argus', GIT_AUTHOR_EMAIL: 'argus@localhost', GIT_COMMITTER_NAME: 'Argus', GIT_COMMITTER_EMAIL: 'argus@localhost' };
  const commit = await git(cwd, ['commit-tree', tree.out.trim(), ...before ? ['-p', before] : [], '-m', `Argus: where the work stands, ${when}`], undefined, who);
  if (commit.code) throw new Error(`git commit-tree: ${commit.err.trim()}`);
  const moved = await git(cwd, ['update-ref', PROGRESS_REF, commit.out.trim(), ...before ? [before] : []]);
  if (moved.code) throw new Error(`git update-ref: ${moved.err.trim()}`);
}

/** Pushes every local branch of the workspace's repository (never forced), and Argus's page to
 * PROGRESS_BRANCH. `helper` is the credential helper git uses for it, in place of the host's own. */
export async function publish(workdir: string, remote: string, files: Record<string, string>, helper?: string): Promise<Published> {
  try {
    await commitPage(workdir, files, new Date().toISOString());
  } catch (error) {
    return { pushed: [], rejected: [], error: String(error instanceof Error ? error.message : error) };
  }
  const page = (await git(workdir, ['rev-parse', '--verify', '--quiet', PROGRESS_REF])).code === 0;
  const credentials = helper ? ['-c', 'credential.helper=', '-c', `credential.helper=${helper}`] : [];
  const pushed = await git(workdir, [...credentials, 'push', '--porcelain', remoteUrl(remote),
    'refs/heads/*:refs/heads/*', ...page ? [`${PROGRESS_REF}:refs/heads/${PROGRESS_BRANCH}`] : []]);
  // --porcelain: a line per ref, `<flag>\t<from>:<to>\t<summary>`; `!` refused, `=` up to date.
  const lines = pushed.out.split('\n').filter(line => /^[ *+=!-]\t/.test(line));
  const branch = (line: string): string => (line.split('\t')[1] ?? '').split(':')[1]?.replace(/^refs\/heads\//, '') ?? '';
  const result: Published = {
    pushed: lines.filter(line => /^[ *+-]\t/.test(line)).map(branch),
    rejected: lines.filter(line => line.startsWith('!')).map(line => `${branch(line)}: ${line.split('\t')[2] ?? ''}`.trim()),
  };
  if (!lines.length && pushed.code) result.error = (pushed.err.trim() || pushed.out.trim()).split('\n').slice(-3).join(' ');
  return result;
}
