import { execFileSync, spawn } from 'node:child_process';

export interface PairingPlan {
  /** Bearer token the backend must be started with, '' when none is needed. */
  token: string;
  /** Address a phone can actually reach, token included. */
  url: string;
  /** Multi-line banner with the URL and a scannable QR code. */
  banner: string;
  /** Whether this bind is reachable off-machine and worth printing in full. */
  pairing: boolean;
}

/** Ask the Python backend how to pair this bind.
 *
 * Token minting, LAN-address resolution, and QR rendering all live in
 * `argus.webapi.pairing`. The cockpit spawns the backend detached with
 * stdio discarded, so it cannot read the banner the backend prints for itself;
 * it asks here instead, then passes the token down when spawning. Keeping one
 * implementation means the two entry points can't disagree about the URL.
 *
 * Returns null when the backend is too old to understand `--pair-plan`, or
 * anything else goes wrong — callers fall back to {@link webUiUrl}. */
export function resolvePairing(
  bin: string,
  host: string,
  port: number,
): PairingPlan | null {
  try {
    const stdout = execFileSync(
      bin,
      ['--pair-plan', '--web-host', host, '--web-port', String(port)],
      { encoding: 'utf8', timeout: 15_000, stdio: ['ignore', 'pipe', 'ignore'] },
    );
    const parsed = JSON.parse(stdout) as Partial<PairingPlan>;
    if (typeof parsed?.url !== 'string' || !parsed.url) return null;
    return {
      token: typeof parsed.token === 'string' ? parsed.token : '',
      url: parsed.url,
      banner: typeof parsed.banner === 'string' ? parsed.banner : '',
      pairing: parsed.pairing === true,
    };
  } catch {
    return null;
  }
}

/** Add the project selector to an already-built pairing URL. */
export function withProject(url: string, project?: string): string {
  if (!project?.trim()) return url;
  const parsed = new URL(url);
  parsed.searchParams.set('project', project.trim());
  return parsed.toString();
}

export function webUiUrl(host: string, port: number, project?: string, token?: string): string {
  const browserHost = host === '0.0.0.0' || host === '::' || host === '::0' ? '127.0.0.1' : host;
  const url = new URL(`http://${browserHost}:${port}/`);
  if (project?.trim()) url.searchParams.set('project', project.trim());
  if (token?.trim()) url.searchParams.set('token', token.trim());
  return url.toString();
}

export function browserCommand(
  url: string,
  platform: NodeJS.Platform = process.platform,
  env: NodeJS.ProcessEnv = process.env,
): { command: string; args: string[] } | null {
  if (platform === 'darwin') return { command: 'open', args: [url] };
  if (platform === 'win32') return { command: 'cmd', args: ['/c', 'start', '', url] };
  // VS Code Remote can ask the local desktop to open a URL even though the
  // server itself has no DISPLAY.
  if (platform === 'linux' && env.VSCODE_IPC_HOOK_CLI) {
    return { command: 'code', args: ['--open-url', url] };
  }
  if (platform === 'linux' && (env.DISPLAY || env.WAYLAND_DISPLAY)) {
    return { command: 'xdg-open', args: [url] };
  }
  return null;
}

/** Best-effort desktop launch. Headless/SSH hosts deliberately return false;
 * the caller still prints the URL for port forwarding. */
export function openWebBrowser(url: string): boolean {
  const spec = browserCommand(url);
  if (!spec) return false;
  try {
    const child = spawn(spec.command, spec.args, { detached: true, stdio: 'ignore' });
    child.unref();
    return true;
  } catch {
    return false;
  }
}
