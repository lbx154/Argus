// A pack may need tools before its roles can work with it: a toolchain, a verifier, a checker. Its
// SKILL.md says so in its front matter (`setup: setup/install`), and Argus runs that script once for
// each version of the pack, as the operator who installed it, outside the sandbox. The script installs
// into ARGUS_PACK_HOME, a folder of the pack's own, and writes there `env`: KEY=VALUE lines that every
// role's commands then get, in which ${ARGUS_PACK_HOME}, ${WORKSPACE} (the project's workdir) and ${PATH}
// stand for what they say. Each role's sandbox holds that folder at the same path, writable to a role
// that writes. Only packs the operator installed have their setup run; the team's own skills never do.

import { spawn } from 'node:child_process';
import { accessSync, closeSync, constants, existsSync, mkdirSync, openSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { packsHome, purpose, SKILL } from './prompts.js';

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
    if (!statSync(dir, { throwIfNoEntry: false })?.isDirectory() || !existsSync(resolve(dir, SKILL))) return [];
    const setup = purpose(dir, 'setup');
    if (!setup) return [];
    const script = resolve(dir, setup);
    // The script is the pack's own file, never one outside it.
    if (relative(dir, script).startsWith('..')) return [];
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
  // A script the store kept without its executable bit runs with the interpreter its #! line names.
  let executable = true;
  try { accessSync(pack.script, constants.X_OK); } catch { executable = false; }
  const shebang = /^#!\s*(\S+)(?:\s+(\S+))?/.exec(readFileSync(pack.script, 'utf8').split('\n')[0] ?? '');
  const named = shebang ? (shebang[1]!.endsWith('/env') && shebang[2] ? [shebang[2]] : [shebang[1]!, ...shebang[2] ? [shebang[2]] : []]) : ['/bin/sh'];
  const [command, args] = executable ? [pack.script, []] : [named[0]!, [...named.slice(1), pack.script]];
  return new Promise<void>(done => {
    const child = spawn(command, args, {
      cwd: resolve(pack.script, '..'), stdio: ['ignore', log, log],
      env: { ...process.env, ARGUS_PACK_HOME: pack.home, ARGUS_PACK_DIR: resolve(pack.script, '..'), ARGUS_PACK_NAME: pack.name, ARGUS_PACK_VERSION: pack.version },
    });
    const end = (ok: boolean, why: string): void => {
      closeSync(log);
      writeFileSync(join(pack.home, ok ? READY : FAILED), `${new Date().toISOString()} ${why}\n`);
      done();
    };
    child.once('error', error => end(false, String(error)));
    child.once('exit', (code, signal) => end(code === 0, signal ? `signal ${signal}` : `exit ${code}`));
  });
}

/** The variables a ready pack's setup wrote, for a role working on `workspace`, over `base` (PATH from it). */
export function packEnv(packs: PackSetup[], workspace: string, base: Record<string, string | undefined>): Record<string, string> {
  const env: Record<string, string> = {};
  for (const pack of packs.filter(pack => pack.state === 'ready')) {
    let text = '';
    try { text = readFileSync(join(pack.home, 'env'), 'utf8'); } catch { continue; }
    for (const line of text.split('\n')) {
      const [, key, value] = /^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(line) ?? [];
      if (!key) continue;
      const path = env.PATH ?? base.PATH ?? '';
      env[key] = value!.replace(/^"(.*)"$/, '$1')
        .replace(/\$\{ARGUS_PACK_HOME\}/g, pack.home).replace(/\$\{WORKSPACE\}/g, workspace).replace(/\$\{PATH\}/g, path);
    }
  }
  return env;
}
