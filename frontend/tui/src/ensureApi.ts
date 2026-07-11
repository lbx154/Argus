import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  describeApiRuntime,
  inspectApiMeta,
  type ApiMeta,
} from '../../core/src/protocol.js';

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
}

export async function ensureApi(opts: {
  host: string;
  port: number;
  token?: string;
  autostart?: boolean;
  onStatus?: (s: string) => void;
}): Promise<EnsureResult> {
  const { host, port, token, autostart = true, onStatus } = opts;

  const initial = await probeApi(host, port, token);
  if (initial.state === 'compatible') {
    return { reachable: true, spawned: false, message: `api up · ${initial.message}` };
  }
  if (initial.state === 'incompatible') {
    return {
      reachable: false,
      spawned: false,
      message: `incompatible Argus API at ${host}:${port}: ${initial.message}. Stop that WebAPI or choose another port.`,
    };
  }
  // Only auto-start a LOCAL API — never try to launch a daemon on a remote host.
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
  try {
    const child = spawn(bin, ['--web', '--web-host', host, '--web-port', String(port)], {
      detached: true,
      stdio: 'ignore',
    });
    child.unref();
  } catch (err) {
    return {
      reachable: false,
      spawned: false,
      message:
        `could not launch '${bin} --web' (${(err as Error).message}). ` +
        `Set ARGUS_SKILL_BIN or start it yourself: argus-skill --web --web-port ${port}`,
    };
  }

  // poll up to ~10s for it to come online
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 500));
    const probe = await probeApi(host, port, token);
    if (probe.state === 'compatible') {
      return { reachable: true, spawned: true, message: `api started · ${probe.message}` };
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
