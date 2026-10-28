// How an agent CLI starts and what it may see: under bubblewrap, the workdir and the directories
// its call is given, system software, the CLI itself, and a private home per workdir into which
// only the CLI's login and settings are brought. Its environment carries no credential but its own.

import { spawnSync } from 'node:child_process';
import { accessSync, closeSync, constants, mkdirSync, openSync, readFileSync, readSync, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, delimiter, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { argusHome } from '../store.js';
import type { AgentRequest, Backend } from '../types.js';


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

function which(name: string): string | null {
  // Windows finds a command by the extensions PATHEXT lists.
  const extensions = process.platform === 'win32' ? (process.env.PATHEXT ?? '.COM;.EXE;.BAT;.CMD').split(';') : [''];
  const candidates = name.includes(sep) ? [name]
    : (process.env.PATH ?? '').split(delimiter).filter(Boolean).flatMap(dir => extensions.map(extension => join(dir, name + extension)));
  for (const candidate of candidates) {
    try { accessSync(candidate, constants.X_OK); if (isFile(candidate)) return candidate; } catch { /* not this one */ }
  }
  return null;
}

/** How to start the CLI `name` with no shell. On Windows npm installs it as a .cmd shim, which only
 * cmd.exe runs, reparsing every argument: the shim's target is run instead, a script by this node. */
export function launcher(name: string): string[] {
  const found = which(name);
  if (!found || !/\.(cmd|bat)$/i.test(found)) return [found ?? name];
  const target = [...read(found).matchAll(/"%~?dp0%?\\([^"]+)"/g)].at(-1)?.[1];
  if (!target) return [found];
  const file = join(dirname(found), target);
  return /\.(exe|com)$/i.test(file) ? [file] : [process.execPath, file];
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
  const dir = process.env.CODEX_HOME || join(homedir(), '.codex');
  const config = read(join(dir, 'config.toml'));
  // Files the config names for codex to read before its first turn; one missing fails the call.
  const named = [...config.matchAll(
    /^\s*(?:model_catalog_json|model_instructions_file|experimental_compact_prompt_file|experimental_instructions_file)\s*=\s*"([^"]+)"/gm,
  )].map(([, value]) => value!.startsWith('~/') ? join(homedir(), value!.slice(2)) : resolve(dir, value!));
  const keys = new Set(['OPENAI_API_KEY', 'CODEX_API_KEY', ...[...config.matchAll(/^\s*env_key\s*=\s*"([^"]+)"/gm)].map(([, key]) => key!)]);
  return {
    login: [join(dir, 'auth.json')], settings: [join(dir, 'config.toml'), join(dir, 'AGENTS.md'), ...named],
    signsInWith: name => keys.has(name),
  };
}

/** The CLI's environment: push-capable and other credentials out, except those the CLI signs in with. */
export function agentEnv(backend: Backend): Record<string, string> {
  const { signsInWith } = cliSetup(backend);
  const kept: Record<string, string> = {};
  for (const [name, value] of Object.entries(process.env)) {
    if (value !== undefined && (signsInWith(name) || !/^(GH_|GITHUB_)|^(SSH_AUTH_SOCK|GIT_ASKPASS)$|TOKEN|SECRET|KEY/i.test(name))) kept[name] = value;
  }
  return kept;
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

// A bubblewrap sandbox ends with its first process, killing everything else in it: a job the
// CLI started detached would die with the CLI. This shell outlives the CLI until nothing else
// in the sandbox runs, then exits with the CLI's code: a turn ends when all it started has.
const WAIT_FOR_EVERYTHING = `
"$@"
code=$?
said=
while :; do
  others=
  for entry in /proc/[0-9]*; do
    pid=\${entry#/proc/}
    [ "$pid" = 1 ] || [ "$pid" = $$ ] || others="$others $pid"
  done
  [ -n "$others" ] || exit $code
  if [ -z "$said" ]; then
    said=1
    {
      printf '%s' '[argus-sandbox] the agent CLI exited; its turn runs on with what it started:'
      for pid in $others; do
        printf ' [%s] ' "$pid"; head -c 200 "/proc/$pid/cmdline" 2>/dev/null | tr '\\000\\n' '  '; printf ';'
      done
      echo
    } >&2
    exec </dev/null >/dev/null 2>&1
  fi
  sleep 5
done
`;

/** `argv` under bubblewrap, seeing only what the call is given. */
export function confine(argv: string[], request: AgentRequest): string[] {
  const [name, ...args] = argv;
  const found = which(name!);
  const executable = found ? real(found) : name!;
  const { login, settings } = cliSetup(request.backend);
  const cwd = resolve(request.cwd);
  const given = [cwd, ...request.readDirs ?? [], ...request.writeDirs ?? []].map(path => resolve(path));
  const writes = request.access === 'write' ? [cwd, ...request.writeDirs ?? []].map(real) : [];
  const files = args.filter(arg => isAbsolute(arg) && isFile(arg)).map(real);
  const readable = outermost([
    ...SYSTEM, installRoot(executable), ...interpreter(executable), ...gitCommonDir(cwd), ...files,
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
  const bind = (path: string, writable: boolean): void => {
    mounts.push(...namedAs(path).map((target): [string, string, boolean] => [real(path), target, writable]));
  };
  for (const file of login) bind(file, true);
  for (const file of settings) bind(file, false);
  for (const dir of given) bind(dir, writes.some(outer => within(real(dir), outer)));
  // A linked worktree's pointer stays put.
  if (isFile(join(cwd, '.git'))) bind(join(cwd, '.git'), false);
  const ordered = [...new Map(mounts.map(mount => [mount.join('\0'), mount])).values()]
    .sort((a, b) => depth(a[1]) - depth(b[1]) || Number(a[0] !== a[1]) - Number(b[0] !== b[1]));
  return [
    'bwrap', '--die-with-parent', '--new-session', '--unshare-pid', '--unshare-uts', '--unshare-ipc',
    '--proc', '/proc', '--dev-bind', '/dev', '/dev', '--tmpfs', '/dev/shm', '--tmpfs', '/tmp',
    ...ordered.flatMap(([source, target, writable]) => [writable ? '--bind-try' : '--ro-bind-try', source, target]),
    '--setenv', 'TMPDIR', '/tmp', '--chdir', cwd, '--', '/bin/sh', '-c', WAIT_FOR_EVERYTHING, 'argus-sandbox', executable, ...args,
  ];
}
