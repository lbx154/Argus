import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

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

async function ping(host: string, port: number, token?: string): Promise<boolean> {
  try {
    const ctrl = AbortSignal.timeout(1200);
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    const r = await fetch(`http://${host}:${port}/api/projects`, { signal: ctrl, headers });
    return r.ok || r.status === 401; // 401 = up but needs token — still "reachable"
  } catch {
    return false;
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

  if (await ping(host, port, token)) {
    return { reachable: true, spawned: false, message: 'api up' };
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
    if (await ping(host, port, token)) {
      return { reachable: true, spawned: true, message: 'api started' };
    }
    onStatus?.(`starting backend api… ${i + 1}`);
  }
  return {
    reachable: false,
    spawned: true,
    message: `started '${bin} --web' but it did not come online at ${host}:${port}`,
  };
}
