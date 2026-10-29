// The code workspace the cockpit shows, read-only: a project's workdir is its one workspace, and
// the page reads its files, a file's text or bytes, and what git says of it. A path reaches only a
// file inside the workdir that is not hidden (`under`), as a result's path does.

import { execFile } from 'node:child_process';
import { existsSync, lstatSync, readdirSync } from 'node:fs';
import { extname, join } from 'node:path';
import { promisify } from 'node:util';
import type { Project } from '../store.js';
import { rawHeaders, sendFile, textOf, under } from './files.js';
import { HttpError, type Handler } from './index.js';

const run = promisify(execFile);
/** The tree lists this many files and folders at most. */
const ENTRIES = 6_000;
const TEXT_BYTES = 2 << 20;
const GIT_BYTES = 1 << 20;
const CUT = '\n… output truncated';

export function workspace(project: (id: string) => Project): [string, RegExp, Handler][] {
  /** The project a request names, and its workdir as the workspace it asks for. */
  const named = (url: URL): Project => {
    const work = project(url.searchParams.get('sid') ?? '');
    if (url.searchParams.get('workspace_id') !== `project:${work.id}`) throw new HttpError(404, 'unknown workspace');
    return work;
  };
  const file = (url: URL) => {
    const work = named(url);
    const found = under(work.workdir, url.searchParams.get('path') ?? '');
    if (!found) throw new HttpError(404, 'no such file');
    return { work, found };
  };
  return [
    ['GET', /^\/api\/v2\/workspaces$/, ({ url }) => {
      const { id, workdir } = project(url.searchParams.get('sid') ?? '');
      const profile = { id: `project:${id}`, label: id, path: workdir, source: 'project', project_sid: id, canonical: true };
      return { profiles: [profile], default_id: profile.id };
    }],
    ['GET', /^\/api\/v2\/workspace\/tree$/, ({ url }) => tree(named(url).workdir)],
    ['GET', /^\/api\/v2\/workspace\/file$/, ({ url }) => {
      const { work, found } = file(url);
      if (found.stat.size > TEXT_BYTES) throw new HttpError(413, `file exceeds the ${TEXT_BYTES} byte preview limit`);
      const content = textOf(found.file);
      if (content === null) throw new HttpError(415, 'binary file preview is blocked');
      return {
        root: work.workdir, workspace_id: `project:${work.id}`, path: found.path, content,
        size: found.stat.size, mtime: found.stat.mtimeMs / 1000, extension: extname(found.path).toLowerCase(),
      };
    }],
    // A file's bytes, as a result's are sent: shown as an image or a PDF, or downloaded.
    ['GET', /^\/api\/v2\/workspace\/(raw|download)$/, ({ params: [how], url, request, response }) => {
      const { found } = file(url);
      sendFile(request, response, found.file, found.stat.size, rawHeaders(found.path, how === 'download'));
    }],
    ['GET', /^\/api\/v2\/workspace\/git$/, ({ url }) => git(named(url).workdir)],
  ];
}

/** The workdir's files and folders, but hidden ones and what tools fill (node_modules,
 * __pycache__), as the file tree and the composer's `@` completion list them. */
function tree(root: string) {
  const entries: Record<string, unknown>[] = [];
  const walk = (dir: string): void => {
    let listed;
    try { listed = readdirSync(join(root, dir), { withFileTypes: true }); } catch { return; }
    for (const entry of listed) {
      if (entries.length >= ENTRIES) return;
      if (entry.name.startsWith('.') || entry.name === 'node_modules' || entry.name === '__pycache__') continue;
      const path = dir ? `${dir}/${entry.name}` : entry.name;
      const stat = lstatSync(join(root, path), { throwIfNoEntry: false });
      // A link is listed and not followed: it opens only if it leads to a file inside.
      const type = entry.isSymbolicLink() ? 'symlink' : entry.isDirectory() ? 'directory' : entry.isFile() ? 'file' : null;
      if (!stat || !type) continue;
      entries.push({
        path, name: entry.name, type, size: type === 'directory' ? 0 : stat.size, mtime: stat.mtimeMs / 1000,
        extension: type === 'file' ? extname(entry.name).toLowerCase() : '',
      });
      if (type === 'directory') walk(path);
    }
  };
  walk('');
  return { root, entries, truncated: entries.length >= ENTRIES };
}

/** Git run outside the sandbox on a repository the roles write: it changes nothing, and runs
 * none of the programs the repository's own config or attributes name (an fsmonitor, a filter, a
 * diff driver, a signature check), nor goes into a submodule, whose config is the roles' too. */
const SAFE_GIT = ['--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'log.showSignature=false'];

/** What git says of the workdir, read without changing anything; a workdir that is not a
 * repository of its own has nothing to say. */
async function git(root: string) {
  const read = async (...args: string[]): Promise<string> => {
    try {
      return (await run('git', [...SAFE_GIT, ...args], { cwd: root, maxBuffer: GIT_BYTES, timeout: 10_000 })).stdout;
    } catch (error) {
      // Output past the limit is shown up to it; a command that failed says nothing.
      const { code, stdout } = error as { code?: unknown; stdout?: string };
      return code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER' ? `${stdout}${CUT}` : '';
    }
  };
  const [name, email] = await Promise.all([read('config', 'user.name'), read('config', 'user.email')]);
  const identity = { name: name.trim(), email: email.trim(), valid: Boolean(name.trim() && email.trim()) };
  if (!existsSync(join(root, '.git'))) {
    return { available: false, branch: '', status: '', diff: '', log: '', remotes: [], upstream: '', ahead: 0, behind: 0, identity };
  }
  // Every filter the config defines runs nothing: an attribute naming one may be in .git/info/attributes.
  const filters = new Set([...(await read('config', '--name-only', '--get-regexp', '^filter\\.')).matchAll(/^filter\.(.+)\.[\w-]+$/gm)]
    .map(([, name]) => name!));
  const off = [...filters].flatMap(name => [...['clean', 'smudge', 'process'].map(key => `filter.${name}.${key}=`), `filter.${name}.required=false`])
    .flatMap(setting => ['-c', setting]);
  const [status, staged, unstaged, log, remote] = await Promise.all([
    read(...off, 'status', '--short', '--branch', '--ignore-submodules'),
    read(...off, 'diff', '--cached', '--no-ext-diff', '--no-textconv', '--ignore-submodules', '--unified=2'),
    read(...off, 'diff', '--no-ext-diff', '--no-textconv', '--ignore-submodules', '--unified=2'),
    read('log', '-20', '--date=iso-strict', '--pretty=format:%H%x09%ad%x09%an%x09%s'),
    read('remote', '-v'),
  ]);
  // The first line of the status: `## main...origin/main [ahead 1, behind 2]`.
  const [head = '', ...changes] = status.split('\n');
  const [, branch = '', upstream = '', counts = ''] = /^## (?:No commits yet on )?(\S+?)(?:\.\.\.(\S+))?(?: \[(.*)\])?$/.exec(head) ?? [];
  const remotes = new Map<string, { name: string; fetch: string; push: string }>();
  for (const [, name, address, use] of remote.matchAll(/^(\S+)\t(\S+) \((fetch|push)\)$/gm)) {
    const row = remotes.get(name!) ?? { name: name!, fetch: '', push: '' };
    // Never a credential written into the address.
    row[use as 'fetch' | 'push'] = address!.replace(/\/\/[^/@]*@/, '//');
    remotes.set(name!, row);
  }
  const diff = `${staged}\n${unstaged}`.trim();
  return {
    available: true, branch, status: changes.join('\n'), diff, log, remotes: [...remotes.values()], upstream,
    ahead: Number(/ahead (\d+)/.exec(counts)?.[1] ?? 0), behind: Number(/behind (\d+)/.exec(counts)?.[1] ?? 0),
    identity, truncated: diff.includes(CUT),
  };
}
