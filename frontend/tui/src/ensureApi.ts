import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { debuglog } from 'node:util';
import {
  describeApiRuntime,
  inspectApiMeta,
  RELEASE_ARTIFACT_DRIFT_WARNING,
  type ApiRuntimeExpectation,
  type ApiMeta,
} from '../../core/src/protocol.js';
import { RELEASE_ID } from '../../core/src/release.generated.js';
import type { ProjectRow } from '../../core/src/types.js';
import {
  claimApiOwnership as claimApiOwnershipImpl,
  isLocalApiHost,
  readOwnedApi as readOwnedApiImpl,
  writeOwnershipRecord as writeOwnershipRecordImpl,
  type ApiOwnershipRecord,
} from './apiOwnership.js';
import { requestWithTimeout } from './network.js';

const debugArgus = debuglog('argus');

/**
 * Make `argus` a true one-command launch: if the backend API isn't up, start
 * `argus-skill --web` ourselves, wait for it, then connect. This is why the
 * launch command can just be `argus`.
 *
 * Binary resolution (this box has SEVERAL argus-skill installs on PATH, most of
 * them older checkouts WITHOUT the `--web` flag): prefer ARGUS_SKILL_BIN, then
 * the repo's own `.venv/bin/argus-skill` (the one this frontend ships beside —
 * the base runtime includes the WebAPI used by the cockpit), and only fall back
 * to bare `argus-skill` on PATH.
 */

export function resolveBin(): string {
  if (process.env.ARGUS_SKILL_BIN) return process.env.ARGUS_SKILL_BIN;
  // this file lives at <repo>/frontend/tui/{src|dist}/ensureApi — the repo venv
  // is three levels up.
  const here = dirname(fileURLToPath(import.meta.url));
  const repo = resolve(here, '..', '..', '..');
  const repoBin = repoBackendPath(repo);
  if (existsSync(repoBin)) return repoBin;
  return 'argus-skill';
}

export function repoBackendPath(
  repo: string,
  platform: NodeJS.Platform = process.platform,
): string {
  return platform === 'win32'
    ? resolve(repo, '.venv', 'Scripts', 'argus-skill.exe')
    : resolve(repo, '.venv', 'bin', 'argus-skill');
}

export interface ApiProbeResult {
  state: 'compatible' | 'incompatible' | 'unreachable';
  message: string;
  warning?: string;
  meta?: ApiMeta;
}

function startupPollAttempts(platform: NodeJS.Platform = process.platform): number {
  return platform === 'win32' ? 60 : 20;
}

function startupPollDeadline(platform: NodeJS.Platform = process.platform): number {
  return Date.now() + (platform === 'win32' ? 30_000 : 10_000);
}

interface SpawnedApiProcess {
  pid: number;
  exited?: Promise<number | null>;
}

function spawnDetachedApi(bin: string, host: string, port: number, token?: string): SpawnedApiProcess {
  const child = spawn(bin, ['--web', '--web-host', host, '--web-port', String(port)], {
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
    env: spawnEnv(token),
  });
  const exited = new Promise<number | null>((resolveExit) => {
    child.once('exit', (code) => resolveExit(code));
    child.once('error', () => resolveExit(-1));
  });
  child.unref();
  return { pid: child.pid!, exited };
}

async function waitForStartupPoll(
  spawned: SpawnedApiProcess,
  sleep: (ms: number) => Promise<void>,
): Promise<number | null | undefined> {
  if (!spawned.exited) {
    await sleep(500);
    return undefined;
  }
  return Promise.race([
    sleep(500).then(() => undefined),
    spawned.exited,
  ]);
}

function localRuntimeExpectation(
  env: NodeJS.ProcessEnv = process.env,
): ApiRuntimeExpectation {
  const sourceDigest = env.ARGUS_TUI_LOCAL_SOURCE_DIGEST?.trim();
  return {
    releaseId: env.ARGUS_TUI_LOCAL_RELEASE_ID?.trim() || RELEASE_ID,
    sourceDigest: sourceDigest || undefined,
  };
}

export async function probeApi(
  host: string,
  port: number,
  token?: string,
): Promise<ApiProbeResult> {
  try {
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    return await requestWithTimeout(
      `http://${host}:${port}/api/meta`,
      { headers },
      1_200,
      async (r): Promise<ApiProbeResult> => {
        if (!r.ok) {
          const suffix = r.status === 404
            ? 'service does not expose /api/meta; it is an older Argus checkout or another process'
            : `GET /api/meta returned HTTP ${r.status}`;
          return { state: 'incompatible', message: suffix };
        }
        let body: unknown;
        try {
          body = await r.json();
        } catch (error) {
          if (!(error instanceof SyntaxError)) throw error;
          return { state: 'incompatible', message: 'backend returned malformed /api/meta JSON' };
        }
        const expectation = localRuntimeExpectation();
        const compatibility = inspectApiMeta(body, expectation);
        if (!compatibility.compatible || !compatibility.meta) {
          return {
            state: 'incompatible',
            message: compatibility.reason,
            meta: compatibility.meta,
          };
        }
        let warning = compatibility.warning;
        if (warning === RELEASE_ARTIFACT_DRIFT_WARNING && expectation.sourceDigest) {
          debugArgus('local source checkout has release artifact drift: %s', warning);
          warning = undefined;
        }
        return {
          state: 'compatible',
          message: describeApiRuntime(compatibility.meta),
          warning,
          meta: compatibility.meta,
        };
      },
    );
  } catch (error) {
    return {
      state: 'unreachable',
      message: error instanceof Error ? error.message : String(error),
    };
  }
}

export function uniqueWarningReporter(
  report: (warning: string) => void,
): (warning: string) => void {
  const seen = new Set<string>();
  return (warning: string) => {
    const normalized = warning.trim();
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    report(normalized);
  };
}

export interface EnsureResult {
  reachable: boolean;
  spawned: boolean;
  message: string;
  warning?: string;
  /** Exact record written for an API process tree created by this invocation. */
  spawnedApi?: SpawnedApiReceipt;
}

export interface SpawnedApiReceipt {
  ownerFile: string;
  ownership: ApiOwnershipRecord;
}

export interface SpawnedApiCleanupResult {
  stopped: boolean;
  message: string;
}

export interface DaemonUpgradeScheduleSummary {
  outdated: string[];
  scheduled: string[];
  skipped: string[];
  failed: string[];
}

export async function scheduleOutdatedDaemonUpgrades(
  projects: ProjectRow[],
  schedule: (sid: string) => Promise<{ scheduled?: unknown }>,
): Promise<DaemonUpgradeScheduleSummary> {
  const outdated = projects
    .filter((project) =>
      project.daemon_upgrade_pending === true
      || (
        project.daemon_alive
        && project.daemon_protocol_compatible === false
        && project.daemon_source_owned === true
      ))
    .map((project) => project.id);
  const settled = await Promise.allSettled(outdated.map((sid) => schedule(sid)));
  const accepted = settled.map((result) =>
    result.status === 'fulfilled' && result.value.scheduled === true);
  return {
    outdated,
    scheduled: outdated.filter((_, index) => accepted[index]),
    skipped: outdated.filter((_, index) =>
      settled[index].status === 'fulfilled' && !accepted[index]),
    failed: outdated.filter((_, index) => settled[index].status === 'rejected'),
  };
}

function compatibleResult(
  probe: ApiProbeResult,
  options: {
    spawned: boolean;
    prefix: string;
    onWarning?: (warning: string) => void;
    spawnedApi?: SpawnedApiReceipt;
  },
): EnsureResult {
  const { spawned, prefix, onWarning, spawnedApi } = options;
  if (probe.warning) onWarning?.(probe.warning);
  return {
    reachable: true,
    spawned,
    message: `${prefix} · ${probe.message}`,
    warning: probe.warning,
    ...(spawnedApi ? { spawnedApi } : {}),
  };
}

/** Environment for a backend we start ourselves.
 *
 * The backend has to accept the very token we are going to probe and pair
 * with. On a non-loopback bind that token may have been minted for this run,
 * so it exists nowhere else — passing it down is what keeps the spawned API
 * reachable instead of answering 401 to its own cockpit. */
function spawnEnv(token?: string): NodeJS.ProcessEnv {
  if (!token?.trim()) return process.env;
  return { ...process.env, ARGUS_SKILL_WEB_TOKEN: token.trim() };
}

function spawnedOwnershipRecord(
  probe: ApiProbeResult,
  rootPid: number,
  host: string,
  port: number,
  backendBin: string,
): ApiOwnershipRecord {
  return {
    schema: 1,
    pid: probe.meta?.runtime.pid ?? rootPid,
    rootPid,
    host,
    port,
    backendBin,
    startedAt: probe.meta?.runtime.started_at || new Date().toISOString(),
  };
}

function sendSigterm(
  pids: Array<number | undefined>,
  signal: (pid: number, signal: NodeJS.Signals) => void,
): { delivered: number; errors: Error[] } {
  let delivered = 0;
  const errors: Error[] = [];
  const validPids = pids.filter((value): value is number => (
    typeof value === 'number' && Number.isInteger(value) && value > 0
  ));
  for (const pid of new Set(validPids)) {
    try {
      signal(pid, 'SIGTERM');
      delivered += 1;
    } catch (error) {
      errors.push(error instanceof Error ? error : new Error(String(error)));
    }
  }
  return { delivered, errors };
}

function exactOwnershipMatch(
  actual: ApiOwnershipRecord,
  expected: ApiOwnershipRecord,
): boolean {
  return actual.schema === expected.schema
    && actual.pid === expected.pid
    && actual.rootPid === expected.rootPid
    && actual.host === expected.host
    && actual.port === expected.port
    && actual.backendBin === expected.backendBin
    && actual.startedAt === expected.startedAt;
}

/**
 * Stop only the API process tree created by this exact TUI invocation.
 *
 * Cleanup fails closed unless the endpoint is loopback, the on-disk owner
 * record still exactly matches the spawn receipt, both Windows listener/root
 * PIDs still verify as this backend, and /api/meta confirms the same runtime
 * PID and start identity. This prevents stale records or PID reuse from
 * authorising a signal to an unrelated process.
 */
export async function cleanupSpawnedApi(opts: {
  result: EnsureResult;
  token?: string;
  dependencies?: {
    readOwnedApi?: (receipt: SpawnedApiReceipt) => Promise<ApiOwnershipRecord | null>;
    probeApi?: (receipt: SpawnedApiReceipt) => Promise<ApiProbeResult>;
    signal?: (pid: number, signal: NodeJS.Signals) => void;
  };
}): Promise<SpawnedApiCleanupResult> {
  const receipt = opts.result.spawnedApi;
  if (!opts.result.spawned || !receipt) {
    return { stopped: false, message: 'API was not safely owned by this invocation' };
  }
  const expected = receipt.ownership;
  if (!isLocalApiHost(expected.host)) {
    return { stopped: false, message: 'refused to stop a non-local API endpoint' };
  }

  const readOwned = opts.dependencies?.readOwnedApi ?? (() => readOwnedApiImpl({
    path: receipt.ownerFile,
    host: expected.host,
    port: expected.port,
    backendBin: expected.backendBin,
  }));
  const actual = await readOwned(receipt);
  if (!actual || !exactOwnershipMatch(actual, expected)) {
    return { stopped: false, message: 'API ownership changed; no process was signalled' };
  }

  const inspectEndpoint = opts.dependencies?.probeApi
    ?? (() => probeApi(expected.host, expected.port, opts.token));
  const endpoint = await inspectEndpoint(receipt);
  if (
    !endpoint.meta
    || endpoint.meta.runtime.pid !== expected.pid
    || endpoint.meta.runtime.started_at !== expected.startedAt
  ) {
    return { stopped: false, message: 'API runtime identity changed; no process was signalled' };
  }

  const signal = opts.dependencies?.signal ?? ((pid: number, sig: NodeJS.Signals) => {
    process.kill(pid, sig);
  });
  const termination = sendSigterm([expected.pid, expected.rootPid], signal);
  if (termination.errors.length > 0) {
    return {
      stopped: termination.delivered > 0,
      message: `API cleanup signalled ${termination.delivered} process(es); ${termination.errors[0].message}`,
    };
  }
  return {
    stopped: termination.delivered > 0,
    message: `stopped owned API process tree (${termination.delivered} process${termination.delivered === 1 ? '' : 'es'})`,
  };
}

export async function ensureApi(opts: {
  host: string;
  port: number;
  token?: string;
  autostart?: boolean;
  ownerFile?: string;
  onStatus?: (s: string) => void;
  onWarning?: (warning: string) => void;
  dependencies?: {
    probeApi: () => Promise<ApiProbeResult>;
    readOwnedApi?: () => Promise<ApiOwnershipRecord | null>;
    claimApiOwnership?: (path: string, record: ApiOwnershipRecord) => Promise<boolean>;
    signal?: (pid: number, signal: NodeJS.Signals) => void;
    spawnApi?: () => Promise<SpawnedApiProcess>;
    writeOwnershipRecord?: (path: string, record: ApiOwnershipRecord) => Promise<void>;
    sleep?: (ms: number) => Promise<void>;
  };
}): Promise<EnsureResult> {
  const {
    host,
    port,
    token,
    autostart = true,
    ownerFile,
    onStatus,
    onWarning,
    dependencies: deps,
  } = opts;

  const doProbe = deps?.probeApi ?? (() => probeApi(host, port, token));
  const doSleep = deps?.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const local = isLocalApiHost(host);

  onStatus?.('checking local and running versions…');
  const initial = await doProbe();
  if (initial.state === 'compatible') {
    // A compatible local API may predate ownership-by-default. Adopt it only
    // when its self-reported PID and live argv prove that it is the exact
    // backend binary and endpoint this CLI would launch.
    if (ownerFile && local && initial.meta) {
      const bin = resolveBin();
      const readOwned = deps?.readOwnedApi ?? (() =>
        readOwnedApiImpl({ path: ownerFile, host, port, backendBin: bin }));
      const existing = await readOwned();
      const ownsCurrentApi = existing?.pid === initial.meta.runtime.pid;
      if (!ownsCurrentApi) {
        const record: ApiOwnershipRecord = {
          schema: 1,
          pid: initial.meta.runtime.pid,
          host,
          port,
          backendBin: bin,
          startedAt: initial.meta.runtime.started_at || new Date().toISOString(),
        };
        const claim = deps?.claimApiOwnership ?? ((path, candidate) =>
          claimApiOwnershipImpl({ path, ...candidate }));
        try {
          const claimed = await claim(ownerFile, record);
          if (!claimed) onWarning?.('local API is compatible but ownership could not be verified; automatic upgrade is disabled for this process');
        } catch (error) {
          onWarning?.(`could not record local API ownership: ${(error as Error).message}`);
        }
      }
    }
    return compatibleResult(initial, {
      spawned: false,
      prefix: 'api up',
      onWarning,
    });
  }
  if (initial.state === 'incompatible') {
    onStatus?.('version mismatch; verifying safe restart ownership…');
    if (!ownerFile) {
      return {
        reachable: false,
        spawned: false,
        message: `incompatible Argus API at ${host}:${port}: ${initial.message}. Stop that WebAPI or choose another port.`,
      };
    }

    // Guard: only recover for local hosts — never inspect, signal, or spawn for a remote endpoint.
    if (!local) {
      return {
        reachable: false,
        spawned: false,
        message: `incompatible Argus API at ${host}:${port}: ${initial.message}. Stop that WebAPI or choose another port.`,
      };
    }

    // Recovery: verify ownership then replace the stale API.
    const bin = resolveBin();
    const doReadOwned = deps?.readOwnedApi ?? (() =>
      readOwnedApiImpl({ path: ownerFile, host, port, backendBin: bin }));
    const doSignal = deps?.signal ?? ((pid: number, sig: NodeJS.Signals) => {
      process.kill(pid, sig);
    });
    const doWriteOwnership = deps?.writeOwnershipRecord ??
      ((p: string, r: ApiOwnershipRecord) => writeOwnershipRecordImpl(p, r));

    let record = await doReadOwned();
    if (record && initial.meta && record.pid !== initial.meta.runtime.pid) {
      record = null;
    }
    if (!record && initial.meta) {
      const candidate: ApiOwnershipRecord = {
        schema: 1,
        pid: initial.meta.runtime.pid,
        host,
        port,
        backendBin: bin,
        startedAt: initial.meta.runtime.started_at || new Date().toISOString(),
      };
      const claim = deps?.claimApiOwnership ?? ((path, value) =>
        claimApiOwnershipImpl({ path, ...value }));
      try {
        if (await claim(ownerFile, candidate)) record = candidate;
      } catch {
        // Fall through to the same fail-closed ownership error below.
      }
    }
    if (!record) {
      return {
        reachable: false,
        spawned: false,
        message: `incompatible Argus API at ${host}:${port}: ${initial.message} — ownership could not be proven`,
      };
    }

    // SIGTERM only — never escalate to SIGKILL. A Windows console-script
    // launcher and its Python listener have different PIDs, so signal both
    // verified members of the ownership record.
    onStatus?.('restarting outdated owned backend…');
    let shutdown = false;
    const termination = sendSigterm([record.pid, record.rootPid], doSignal);
    if (termination.delivered === 0 && termination.errors.length > 0) {
      const afterSignalFailure = await doProbe();
      if (afterSignalFailure.state === 'unreachable') {
        shutdown = true;
      } else {
        return {
          reachable: false,
          spawned: false,
          message:
            `incompatible Argus API at ${host}:${port}: could not signal owned pid ${record.pid}` +
            ` (${termination.errors[0].message})`,
        };
      }
    }
    onStatus?.('waiting for stale backend to shut down…');

    // Probe every 250 ms for at most 8 seconds.
    for (let i = 0; !shutdown && i < 32; i++) {
      await doSleep(250);
      const probe = await doProbe();
      if (probe.state === 'unreachable') {
        shutdown = true;
        break;
      }
    }

    if (!shutdown) {
      return {
        reachable: false,
        spawned: false,
        message: `incompatible Argus API at ${host}:${port}: graceful shutdown timed out after SIGTERM`,
      };
    }

    // Spawn replacement backend.
    const doSpawn = deps?.spawnApi ?? (async () => spawnDetachedApi(bin, host, port, token));

    onStatus?.('starting backend api…');
    const spawned = await doSpawn();

    // Poll for the new backend to come online.
    const replacementDeadline = startupPollDeadline();
    for (
      let i = 0;
      i < startupPollAttempts() && Date.now() < replacementDeadline;
      i++
    ) {
      const exitCode = await waitForStartupPoll(spawned, doSleep);
      if (exitCode !== undefined) {
        const competing = await doProbe();
        if (competing.state === 'compatible') {
          return compatibleResult(competing, {
            spawned: false,
            prefix: 'api up',
            onWarning,
          });
        }
        return {
          reachable: false,
          spawned: true,
          message: `replacement backend exited before becoming ready (exit ${exitCode ?? 'unknown'})`,
        };
      }
      const probe = await doProbe();
      if (probe.state === 'compatible') {
        const ownership = spawnedOwnershipRecord(probe, spawned.pid, host, port, bin);
        try {
          await doWriteOwnership(ownerFile, ownership);
        } catch (writeErr) {
          sendSigterm([ownership.pid, ownership.rootPid], doSignal);
          return {
            reachable: false,
            spawned: false,
            message:
              `incompatible Argus API at ${host}:${port}: ownership write failed after spawn` +
              ` (${(writeErr as Error).message}); sent SIGTERM to spawned process tree`,
          };
        }
        return compatibleResult(probe, {
          spawned: true,
          prefix: 'api started',
          onWarning,
          spawnedApi: { ownerFile, ownership },
        });
      }
      if (probe.state === 'incompatible') {
        sendSigterm([spawned.pid], doSignal);
        return {
          reachable: false,
          spawned: true,
          message: `port ${port} is occupied by an incompatible Argus API: ${probe.message}`,
        };
      }
      onStatus?.(`starting backend api… ${i + 1}`);
    }
    sendSigterm([spawned.pid], doSignal);
    return {
      reachable: false,
      spawned: true,
      message: `started backend but it did not come online at ${host}:${port}`,
    };
  }

  // Unreachable — try to auto-start a local API.
  if (!autostart || !local) {
    return {
      reachable: false,
      spawned: false,
      message: `no API at ${host}:${port} — start it with:  argus-skill --web --web-port ${port}`,
    };
  }

  onStatus?.('starting backend api…');
  const bin = resolveBin();

  const doNormalSpawn = deps?.spawnApi ??
    (async () => spawnDetachedApi(bin, host, port, token));

  const doNormalSignal = deps?.signal ?? ((pid: number, sig: NodeJS.Signals) => {
    process.kill(pid, sig);
  });
  const doNormalWriteOwnership = deps?.writeOwnershipRecord ??
    ((p: string, r: ApiOwnershipRecord) => writeOwnershipRecordImpl(p, r));

  let spawned: SpawnedApiProcess;
  try {
    spawned = await doNormalSpawn();
  } catch (err) {
    return {
      reachable: false,
      spawned: false,
      message:
        `could not launch '${bin} --web' (${(err as Error).message}). ` +
        `Set ARGUS_SKILL_BIN or start it yourself: argus-skill --web --web-port ${port}`,
    };
  }

  const startupDeadline = startupPollDeadline();
  for (
    let i = 0;
    i < startupPollAttempts() && Date.now() < startupDeadline;
    i++
  ) {
    const exitCode = await waitForStartupPoll(spawned, doSleep);
    if (exitCode !== undefined) {
      const competing = await doProbe();
      if (competing.state === 'compatible') {
        return compatibleResult(competing, {
          spawned: false,
          prefix: 'api up',
          onWarning,
        });
      }
      return {
        reachable: false,
        spawned: true,
        message: `backend exited before becoming ready (exit ${exitCode ?? 'unknown'})`,
      };
    }
    const probe = await doProbe();
    if (probe.state === 'compatible') {
      let spawnedApi: SpawnedApiReceipt | undefined;
      if (ownerFile) {
        const ownership = spawnedOwnershipRecord(probe, spawned.pid, host, port, bin);
        try {
          await doNormalWriteOwnership(ownerFile, ownership);
        } catch (writeErr) {
          sendSigterm([ownership.pid, ownership.rootPid], doNormalSignal);
          return {
            reachable: false,
            spawned: false,
            message:
              `could not write ownership record (${(writeErr as Error).message}); ` +
              'sent SIGTERM to spawned process tree',
          };
        }
        spawnedApi = { ownerFile, ownership };
      }
      return compatibleResult(probe, {
        spawned: true,
        prefix: 'api started',
        onWarning,
        spawnedApi,
      });
    }
    if (probe.state === 'incompatible') {
      sendSigterm([spawned.pid], doNormalSignal);
      return {
        reachable: false,
        spawned: true,
        message: `port ${port} is occupied by an incompatible Argus API: ${probe.message}`,
      };
    }
    onStatus?.(`starting backend api… ${i + 1}`);
  }
  sendSigterm([spawned.pid], doNormalSignal);
  return {
    reachable: false,
    spawned: true,
    message: `started '${bin} --web' but it did not come online at ${host}:${port}`,
  };
}
