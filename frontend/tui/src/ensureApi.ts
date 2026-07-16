import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  describeApiRuntime,
  inspectApiMeta,
  type ApiMeta,
} from '../../core/src/protocol.js';
import {
  readOwnedApi as readOwnedApiImpl,
  writeOwnershipRecord as writeOwnershipRecordImpl,
  type ApiOwnershipRecord,
} from './apiOwnership.js';

/**
 * Make `argus` a true one-command launch: if the backend API isn't up, start
 * `argus-skill --web` ourselves, wait for it, then connect. This is why the
 * launch command can just be `argus`.
 *
 * Binary resolution (this box has SEVERAL argus-skill installs on PATH, most of
 * them older checkouts WITHOUT the `--web` flag): prefer ARGUS_SKILL_BIN, then
 * the repo's own `.venv/bin/argus-skill` (the one this frontend ships beside —
 * it has the [web] extra + `--web`), and only fall back to bare `argus-skill`
 * on PATH.
 */

function resolveBin(): string {
  if (process.env.ARGUS_SKILL_BIN) return process.env.ARGUS_SKILL_BIN;
  // this file lives at <repo>/frontend/tui/{src|dist}/ensureApi — the repo venv
  // is three levels up.
  const here = dirname(fileURLToPath(import.meta.url));
  const repoBin = resolve(here, '..', '..', '..', '.venv', 'bin', 'argus-skill');
  if (existsSync(repoBin)) return repoBin;
  return 'argus-skill';
}

export interface ApiProbeResult {
  state: 'compatible' | 'incompatible' | 'unreachable';
  message: string;
  warning?: string;
  meta?: ApiMeta;
}

export async function probeApi(
  host: string,
  port: number,
  token?: string,
): Promise<ApiProbeResult> {
  try {
    const ctrl = AbortSignal.timeout(1200);
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    const r = await fetch(`http://${host}:${port}/api/meta`, { signal: ctrl, headers });
    if (!r.ok) {
      const suffix = r.status === 404
        ? 'service does not expose /api/meta; it is an older Argus checkout or another process'
        : `GET /api/meta returned HTTP ${r.status}`;
      return { state: 'incompatible', message: suffix };
    }
    let body: unknown;
    try {
      body = await r.json();
    } catch {
      return { state: 'incompatible', message: 'backend returned malformed /api/meta JSON' };
    }
    const compatibility = inspectApiMeta(body);
    if (!compatibility.compatible || !compatibility.meta) {
      return { state: 'incompatible', message: compatibility.reason };
    }
    return {
      state: 'compatible',
      message: describeApiRuntime(compatibility.meta),
      warning: compatibility.warning,
      meta: compatibility.meta,
    };
  } catch (error) {
    return {
      state: 'unreachable',
      message: error instanceof Error ? error.message : String(error),
    };
  }
}

export interface EnsureResult {
  reachable: boolean;
  spawned: boolean;
  message: string;
  warning?: string;
}

function compatibleResult(
  probe: ApiProbeResult,
  options: {
    spawned: boolean;
    prefix: string;
    onWarning?: (warning: string) => void;
  },
): EnsureResult {
  const { spawned, prefix, onWarning } = options;
  if (probe.warning) onWarning?.(probe.warning);
  return {
    reachable: true,
    spawned,
    message: `${prefix} · ${probe.message}`,
    warning: probe.warning,
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
    signal?: (pid: number, signal: NodeJS.Signals) => void;
    spawnApi?: () => Promise<{ pid: number }>;
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

  const initial = await doProbe();
  if (initial.state === 'compatible') {
    return compatibleResult(initial, {
      spawned: false,
      prefix: 'api up',
      onWarning,
    });
  }
  if (initial.state === 'incompatible') {
    if (!ownerFile) {
      return {
        reachable: false,
        spawned: false,
        message: `incompatible Argus API at ${host}:${port}: ${initial.message}. Stop that WebAPI or choose another port.`,
      };
    }

    // Guard: only recover for local hosts — never inspect, signal, or spawn for a remote endpoint.
    const isLocal = host === '127.0.0.1' || host === 'localhost' || host === '::1';
    if (!isLocal) {
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

    const record = await doReadOwned();
    if (!record) {
      return {
        reachable: false,
        spawned: false,
        message: `incompatible Argus API at ${host}:${port}: ${initial.message} — ownership could not be proven`,
      };
    }

    // SIGTERM only — never escalate to SIGKILL.
    doSignal(record.pid, 'SIGTERM');
    onStatus?.('waiting for stale backend to shut down…');

    // Probe every 250 ms for at most 8 seconds.
    let shutdown = false;
    for (let i = 0; i < 32; i++) {
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
    const doSpawn = deps?.spawnApi ?? (async () => {
      const child = spawn(bin, ['--web', '--web-host', host, '--web-port', String(port)], {
        detached: true,
        stdio: 'ignore',
      });
      child.unref();
      return { pid: child.pid! };
    });

    onStatus?.('starting backend api…');
    const spawned = await doSpawn();

    // Write new ownership record atomically. If this fails, stop the just-spawned child
    // with SIGTERM so it does not run as a silent unowned process.
    try {
      await doWriteOwnership(ownerFile, {
        schema: 1,
        pid: spawned.pid,
        host,
        port,
        backendBin: bin,
        startedAt: new Date().toISOString(),
      });
    } catch (writeErr) {
      try { doSignal(spawned.pid, 'SIGTERM'); } catch { /* ignore */ }
      return {
        reachable: false,
        spawned: false,
        message:
          `incompatible Argus API at ${host}:${port}: ownership write failed after spawn` +
          ` (${(writeErr as Error).message}); sent SIGTERM to pid ${spawned.pid}`,
      };
    }

    // Poll for the new backend to come online.
    for (let i = 0; i < 20; i++) {
      await doSleep(500);
      const probe = await doProbe();
      if (probe.state === 'compatible') {
        return compatibleResult(probe, {
          spawned: true,
          prefix: 'api started',
          onWarning,
        });
      }
      if (probe.state === 'incompatible') {
        return {
          reachable: false,
          spawned: true,
          message: `port ${port} is occupied by an incompatible Argus API: ${probe.message}`,
        };
      }
      onStatus?.(`starting backend api… ${i + 1}`);
    }
    return {
      reachable: false,
      spawned: true,
      message: `started backend but it did not come online at ${host}:${port}`,
    };
  }

  // Unreachable — try to auto-start a local API.
  const local = host === '127.0.0.1' || host === 'localhost' || host === '::1';
  if (!autostart || !local) {
    return {
      reachable: false,
      spawned: false,
      message: `no API at ${host}:${port} — start it with:  argus-skill --web --web-port ${port}`,
    };
  }

  onStatus?.('starting backend api…');
  const bin = resolveBin();

  const doNormalSpawn = deps?.spawnApi ?? (async () => {
    const child = spawn(bin, ['--web', '--web-host', host, '--web-port', String(port)], {
      detached: true,
      stdio: 'ignore',
    });
    child.unref();
    return { pid: child.pid! };
  });

  const doNormalSignal = deps?.signal ?? ((pid: number, sig: NodeJS.Signals) => {
    process.kill(pid, sig);
  });
  const doNormalWriteOwnership = deps?.writeOwnershipRecord ??
    ((p: string, r: ApiOwnershipRecord) => writeOwnershipRecordImpl(p, r));

  let spawnedPid: number;
  try {
    const spawned = await doNormalSpawn();
    spawnedPid = spawned.pid;
  } catch (err) {
    return {
      reachable: false,
      spawned: false,
      message:
        `could not launch '${bin} --web' (${(err as Error).message}). ` +
        `Set ARGUS_SKILL_BIN or start it yourself: argus-skill --web --web-port ${port}`,
    };
  }

  // Atomically write ownership immediately after spawn so future stale
  // recovery can prove we own this process.
  if (ownerFile) {
    try {
      await doNormalWriteOwnership(ownerFile, {
        schema: 1,
        pid: spawnedPid,
        host,
        port,
        backendBin: bin,
        startedAt: new Date().toISOString(),
      });
    } catch (writeErr) {
      try { doNormalSignal(spawnedPid, 'SIGTERM'); } catch { /* ignore */ }
      return {
        reachable: false,
        spawned: false,
        message:
          `could not write ownership record (${(writeErr as Error).message}); ` +
          `sent SIGTERM to pid ${spawnedPid}`,
      };
    }
  }

  for (let i = 0; i < 20; i++) {
    await doSleep(500);
    const probe = await doProbe();
    if (probe.state === 'compatible') {
      return compatibleResult(probe, {
        spawned: true,
        prefix: 'api started',
        onWarning,
      });
    }
    if (probe.state === 'incompatible') {
      return {
        reachable: false,
        spawned: true,
        message: `port ${port} is occupied by an incompatible Argus API: ${probe.message}`,
      };
    }
    onStatus?.(`starting backend api… ${i + 1}`);
  }
  return {
    reachable: false,
    spawned: true,
    message: `started '${bin} --web' but it did not come online at ${host}:${port}`,
  };
}
