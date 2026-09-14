import { execFile } from 'node:child_process';
import {
  mkdir,
  readFile,
  writeFile,
  rename,
  unlink,
} from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, join, posix, resolve, win32 } from 'node:path';

// ── Public types ────────────────────────────────────────────────────────────

export interface ApiOwnershipRecord {
  schema: 1;
  /** PID reported by /api/meta (the process actually listening on the port). */
  pid: number;
  /** Launcher/process-tree root; differs from pid for Windows console scripts. */
  rootPid?: number;
  host: string;
  port: number;
  backendBin: string;
  startedAt: string;
}

export interface ProcessInspection {
  alive: boolean;
  argv: string[];
  /** Flat command line used on platforms that do not expose NUL-delimited argv. */
  commandLine?: string;
}

export interface ReadOwnedApiOptions {
  path: string;
  host: string;
  port: number;
  backendBin: string;
  /** Override the OS inspector for testing. */
  inspect?: (pid: number) => Promise<ProcessInspection>;
  /** Override platform dispatch for cross-platform ownership tests. */
  platform?: NodeJS.Platform;
}

export interface ClaimApiOwnershipOptions extends ReadOwnedApiOptions {
  pid: number;
  rootPid?: number;
  startedAt: string;
}

export function isLocalApiHost(host: string): boolean {
  const normalized = host.trim().toLowerCase();
  if (normalized === 'localhost' || normalized === '::1') return true;
  const octets = normalized.split('.').map(Number);
  return octets.length === 4
    && octets[0] === 127
    && octets.every((octet) => Number.isInteger(octet) && octet >= 0 && octet <= 255);
}

/**
 * Stable per-user ownership location for a local WebAPI endpoint.
 * An explicit ARGUS_TUI_API_OWNER_FILE may still override this at the CLI layer.
 */
export function defaultApiOwnershipPath(
  host: string,
  port: number,
  env: NodeJS.ProcessEnv = process.env,
): string | undefined {
  if (!isLocalApiHost(host)) return undefined;
  const configuredRoot = env.ARGUS_SKILL_HOME?.trim();
  const home = env.HOME?.trim() || homedir();
  const stateRoot = configuredRoot ? resolve(configuredRoot) : join(home, '.argus-skill');
  const endpoint = host.toLowerCase().replace(/[^a-z0-9._-]+/g, '_');
  return join(stateRoot, 'runtime', `webapi-${endpoint}-${port}.owner.json`);
}

// ── Default platform inspectors ─────────────────────────────────────────────

async function linuxInspect(pid: number): Promise<ProcessInspection> {
  // Liveness check: signal 0 throws when process does not exist.
  let alive = false;
  try {
    process.kill(pid, 0);
    alive = true;
  } catch {
    return { alive: false, argv: [] };
  }

  try {
    const raw = await readFile(`/proc/${pid}/cmdline`);
    // /proc/<pid>/cmdline is NUL-delimited; the last byte may also be NUL.
    const argv = raw.toString('utf8').split('\0').filter(Boolean);
    return { alive, argv };
  } catch {
    // Process may have exited between the kill(0) and the read.
    return { alive: false, argv: [] };
  }
}

async function darwinInspect(pid: number): Promise<ProcessInspection> {
  try {
    process.kill(pid, 0);
  } catch {
    return { alive: false, argv: [] };
  }

  return new Promise((resolveInspection) => {
    execFile(
      '/bin/ps',
      ['-ww', '-p', String(pid), '-o', 'command='],
      { encoding: 'utf-8' },
      (error, stdout) => {
        const commandLine = error ? '' : stdout.trim();
        resolveInspection({
          alive: Boolean(commandLine),
          argv: [],
          commandLine: commandLine || undefined,
        });
      },
    );
  });
}

async function windowsInspect(pid: number): Promise<ProcessInspection> {
  try {
    process.kill(pid, 0);
  } catch {
    return { alive: false, argv: [] };
  }

  const script = [
    "$ErrorActionPreference = 'Stop'",
    '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)',
    `$process = Get-CimInstance Win32_Process -Filter "ProcessId = ${pid}"`,
    'if ($null -eq $process) { exit 3 }',
    '[PSCustomObject]@{ commandLine = [string]$process.CommandLine } | ConvertTo-Json -Compress',
  ].join('; ');
  return new Promise((resolveInspection) => {
    execFile(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-Command', script],
      { encoding: 'utf-8', windowsHide: true },
      (error, stdout) => {
        if (error) {
          resolveInspection({ alive: false, argv: [] });
          return;
        }
        try {
          const body = JSON.parse(stdout.trim()) as { commandLine?: unknown };
          const commandLine = typeof body.commandLine === 'string'
            ? body.commandLine.trim()
            : '';
          resolveInspection({
            alive: Boolean(commandLine),
            argv: [],
            commandLine: commandLine || undefined,
          });
        } catch {
          resolveInspection({ alive: false, argv: [] });
        }
      },
    );
  });
}

export function inspectProcess(
  pid: number,
  platform: NodeJS.Platform = process.platform,
): Promise<ProcessInspection> {
  if (platform === 'win32') return windowsInspect(pid);
  if (platform === 'darwin') return darwinInspect(pid);
  return linuxInspect(pid);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function commandHasArgument(
  commandLine: string,
  argument: string,
  caseInsensitive: boolean,
): boolean {
  return new RegExp(
    `(?:^|\\s)["']?${escapeRegExp(argument)}["']?(?=\\s|$)`,
    caseInsensitive ? 'i' : '',
  ).test(commandLine);
}

function commandHasOptionValue(
  commandLine: string,
  option: string,
  value: string,
  caseInsensitive: boolean,
): boolean {
  return new RegExp(
    `(?:^|\\s)${escapeRegExp(option)}\\s+["']?${escapeRegExp(value)}["']?(?=\\s|$)`,
    caseInsensitive ? 'i' : '',
  ).test(commandLine);
}

// ── Public API ──────────────────────────────────────────────────────────────

/**
 * Write an ownership record atomically with mode 0600.
 * Uses a sibling temp file to guarantee the rename is atomic on Linux.
 */
export async function writeOwnershipRecord(
  path: string,
  record: ApiOwnershipRecord,
): Promise<void> {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const tmp = `${path}.tmp.${process.pid}`;
  await writeFile(tmp, JSON.stringify(record), { encoding: 'utf-8', mode: 0o600 });
  await rename(tmp, path);
}

// Backend launcher names that are the same install: `argus` and the pre-rename
// `argus-skill` beside it in one venv (2026-09-14 rename, kept one release).
// Ownership records written before the rename say `.../bin/argus-skill` and a
// backend started by that launcher still carries it in argv; the cockpit now
// resolves `.../bin/argus`. Without this equivalence every upgrade would fail
// closed with "ownership could not be proven" until the operator killed the
// old backend by hand.
const BACKEND_BIN_NAMES = ['argus', 'argus-skill'] as const;
const BACKEND_BIN_BASENAMES = new Set<string>(
  BACKEND_BIN_NAMES.flatMap((name) => [name, `${name}.exe`]),
);

function pathFor(platform: NodeJS.Platform) {
  return platform === 'win32' ? win32 : posix;
}

/** True for the same launcher file, or sibling `argus`/`argus-skill` launchers in one directory. */
export function sameBackendBin(
  left: string,
  right: string,
  platform: NodeJS.Platform = process.platform,
): boolean {
  const fold = (value: string) => (platform === 'win32' ? value.toLowerCase() : value);
  if (fold(left) === fold(right)) return true;
  const path = pathFor(platform);
  const leftBase = fold(path.basename(left));
  const rightBase = fold(path.basename(right));
  if (!BACKEND_BIN_BASENAMES.has(leftBase) || !BACKEND_BIN_BASENAMES.has(rightBase)) return false;
  return fold(path.dirname(left)) === fold(path.dirname(right));
}

/** `bin` plus its sibling launcher spellings, for matching a live process's argv. */
export function backendBinAliases(
  bin: string,
  platform: NodeJS.Platform = process.platform,
): string[] {
  const path = pathFor(platform);
  const base = path.basename(bin);
  const lower = base.toLowerCase();
  if (!BACKEND_BIN_BASENAMES.has(lower)) return [bin];
  const extension = lower.endsWith('.exe') ? base.slice(-4) : '';
  const directory = path.dirname(bin);
  const siblings = BACKEND_BIN_NAMES
    .map((name) => `${name}${extension}`)
    .filter((name) => name.toLowerCase() !== lower)
    .map((name) => path.join(directory, name));
  return [bin, ...siblings];
}

/** Verify that a live PID is exactly the local Argus WebAPI for this endpoint. */
export async function verifyApiProcess(
  opts: Omit<ReadOwnedApiOptions, 'path'> & { pid: number },
): Promise<boolean> {
  const { pid, host, port, backendBin } = opts;
  const platform = opts.platform ?? process.platform;
  const inspect = opts.inspect ?? ((candidate: number) => inspectProcess(candidate, platform));
  const caseInsensitive = platform === 'win32';
  const equals = (left: string, right: string) => (
    caseInsensitive ? left.toLowerCase() === right.toLowerCase() : left === right
  );
  if (!Number.isInteger(pid) || pid <= 0) return false;
  const backendBins = backendBinAliases(backendBin, platform);

  const { alive, argv, commandLine } = await inspect(pid);
  if (!alive) return false;
  if (argv.length > 0) {
    const indexOf = (argument: string) => argv.findIndex((value) => equals(value, argument));
    if (!backendBins.some((bin) => indexOf(bin) !== -1) || indexOf('--web') === -1) return false;
    const webPortIdx = indexOf('--web-port');
    if (webPortIdx === -1 || !equals(argv[webPortIdx + 1] ?? '', String(port))) return false;
    const webHostIdx = indexOf('--web-host');
    if (webHostIdx !== -1 && !equals(argv[webHostIdx + 1] ?? '', host)) return false;
    return true;
  }
  if (!commandLine) return false;
  if (
    !backendBins.some((bin) => commandHasArgument(commandLine, bin, caseInsensitive))
    || !commandHasArgument(commandLine, '--web', caseInsensitive)
  ) {
    return false;
  }
  if (!commandHasOptionValue(commandLine, '--web-port', String(port), caseInsensitive)) return false;
  if (
    commandHasArgument(commandLine, '--web-host', caseInsensitive)
    && !commandHasOptionValue(commandLine, '--web-host', host, caseInsensitive)
  ) return false;
  return true;
}

/**
 * Bootstrap an ownership record from a WebAPI's self-reported PID only after
 * independently verifying its live process command line.
 */
export async function claimApiOwnership(opts: ClaimApiOwnershipOptions): Promise<boolean> {
  const verified = await verifyApiProcess({
    pid: opts.pid,
    host: opts.host,
    port: opts.port,
    backendBin: opts.backendBin,
    inspect: opts.inspect,
    platform: opts.platform,
  });
  if (!verified) return false;
  await writeOwnershipRecord(opts.path, {
    schema: 1,
    pid: opts.pid,
    ...(opts.rootPid === undefined ? {} : { rootPid: opts.rootPid }),
    host: opts.host,
    port: opts.port,
    backendBin: opts.backendBin,
    startedAt: opts.startedAt,
  });
  return true;
}

/**
 * Remove an ownership file. Returns silently if the file does not exist.
 */
export async function removeOwnershipRecord(path: string): Promise<void> {
  try {
    await unlink(path);
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code !== 'ENOENT') throw err;
  }
}

/**
 * Read and verify an ownership record.  Returns the record when all of the
 * following hold:
 *   - schema === 1
 *   - pid is a positive integer and the process is alive
 *   - host and port match the requested endpoint
 *   - backendBin matches the requested binary
 *   - argv contains the binary, `--web`, and `--web-port <port>`
 *
 * Returns null for any read, parse, inspection, or validation failure
 * (fail-closed).
 */
export async function readOwnedApi(
  opts: ReadOwnedApiOptions,
): Promise<ApiOwnershipRecord | null> {
  const { path, host, port, backendBin } = opts;
  const platform = opts.platform ?? process.platform;
  const inspect = opts.inspect ?? ((candidate: number) => inspectProcess(candidate, platform));

  try {
    const raw = await readFile(path, 'utf-8');
    const record = JSON.parse(raw) as Partial<ApiOwnershipRecord>;

    // Schema
    if (record.schema !== 1) return null;

    // PID
    const pid = record.pid;
    if (typeof pid !== 'number' || !Number.isInteger(pid) || pid <= 0) return null;
    if (
      record.rootPid !== undefined
      && (
        typeof record.rootPid !== 'number'
        || !Number.isInteger(record.rootPid)
        || record.rootPid <= 0
      )
    ) return null;

    // Endpoint
    if (record.host !== host) return null;
    if (record.port !== port) return null;

    // Backend binary: the same launcher, or its pre-rename sibling in the same venv.
    if (typeof record.backendBin !== 'string') return null;
    if (!sameBackendBin(record.backendBin, backendBin, platform)) return null;

    if (!await verifyApiProcess({ pid, host, port, backendBin, inspect, platform })) return null;

    // A corrupted or obsolete optional launcher PID must never authorize a
    // signal to an unrelated process. Keep ownership of the verified listener,
    // but discard the launcher unless its command line independently matches
    // this exact WebAPI endpoint.
    if (
      record.rootPid !== undefined
      && record.rootPid !== pid
      && !await verifyApiProcess({
        pid: record.rootPid,
        host,
        port,
        backendBin,
        inspect,
        platform,
      })
    ) {
      const { rootPid: _unsafeRootPid, ...listenerRecord } = record;
      return listenerRecord as ApiOwnershipRecord;
    }

    return record as ApiOwnershipRecord;
  } catch {
    return null;
  }
}
