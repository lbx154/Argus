import {
  READ_API, SNAPSHOT_SCHEMA_VERSION, isJsonObject, requireCompatibleApiMeta,
  requireProjectList, requireProjectCosts, requireReadSnapshot,
  type ReadApiMeta, type ProjectList, type ProjectCosts, type Snapshot,
} from '@argus/contracts';

export class ReadApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) { super(message); this.name = 'ReadApiError'; }
}

export class ReadApiClient {
  private readonly base: URL;
  constructor(url: string, private readonly token: string) {
    this.base = new URL(url);
    if (!['http:', 'https:'].includes(this.base.protocol) || this.base.username || this.base.password || this.base.search || this.base.hash || this.base.pathname !== '/') {
      throw new Error('read API URL must be an HTTP origin without credentials');
    }
  }

  async meta(signal?: AbortSignal): Promise<ReadApiMeta> {
    const value = await this.get('/api/meta', signal);
    if (!isJsonObject(value) || value.service !== READ_API.service || value.read_only !== true
      || !isJsonObject(value.protocol) || value.protocol.name !== READ_API.protocol.name
      || value.protocol.major !== READ_API.protocol.major || typeof value.protocol.minor !== 'number' || !Number.isInteger(value.protocol.minor)
      || value.protocol.minor < READ_API.protocol.minor || value.snapshot_schema_version !== SNAPSHOT_SCHEMA_VERSION
      || !Array.isArray(value.capabilities) || !READ_API.methods.every(method => value.capabilities instanceof Array && value.capabilities.includes(method))
      || !isJsonObject(value.runtime) || value.runtime.implementation !== 'node'
      || typeof value.runtime.version !== 'string' || !Number.isInteger(value.runtime.pid)) {
      throw new ReadApiError(502, 'invalid_response', 'incompatible read API metadata');
    }
    requireCompatibleApiMeta(value.backend);
    return value as unknown as ReadApiMeta;
  }
  async projects(options: { limit?: number; includeEmpty?: boolean; signal?: AbortSignal } = {}): Promise<ProjectList> {
    const query = new URLSearchParams({ limit: String(options.limit ?? 100), include_empty: String(options.includeEmpty ?? false) });
    return requireProjectList(await this.get(`/api/projects?${query}`, options.signal));
  }
  async costs(options: { limit?: number; signal?: AbortSignal } = {}): Promise<ProjectCosts> {
    return requireProjectCosts(await this.get(`/api/projects/costs?limit=${options.limit ?? 100}`, options.signal));
  }
  async snapshot(sid: string, options: { eventsLimit?: number; compact?: boolean; signal?: AbortSignal } = {}): Promise<Snapshot> {
    const query = new URLSearchParams({ events_limit: String(options.eventsLimit ?? 80), compact: String(options.compact ?? false) });
    return requireReadSnapshot(await this.get(`/api/projects/${encodeURIComponent(sid)}/snapshot?${query}`, options.signal), sid);
  }

  private async get(path: string, signal?: AbortSignal): Promise<unknown> {
    const response = await fetch(new URL(path, this.base), {
      method: 'GET', headers: { authorization: `Bearer ${this.token}` }, redirect: 'error',
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(35_000)]) : AbortSignal.timeout(35_000),
    });
    const reader = response.body?.getReader();
    const chunks: Uint8Array[] = [];
    let length = 0;
    if (reader) {
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          length += value.byteLength;
          if (length > READ_API.max_response_bytes) throw new ReadApiError(502, 'response_too_large', 'read API response exceeds its limit');
          chunks.push(value);
        }
      } finally { await reader.cancel(); reader.releaseLock(); }
    }
    let payload: unknown;
    try { payload = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
    catch { throw new ReadApiError(502, 'invalid_response', 'read API returned malformed JSON'); }
    if (!response.ok) {
      const error = isJsonObject(payload) ? payload : {};
      throw new ReadApiError(response.status, typeof error.code === 'string' ? error.code : 'request_failed', typeof error.detail === 'string' ? error.detail : 'read query failed');
    }
    return payload;
  }
}
