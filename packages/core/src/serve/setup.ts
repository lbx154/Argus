// Facts requested by Settings: executable discovery, CLI versions, the same working-bubblewrap
// probe the agent launcher uses, and addresses on the interfaces this server listens on.
import { execFile } from 'node:child_process';
import { networkInterfaces } from 'node:os';
import { promisify } from 'node:util';
import { agentEnv, launcher, notInstalled, sandboxed } from '../agent/sandbox.js';
import { BACKENDS } from '../types.js';

const run = promisify(execFile);

export async function setupStatus() {
  const backends = await Promise.all(BACKENDS.map(async backend => {
    const missing = notInstalled(backend);
    if (missing) return { backend, installed: false, version: null, detail: missing };
    const [file, ...args] = launcher(backend);
    try {
      const { stdout } = await run(file!, [...args, '--version'], { env: agentEnv(backend), maxBuffer: 64 * 1024, timeout: 10_000, windowsHide: true });
      return { backend, installed: true, version: stdout.trim() || null, detail: stdout.trim() ? '' : 'The version command returned no output.' };
    } catch (error) {
      return { backend, installed: true, version: null, detail: (error as Error).message };
    }
  }));
  return { backends, sandbox: { bubblewrap: sandboxed(), platform: process.platform } };
}

export function mobileOrigins(host: string, port: number): string[] {
  if (/^(localhost|127(\.\d+){3}|::1)$/.test(host)) return [];
  const addresses = host === '0.0.0.0' || host === '::'
    ? Object.values(networkInterfaces()).flatMap(entries => entries ?? [])
      .filter(entry => !entry.internal && (host === '::' || entry.family === 'IPv4') && !entry.address.startsWith('fe80:'))
      .map(entry => entry.address)
    : [host];
  return [...new Set(addresses)].map(address => `http://${address.includes(':') ? `[${address}]` : address}:${port}`);
}
