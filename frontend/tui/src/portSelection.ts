import { createServer } from 'node:net';

import { isLocalApiHost, readOwnedApi } from './apiOwnership.js';
import { probeApi, type ApiProbeResult } from './ensureApi.js';

export interface SelectApiPortOptions {
  host: string;
  preferredPort: number;
  token?: string;
  explicit: boolean;
  maxAttempts?: number;
  ownerFile?: string;
  backendBin?: string;
}

export interface SelectApiPortDeps {
  probe?: (host: string, port: number, token?: string) => Promise<ApiProbeResult>;
  available?: (host: string, port: number) => Promise<boolean>;
  replaceable?: (probe: ApiProbeResult) => Promise<boolean>;
}

export async function isPortAvailable(host: string, port: number): Promise<boolean> {
  return new Promise<boolean>((resolve, reject) => {
    const server = createServer();
    server.unref();
    server.once('error', (error: NodeJS.ErrnoException) => {
      if (error.code === 'EADDRINUSE' || error.code === 'EACCES') resolve(false);
      else reject(error);
    });
    server.listen({ host, port, exclusive: true }, () => {
      server.close((error) => {
        if (error) reject(error);
        else resolve(true);
      });
    });
  });
}

/**
 * Reuse the preferred port when it already hosts a compatible Argus API.
 * Otherwise, avoid stale or unrelated listeners by selecting the first port
 * this process can bind.
 */
export async function selectApiPort(
  options: SelectApiPortOptions,
  deps: SelectApiPortDeps = {},
): Promise<number> {
  if (options.explicit) return options.preferredPort;

  const probe = deps.probe ?? probeApi;
  const available = deps.available ?? isPortAvailable;
  const preferredProbe = await probe(options.host, options.preferredPort, options.token);
  if (preferredProbe.state === 'compatible') return options.preferredPort;
  if (
    preferredProbe.state === 'incompatible'
    && preferredProbe.meta
    && isLocalApiHost(options.host)
  ) {
    const replaceable = deps.replaceable ?? (async (probe: ApiProbeResult) => {
      if (!options.ownerFile || !options.backendBin || !probe.meta) return false;
      const record = await readOwnedApi({
        path: options.ownerFile,
        host: options.host,
        port: options.preferredPort,
        backendBin: options.backendBin,
      });
      return record?.pid === probe.meta.runtime.pid;
    });
    if (await replaceable(preferredProbe)) return options.preferredPort;
  }
  const host = options.host.trim().toLowerCase();
  const localBind = isLocalApiHost(host) || host === '0.0.0.0' || host === '::' || host === '::0';
  if (!localBind) return options.preferredPort;

  const attempts = options.maxAttempts ?? 20;
  for (let offset = 0; offset < attempts; offset += 1) {
    const port = options.preferredPort + offset;
    if (port > 65_535) break;
    if (await available(options.host, port)) return port;
    if (port !== options.preferredPort) {
      const candidateProbe = await probe(options.host, port, options.token);
      if (candidateProbe.state === 'compatible') return port;
    }
  }
  throw new Error(
    `no available API port found from ${options.preferredPort} `
    + `after ${attempts} attempts; pass --port to choose one explicitly`,
  );
}
