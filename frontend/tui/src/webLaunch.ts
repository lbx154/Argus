import { spawn } from 'node:child_process';

export function webUiUrl(host: string, port: number, project?: string): string {
  const browserHost = host === '0.0.0.0' || host === '::' || host === '::0' ? '127.0.0.1' : host;
  const url = new URL(`http://${browserHost}:${port}/`);
  if (project?.trim()) url.searchParams.set('project', project.trim());
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
