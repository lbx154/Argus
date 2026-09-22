import { createHash, timingSafeEqual } from 'node:crypto';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import {
  READ_API, SNAPSHOT_SCHEMA_VERSION, validProjectId,
  type ReadApiMeta, type ReadMethod,
} from '@argus/contracts';
import { QueryError, type QueryBackend } from './backend.js';

export interface ReadApiOptions {
  backend: QueryBackend;
  token: string;
  host?: '127.0.0.1' | '::1';
  port?: number;
  signal?: AbortSignal;
}
export interface ReadApiServer { url: string; close(): Promise<void> }

class HttpError extends Error {
  constructor(readonly status: number, readonly code: string, detail: string) { super(detail); }
}
function integer(params: URLSearchParams, name: string, fallback: number, min: number, max: number): number {
  const raw = params.get(name);
  if (raw === null) return fallback;
  if (!/^(0|[1-9]\d*)$/.test(raw)) throw new HttpError(422, 'invalid_query', `${name} must be an integer`);
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < min || value > max) throw new HttpError(422, 'invalid_query', `${name} is outside the supported range`);
  return value;
}
function boolean(params: URLSearchParams, name: string, fallback: boolean): boolean {
  const raw = params.get(name);
  if (raw === null) return fallback;
  if (raw === 'true' || raw === '1') return true;
  if (raw === 'false' || raw === '0') return false;
  throw new HttpError(422, 'invalid_query', `${name} must be boolean`);
}
function parameters(params: URLSearchParams, allowed: string[]): void {
  for (const key of params.keys()) {
    if (!allowed.includes(key) || params.getAll(key).length !== 1) throw new HttpError(422, 'invalid_query', 'unsupported or repeated query parameter');
  }
}
function respond(response: ServerResponse, status: number, value: unknown): void {
  if (response.destroyed) return;
  const body = JSON.stringify(value);
  if (Buffer.byteLength(body) > READ_API.max_response_bytes) throw new HttpError(502, 'response_too_large', 'read response exceeds the response limit');
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
  });
  response.end(body);
}

/** An authenticated, loopback-only GET surface; never forwards command requests. */
export async function startReadApi(options: ReadApiOptions): Promise<ReadApiServer> {
  const host = options.host ?? '127.0.0.1';
  const port = options.port ?? 0;
  if (!['127.0.0.1', '::1'].includes(host)) throw new Error('read API must bind to a loopback address');
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('invalid port');
  if (typeof options.token !== 'string' || options.token.length < 16) throw new Error('read API requires a bearer token of at least 16 characters');
  const tokenHash = createHash('sha256').update(`Bearer ${options.token}`).digest();
  let closing: Promise<void> | undefined;
  let url = '';
  const authorities = new Set<string>();
  const controllers = new Set<AbortController>();
  const server = createServer((request, response) => { void handle(request, response); });
  server.requestTimeout = 30_000;
  server.headersTimeout = 10_000;
  server.keepAliveTimeout = 1_000;

  async function handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
    const controller = new AbortController();
    controllers.add(controller);
    const disconnect = (): void => { if (!response.writableEnded) controller.abort(); };
    response.once('close', disconnect);
    try {
      if (closing) throw new HttpError(503, 'unavailable', 'read API is stopping');
      const authority = (request.headers.host ?? '').toLowerCase();
      if (!authorities.has(authority)) throw new HttpError(421, 'invalid_host', 'unexpected request host');
      if (request.headers.origin && request.headers.origin !== new URL(`http://${authority}`).origin) {
        throw new HttpError(403, 'invalid_origin', 'cross-origin requests are not supported');
      }
      const supplied = typeof request.headers.authorization === 'string' ? request.headers.authorization : '';
      if (!timingSafeEqual(createHash('sha256').update(supplied).digest(), tokenHash)) {
        response.setHeader('www-authenticate', 'Bearer');
        throw new HttpError(401, 'unauthorized', 'invalid or missing bearer token');
      }
      if (request.method !== 'GET') {
        response.shouldKeepAlive = false;
        response.setHeader('allow', 'GET');
        throw new HttpError(405, 'read_only', 'this API supports read queries only');
      }
      if (request.headers['transfer-encoding'] || (request.headers['content-length'] && request.headers['content-length'] !== '0')) {
        response.shouldKeepAlive = false;
        throw new HttpError(400, 'invalid_query', 'read queries cannot contain a request body');
      }
      if (!request.url?.startsWith('/') || request.url.startsWith('//') || request.url.length > 8192) {
        throw new HttpError(400, 'invalid_query', 'invalid request target');
      }
      const target = new URL(request.url, url);
      const params = target.searchParams;
      if (target.pathname === '/api/meta') {
        parameters(params, []);
        const backend = await options.backend.query('meta', {}, controller.signal);
        const meta: ReadApiMeta = {
          service: READ_API.service, protocol: { ...READ_API.protocol }, read_only: true,
          capabilities: [...READ_API.methods] as ReadMethod[], snapshot_schema_version: SNAPSHOT_SCHEMA_VERSION,
          runtime: { implementation: 'node', version: process.version, pid: process.pid }, backend,
        };
        respond(response, 200, meta);
      } else if (target.pathname === '/api/projects') {
        parameters(params, ['limit', 'include_empty']);
        respond(response, 200, await options.backend.query('projects', {
          limit: integer(params, 'limit', 100, 1, READ_API.max_projects),
          include_empty: boolean(params, 'include_empty', false),
        }, controller.signal));
      } else if (target.pathname === '/api/projects/costs') {
        parameters(params, ['limit']);
        respond(response, 200, await options.backend.query('costs', {
          limit: integer(params, 'limit', 100, 1, READ_API.max_projects),
        }, controller.signal));
      } else {
        const match = /^\/api\/projects\/([^/]+)\/snapshot$/.exec(target.pathname);
        if (!match) throw new HttpError(404, 'not_found', 'unknown read endpoint');
        let sid: string;
        try { sid = decodeURIComponent(match[1]!); } catch { throw new HttpError(422, 'invalid_query', 'invalid project id'); }
        if (!validProjectId(sid)) throw new HttpError(422, 'invalid_query', 'invalid project id');
        parameters(params, ['events_limit', 'compact']);
        const snapshot = await options.backend.query('snapshot', {
          sid, events_limit: integer(params, 'events_limit', 80, 0, READ_API.max_events),
          compact: boolean(params, 'compact', false),
        }, controller.signal);
        if (snapshot === null) throw new HttpError(404, 'not_found', 'unknown project');
        respond(response, 200, snapshot);
      }
    } catch (error) {
      if (controller.signal.aborted || response.destroyed) return;
      if (error instanceof HttpError) respond(response, error.status, { code: error.code, detail: error.message });
      else if (error instanceof QueryError) {
        const status = error.code === 'invalid_query' ? 422 : error.code === 'timeout' ? 504 : error.code === 'busy' || error.code === 'unavailable' ? 503 : 502;
        respond(response, status, { code: error.code, detail: error.message });
      } else respond(response, 500, { code: 'internal_error', detail: 'unable to complete the read query' });
    } finally {
      controllers.delete(controller);
      response.removeListener('close', disconnect);
    }
  }

  const close = (): Promise<void> => {
    closing ??= (async () => {
      options.signal?.removeEventListener('abort', abort);
      for (const controller of controllers) controller.abort();
      const stopped = new Promise<void>(resolve => server.close(() => resolve()));
      server.closeAllConnections();
      await Promise.all([stopped, options.backend.close()]);
    })();
    return closing;
  };
  const abort = (): void => { void close(); };
  try {
    // Fail startup before opening a port if the selected Python installation is incompatible.
    await options.backend.query('meta', {}, options.signal);
    if (options.signal?.aborted) throw new QueryError('aborted', 'startup cancelled');
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject);
      server.listen(port, host, () => { server.removeListener('error', reject); resolve(); });
    });
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('read API has no TCP address');
    const hostname = host === '::1' ? '[::1]' : host;
    authorities.add(`${hostname}:${address.port}`);
    authorities.add(`localhost:${address.port}`);
    url = new URL(`http://${hostname}:${address.port}`).origin;
    authorities.add(new URL(url).host);
    authorities.add(new URL(`http://localhost:${address.port}`).host);
    options.signal?.addEventListener('abort', abort, { once: true });
    if (options.signal?.aborted) { await close(); throw new QueryError('aborted', 'startup cancelled'); }
    return { url, close };
  } catch (error) {
    await close();
    throw error;
  }
}
