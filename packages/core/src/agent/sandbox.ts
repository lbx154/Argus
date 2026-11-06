// How an agent CLI starts and what it may see: under bubblewrap, the workdir and the directories
// its call is given, system software, the CLI itself, and a private home per workdir into which
// only the CLI's login and settings are brought. The operator may register more environment values.

import { spawnSync } from 'node:child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, readSync, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { argusHome } from '../store.js';
import { credentials } from '../credentials.js';
import { envValue, findExecutable as which, launcher as nativeLauncher, mergeEnv } from '../commands.js';
import { BACKENDS, type AgentRequest, type Backend } from '../types.js';

// The Windows desktop's selected executable is not necessarily named after
// its backend, nor first on PATH. All version/model/turn launchers use it.
function selectedExecutable(name: string): string | undefined {
  // This is the desktop host's confirmed startup configuration, not a
  // provider/pack/credential environment override for an individual child.
  return process.platform === 'win32' && BACKENDS.includes(name as Backend)
    ? envValue(process.env, `ARGUS_${name.toUpperCase()}_BIN`) : undefined;
}

export function launcher(name: string, env: NodeJS.ProcessEnv = process.env, cwd = process.cwd()): string[] {
  const selected = selectedExecutable(name);
  if (selected !== undefined && !isAbsolute(selected)) throw Object.assign(new Error(`Configured ${name} executable must be an absolute path: ${selected || '<empty>'}`), { code: 'ENOENT' });
  return nativeLauncher(selected ?? name, env, cwd);
}


let bwrap: boolean | undefined;
/** Bubblewrap that starts here: a host may refuse it the user namespace it needs. Probed once. */
export function sandboxed(): boolean {
  bwrap ??= process.platform === 'linux' && spawnSync('bwrap', ['--ro-bind', '/', '/', 'true'], { stdio: 'ignore' }).status === 0;
  return bwrap;
}

const real = (path: string): string => { try { return realpathSync(path); } catch { return resolve(path); } };
const read = (path: string): string => { try { return readFileSync(path, 'utf8'); } catch { return ''; } };
const isFile = (path: string): boolean => { try { return statSync(path).isFile(); } catch { return false; } };
const within = (path: string, dir: string): boolean => {
  const rest = relative(dir, path);
  return rest === '' || (rest !== '..' && !rest.startsWith(`..${sep}`) && !isAbsolute(rest));
};
const depth = (path: string): number => path.split(sep).filter(Boolean).length;
const outermost = (paths: string[]): string[] => [...new Set(paths)].sort((a, b) => depth(a) - depth(b))
  .reduce<string[]>((kept, path) => kept.some(outer => within(path, outer)) ? kept : [...kept, path], []);
/** A path as the CLI may name it: as given and resolved. */
const namedAs = (path: string): string[] => [...new Set([resolve(path), real(path)])];

const INSTALL: Record<Backend, string> = {
  codex: 'npm install -g @openai/codex@latest',
  claude: 'npm install -g @anthropic-ai/claude-code',
  copilot: 'npm install -g @github/copilot',
  pi: 'npm install -g --ignore-scripts @earendil-works/pi-coding-agent',
};

/** How the operator signs a backend's CLI in. */
export const SIGN_IN: Record<Backend, string> = {
  codex: 'run `codex login` in a terminal',
  claude: 'run `claude` in a terminal, then `/login`',
  copilot: 'run `copilot login` in a terminal',
  pi: 'run `pi` in a terminal, then `/login`',
};

/** Why a call could not start its CLI, when it is not on PATH: with how to install it. */
export function notInstalled(backend: Backend, env: NodeJS.ProcessEnv = process.env, cwd = process.cwd()): string | null {
  const selected = selectedExecutable(backend);
  if (selected !== undefined) {
    if (!isAbsolute(selected)) return `Configured ${backend} executable must be an absolute path: ${selected || '<empty>'}`;
    return which(selected, env, cwd) ? null : `Configured ${backend} executable was not found at ${selected}. Update the desktop CLI selection.`;
  }
  return which(backend, process.platform === 'win32' ? env : process.env, cwd) ? null : `${backend} is not installed (not found on PATH). Install it with: ${INSTALL[backend]}`;
}

/** The login files the CLI refreshes, the settings it only reads, and the variables it signs in with. */
function cliSetup(backend: Backend): { login: string[]; settings: string[]; signsInWith: (name: string) => boolean } {
  if (backend === 'claude') {
    const dir = process.env.CLAUDE_CONFIG_DIR || join(homedir(), '.claude');
    return {
      login: [join(dir, '.credentials.json')], settings: [join(dir, 'settings.json'), join(dir, 'CLAUDE.md')],
      signsInWith: name => name.startsWith('ANTHROPIC_') || name === 'CLAUDE_CODE_OAUTH_TOKEN',
    };
  }
  if (backend === 'copilot') {
    const dir = process.env.COPILOT_HOME || join(homedir(), '.copilot');
    return {
      // The standalone copilot runs from the package it unpacked into ~/.cache/copilot.
      login: [join(dir, 'config.json')],
      settings: [join(dir, 'settings.json'), join(dir, 'permissions-config.json'), join(homedir(), '.cache', 'copilot')],
      // Not the GH_TOKEN or GITHUB_TOKEN it also takes: those are git's and gh's.
      signsInWith: name => name.startsWith('COPILOT_'),
    };
  }
  if (backend === 'pi') {
    const dir = process.env.PI_CODING_AGENT_DIR || join(homedir(), '.pi', 'agent');
    return {
      login: [join(dir, 'auth.json')], settings: [join(dir, 'models.json'), join(dir, 'settings.json')],
      // Pi reaches many providers, each with its own key.
      signsInWith: name => name.endsWith('_API_KEY') || name.startsWith('ANTHROPIC_'),
    };
  }
  const dir = process.env.CODEX_HOME || join(homedir(), '.codex');
  const config = read(join(dir, 'config.toml'));
  // A setting's string, in TOML's quotes, double or single.
  const values = (keys: string): string[] => [...config.matchAll(new RegExp(`^\\s*(?:${keys})\\s*=\\s*(?:"([^"]+)"|'([^']+)')`, 'gm'))]
    .map(([, double, single]) => (double ?? single)!);
  // Files the config names for codex to read before its first turn; one missing fails the call.
  const named = values('model_catalog_json|model_instructions_file|experimental_compact_prompt_file|experimental_instructions_file')
    .map(value => value.startsWith('~/') ? join(homedir(), value.slice(2)) : resolve(dir, value));
  const keys = new Set(['OPENAI_API_KEY', 'CODEX_API_KEY', ...values('env_key')].map(name => process.platform === 'win32' ? name.toUpperCase() : name));
  return {
    login: [join(dir, 'auth.json')], settings: [join(dir, 'config.toml'), join(dir, 'AGENTS.md'), ...named],
    signsInWith: name => keys.has(name),
  };
}

/** The CLI's login environment and the operator's registrations; other credentials stay out. */
export function agentEnv(backend: Backend): Record<string, string> {
  const { signsInWith } = cliSetup(backend);
  const kept = Object.fromEntries(Object.entries(process.env).filter(([name, value]) => value !== undefined
    && (signsInWith(process.platform === 'win32' ? name.toUpperCase() : name) || !/^(GH_|GITHUB_)|^(SSH_AUTH_SOCK|GIT_ASKPASS)$|TOKEN|SECRET|KEY/i.test(name))));
  return mergeEnv(kept, credentials());
}

/** Where agents working in `cwd` keep `path`, a file of their home or of Argus's. */
export function privatePath(path: string, cwd: string): string {
  const workdir = encodeURIComponent(real(cwd)).replace(/%/g, '=');
  return join(real(argusHome()), 'sandbox', workdir, real(path));
}

/** An npm-installed CLI needs the node_modules it was installed into; anything else is one file. */
function installRoot(executable: string): string {
  const parts = executable.split(sep);
  const at = parts.indexOf('node_modules');
  return at >= 0 && at < parts.length - 1 ? parts.slice(0, at + 1).join(sep) : executable;
}

/** The interpreter a script's `#!` line names, e.g. node. */
function interpreter(executable: string): string[] {
  const head = Buffer.alloc(256);
  let line = '';
  try { const fd = openSync(executable, 'r'); line = head.toString('utf8', 0, readSync(fd, head)).split('\n')[0]!; closeSync(fd); } catch { return []; }
  let words = line.startsWith('#!') ? line.slice(2).trim().split(/\s+/) : [];
  if (words[0] && basename(words[0]) === 'env') words = words.slice(1).filter(word => !word.startsWith('-'));
  const found = words[0] ? which(words[0]) : null;
  return found ? [real(found)] : [];
}

/** A linked worktree's repository, whose history it reads but never edits. */
function gitCommonDir(cwd: string): string[] {
  const pointer = /^gitdir:\s*(.+)$/m.exec(read(join(cwd, '.git')))?.[1];
  if (!pointer) return [];
  const gitdir = resolve(cwd, pointer.trim());
  const common = read(join(gitdir, 'commondir')).trim();
  return [common ? resolve(gitdir, common) : gitdir];
}

const SYSTEM = ['/usr', '/bin', '/sbin', '/lib', '/lib32', '/lib64', '/libx32', '/etc', '/opt', '/sys'];

/** `argv` under bubblewrap, seeing only what the call is given. */
export function confine(argv: string[], request: AgentRequest, temp?: string): string[] {
  const [name, ...args] = argv;
  const found = which(name!);
  const executable = found ? real(found) : name!;
  const { login, settings } = cliSetup(request.backend);
  const cwd = resolve(request.cwd);
  const given = [cwd, ...request.readDirs ?? [], ...request.writeDirs ?? []].map(path => resolve(path));
  // Writable: the workdir and the directories given to write, to a role that writes. A directory given
  // to read inside one is mounted after it, being deeper, and read-only: Argus's record stays put.
  const writes = request.access === 'write' ? [cwd, ...request.writeDirs ?? []].map(path => resolve(path)) : [];
  // Files and directories the arguments name, such as the schema or extension a call writes.
  const passed = args.filter(arg => isAbsolute(arg) && existsSync(arg)).map(real);
  const readable = outermost([
    ...SYSTEM, installRoot(executable), ...interpreter(executable), ...gitCommonDir(cwd), ...passed,
    // The resolver file, where /etc/resolv.conf points outside /etc.
    ...[real('/etc/resolv.conf')].filter(target => target !== '/etc/resolv.conf' && isFile(target)),
  ]).filter(path => !given.some(dir => within(path, real(dir))));
  // (source, target, writable): deeper mounts cover shallower ones, and at equal depth a
  // private copy covers the real directory.
  const mounts: Array<[string, string, boolean]> = readable.map(path => [path, path, false]);
  for (const dir of outermost([homedir(), argusHome(), ...[...login, ...settings].map(dirname)].map(real))) {
    const copy = privatePath(dir, cwd);
    mkdirSync(copy, { recursive: true, mode: 0o700 });
    mounts.push(...namedAs(dir).map((target): [string, string, boolean] => [copy, target, true]));
  }
  // What a role downloads (a model, a package, a browser) the next project's roles find: they share
  // one cache, npm's too, Argus's own, not the machine's, which may hold its owner's tokens. So one project
  // reads what another left there, a token a CLI saves in its cache among it: projects are their
  // operator's alike, and none of them reaches the machine's.
  const cache = join(real(argusHome()), 'cache');
  mkdirSync(cache, { recursive: true, mode: 0o700 });
  mounts.push(...namedAs(join(homedir(), '.cache')).map((target): [string, string, boolean] => [cache, target, true]));
  const bind = (path: string, writable: boolean): void => {
    mounts.push(...namedAs(path).map((target): [string, string, boolean] => [real(path), target, writable]));
  };
  for (const file of login) bind(file, true);
  for (const file of settings) bind(file, false);
  for (const dir of given) bind(dir, writes.includes(dir));
  // A linked worktree's pointer stays put.
  if (isFile(join(cwd, '.git'))) bind(join(cwd, '.git'), false);
  const ordered = [...new Map(mounts.map(mount => [mount.join('\0'), mount])).values()]
    .sort((a, b) => depth(a[1]) - depth(b[1]) || Number(a[0] !== a[1]) - Number(b[0] !== b[1]));
  return [
    'bwrap', '--die-with-parent', '--new-session', '--unshare-pid', '--unshare-uts', '--unshare-ipc',
    '--proc', '/proc', '--dev-bind', '/dev', '/dev', '--tmpfs', '/dev/shm',
    ...temp ? ['--bind', temp, '/tmp'] : ['--tmpfs', '/tmp'],
    ...ordered.flatMap(([source, target, writable]) => [writable ? '--bind-try' : '--ro-bind-try', source, target]),
    // The sandbox ends with the CLI, and everything in it: a session's CLI outlives what it started.
    '--setenv', 'TMPDIR', '/tmp', '--setenv', 'XDG_CACHE_HOME', join(homedir(), '.cache'), '--setenv', 'npm_config_cache', join(homedir(), '.cache', 'npm'),
    '--chdir', cwd, '--', executable, ...args,
  ];
}
