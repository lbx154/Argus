// A pack may need tools before its roles can work with it: a toolchain, a verifier, a checker. Its
// SKILL.md says so in its front matter (`setup: setup/install`), and Argus runs that script once for
// each version of the pack, as the operator who installed it, outside the sandbox. The script installs
// into ARGUS_PACK_HOME, a folder of the pack's own, and writes there `env`: KEY=VALUE lines that every
// role's commands then get, in which ${ARGUS_PACK_HOME}, ${WORKSPACE} (the project's workdir) and ${PATH}
// stand for what they say. Each role's sandbox holds that folder at the same path, writable to a role
// that writes. Only packs the operator installed have their setup run; the team's own skills never do.

import { spawn } from 'node:child_process';
import { accessSync, closeSync, constants, existsSync, lstatSync, mkdirSync, openSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync, writeSync } from 'node:fs';
import { basename, delimiter, dirname, extname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { packsHome, purpose, SKILL } from './prompts.js';
import { envValue, findExecutable, mergeEnv } from './commands.js';

/** A pack whose SKILL.md names a setup script, and how its setup stands. */
export interface PackSetup {
  name: string;
  version: string;
  /** ARGUS_PACK_HOME: where the setup installs, and its `env`, `setup.log` and state are kept. */
  home: string;
  state: 'ready' | 'failed' | 'pending';
  /** The setup's output, kept for the operator and the roles to read. */
  log: string;
}

const READY = '.ready';
const FAILED = '.failed';
const safe = (part: string): string => part.replace(/[^A-Za-z0-9._-]/g, '_') || '_';

/** The operator's installed packs that name a setup script, and how each one's setup stands now. */
export function packSetups(home: string): (PackSetup & { script: string })[] {
  const root = packsHome(home);
  return readdirSync(root).flatMap(name => {
    const dir = resolve(root, name);
    const windows = process.platform === 'win32';
    if (!(windows ? lstatSync : statSync)(dir, { throwIfNoEntry: false })?.isDirectory()
      || !(windows ? lstatSync(resolve(dir, SKILL), { throwIfNoEntry: false })?.isFile() : existsSync(resolve(dir, SKILL)))) return [];
    const setup = purpose(dir, 'setup');
    if (!setup) return [];
    const script = resolve(dir, setup);
    // The script is the pack's own file, never one outside it.
    const under = relative(dir, script);
    if (under.startsWith('..')) return [];
    if (windows) {
      if (!under || isAbsolute(under)) return [];
      // Native dispatch must not turn a Windows junction or link into another setup source.
      // POSIX keeps its existing lexical boundary and dispatch rules.
      let part = dir;
      for (const name of under.split(sep)) {
        part = join(part, name);
        const entry = lstatSync(part, { throwIfNoEntry: false });
        if (entry && !(part === script ? entry.isFile() : entry.isDirectory())) return [];
      }
    }
    const version = purpose(dir, 'version') || '0';
    const packHome = resolve(home, 'pack-homes', safe(name), safe(version));
    const state = existsSync(join(packHome, READY)) ? 'ready' : existsSync(join(packHome, FAILED)) ? 'failed' : 'pending';
    return [{ name, version, home: packHome, script, state, log: join(packHome, 'setup.log') }];
  });
}

/** The setups under way in this process, by pack home. */
const running = new Map<string, Promise<void>>();

/** Runs every installed pack's setup that has not run for its version, one pack at a time, each
 * once in this process; resolves with every setup as it then stands. A failed one is tried again
 * when Argus next starts. */
export async function prepare(home: string): Promise<PackSetup[]> {
  for (const pack of packSetups(home)) {
    if (pack.state === 'ready') continue;
    if (pack.state === 'failed' && !running.has(pack.home)) continue;
    let going = running.get(pack.home);
    if (!going) {
      going = install(pack);
      running.set(pack.home, going);
    }
    await going;
  }
  return packSetups(home).map(({ script: _, ...pack }) => pack);
}

/** A failed setup is tried once more, by the next process. */
export function forgetFailures(home: string): void {
  for (const pack of packSetups(home)) if (pack.state === 'failed') rmSync(join(pack.home, FAILED), { force: true });
}

function install(pack: PackSetup & { script: string }): Promise<void> {
  mkdirSync(pack.home, { recursive: true });
  rmSync(join(pack.home, FAILED), { force: true });
  const log = openSync(pack.log, 'w');
  return new Promise<void>(done => {
    const end = (ok: boolean, why: string): void => {
      if (!ok) writeSync(log, `Setup failed: ${why}\n`);
      closeSync(log);
      writeFileSync(join(pack.home, ok ? READY : FAILED), `${new Date().toISOString()} ${why}\n`);
      done();
    };
    try {
      const batch = process.platform === 'win32' && /\.(cmd|bat)$/i.test(pack.script);
      // Only a batch setup needs cmd. Its one path comes from an environment variable, expanded
      // once inside quotes, not interpolated into shell syntax (spaces, %, & and ! stay literal).
      const [command, ...args] = batch
        ? [process.env.ComSpec ?? 'cmd.exe', '/d', '/v:off', '/s', '/c', '""%ARGUS_SETUP_SCRIPT%""']
        : setupCommand(pack.script);
      const child = spawn(command!, args, {
        cwd: dirname(pack.script), stdio: ['ignore', log, log], windowsVerbatimArguments: batch,
        env: mergeEnv(process.env, { ARGUS_SETUP_SCRIPT: pack.script, ARGUS_PACK_HOME: pack.home, ARGUS_PACK_DIR: dirname(pack.script), ARGUS_PACK_NAME: pack.name, ARGUS_PACK_VERSION: pack.version }),
      });
      let error: Error | undefined;
      child.once('error', why => { error = why; });
      child.once('close', (code, signal) => end(!error && code === 0, error ? `${command}: ${error.message}` : signal ? `signal ${signal}` : `exit ${code}`));
    } catch (error) { end(false, String(error)); }
  });
}

/** The store has no execute bits. Windows has no shebang dispatch either: Node scripts use the
 * runtime already running Argus, other scripts need the interpreter they name on PATH. */
function setupCommand(script: string): string[] {
  if (process.platform !== 'win32') {
    // Preserve POSIX dispatch: execute bits first, then its existing shebang/default-sh contract.
    try { accessSync(script, constants.X_OK); return [script]; } catch { /* use its interpreter */ }
    const shebang = /^#!\s*(\S+)(?:\s+(\S+))?/.exec(readFileSync(script, 'utf8').split('\n')[0] ?? '');
    const named = shebang ? (shebang[1]!.endsWith('/env') && shebang[2] ? [shebang[2]] : [shebang[1]!, ...shebang[2] ? [shebang[2]] : []]) : ['/bin/sh'];
    return [...named, script];
  }
  const extension = extname(script).toLowerCase();
  if (['.js', '.mjs', '.cjs'].includes(extension)) return [process.execPath, script];
  if (['.exe', '.com'].includes(extension)) return [script];
  const shebang = /^#!\s*(\S+)(?:\s+([^\r\n]+))?/.exec(readFileSync(script, 'utf8').split('\n')[0] ?? '');
  let interpreter = shebang?.[1] ?? '/bin/sh';
  let args = shebang?.[2]?.trim().split(/\s+/) ?? [];
  if (interpreter.endsWith('/env')) {
    if (args[0] === '-S') args = args.slice(1);
    interpreter = args.shift() ?? '';
  }
  if (basename(interpreter) === 'node') return [process.execPath, ...args, script];
  interpreter = basename(interpreter);
  const found = findExecutable(interpreter);
  if (!found) throw new Error(`Setup interpreter ${interpreter} is unavailable (not found on PATH or not executable). Install it or use a Node setup (.cjs/.mjs).`);
  return [found, ...args, script];
}

/** The variables a ready pack's setup wrote, for a role working on `workspace`, over `base` (PATH from it).
 * ${PATH} is native; a pack may use ${PATH_SEPARATOR} to prepend entries portably. */
export function packEnv(packs: PackSetup[], workspace: string, base: Record<string, string | undefined>): Record<string, string> {
  let env: Record<string, string> = {};
  for (const pack of packs.filter(pack => pack.state === 'ready')) {
    let text = '';
    try { text = readFileSync(join(pack.home, 'env'), 'utf8'); } catch { continue; }
    for (const line of text.split(/\r?\n/)) {
      const [, key, value] = /^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(line) ?? [];
      if (!key) continue;
      const path = envValue(env, 'PATH') ?? envValue(base, 'PATH') ?? '';
      const expanded = value!.replace(/^"(.*)"$/, '$1')
        .replace(/\$\{ARGUS_PACK_HOME\}/g, () => pack.home).replace(/\$\{WORKSPACE\}/g, () => workspace)
        .replace(/\$\{PATH_SEPARATOR\}/g, delimiter).replace(/\$\{PATH\}/g, () => path);
      env = mergeEnv(env, { [process.platform === 'win32' && key.toUpperCase() === 'PATH' ? 'PATH' : key]: expanded });
    }
  }
  return env;
}
