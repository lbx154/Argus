import { randomUUID } from 'node:crypto';
import { realpathSync } from 'node:fs';
import { isAbsolute, resolve } from 'node:path';
import {
  READ_API, isJsonObject, requireCompatibleApiMeta, requireProjectList,
  requireProjectCosts, requireReadSnapshot,
  type ReadMethod, type ReadQueryParams, type ReadQueryResults,
} from '@argus/contracts';
import { executeProcess } from '@argus/runtime';
import { CostProjectionStream } from './costProjection.js';

export class QueryError extends Error {
  constructor(readonly code: 'invalid_query' | 'query_failed' | 'unavailable' | 'timeout' | 'aborted' | 'busy' | 'invalid_response' | 'response_too_large', message: string) {
    super(message);
    this.name = 'QueryError';
  }
}

export interface QueryBackend {
  query<M extends ReadMethod>(method: M, params: ReadQueryParams[M], signal?: AbortSignal): Promise<ReadQueryResults[M]>;
  close(): Promise<void>;
}

export interface PythonBackendOptions {
  executable: string;
  sourceRoot: string;
  globalRoot: string;
  timeoutMs?: number;
  maxConcurrent?: number;
  /** For a Python launcher or a deterministic process fixture, never shell text. */
  prefixArgs?: string[];
  env?: NodeJS.ProcessEnv;
}

/** Each query has an isolated lifetime; no Python worker survives its receipt. */
export class PythonQueryBackend implements QueryBackend {
  private readonly controllers = new Set<AbortController>();
  private readonly pending = new Set<Promise<unknown>>();
  private closed = false;
  private readonly maxConcurrent: number;
  private readonly timeoutMs: number;

  constructor(private readonly options: PythonBackendOptions) {
    if (!isAbsolute(options.sourceRoot) || !isAbsolute(options.globalRoot)) throw new Error('sourceRoot and globalRoot must be absolute');
    this.options = { ...options, sourceRoot: realpathSync(options.sourceRoot) };
    this.maxConcurrent = options.maxConcurrent ?? 4;
    this.timeoutMs = options.timeoutMs ?? 30_000;
    if (!Number.isSafeInteger(this.maxConcurrent) || this.maxConcurrent < 1 || this.maxConcurrent > 64) throw new Error('maxConcurrent must be between 1 and 64');
    if (!Number.isSafeInteger(this.timeoutMs) || this.timeoutMs < 1 || this.timeoutMs > 2_147_483_647) throw new Error('invalid query timeout');
  }

  query<M extends ReadMethod>(method: M, params: ReadQueryParams[M], signal?: AbortSignal): Promise<ReadQueryResults[M]> {
    if (this.closed) return Promise.reject(new QueryError('unavailable', 'query backend is closed'));
    if (signal?.aborted) return Promise.reject(new QueryError('aborted', 'query cancelled'));
    if (this.controllers.size >= this.maxConcurrent) return Promise.reject(new QueryError('busy', 'query capacity is exhausted'));
    const controller = new AbortController();
    this.controllers.add(controller);
    const combined = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;
    const operation = this.execute(method, params, combined);
    this.pending.add(operation);
    const cleanup = (): void => { this.controllers.delete(controller); this.pending.delete(operation); };
    // Both handlers are attached before returning, including when close() aborts it.
    void operation.then(cleanup, cleanup);
    return operation;
  }

  async close(): Promise<void> {
    this.closed = true;
    for (const controller of this.controllers) controller.abort();
    await Promise.allSettled([...this.pending]);
  }

  private async execute<M extends ReadMethod>(method: M, params: ReadQueryParams[M], signal: AbortSignal): Promise<ReadQueryResults[M]> {
    if (!READ_API.methods.includes(method)) throw new QueryError('invalid_query', 'unsupported read operation');
    const id = randomUUID();
    const input = JSON.stringify({ protocol: READ_API.bridge_protocol, version: READ_API.bridge_version, id, method, params });
    if (Buffer.byteLength(input) > READ_API.max_request_bytes) throw new QueryError('invalid_query', 'read query exceeds the request limit');
    const env = { ...process.env, ...this.options.env, ARGUS_SKILL_HOME: this.options.globalRoot, ARGUS_SKILL_SOURCE_ROOT: this.options.sourceRoot, PYTHONPATH: this.options.sourceRoot, PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1' };
    delete (env as NodeJS.ProcessEnv).ARGUS_DESKTOP_LAUNCH_NONCE;
    delete (env as NodeJS.ProcessEnv).ARGUS_READ_API_TOKEN;
    let reply: unknown;
    let received = false;
    let successfulExit = false;
    let responseBytes = 0;
    const costs = method === 'costs' ? new CostProjectionStream((params as ReadQueryParams['costs']).limit) : null;
    for await (const event of executeProcess({
      executable: this.options.executable,
      args: [...(this.options.prefixArgs ?? []), '-m', 'argus.webapi.read_bridge', '--global-root', this.options.globalRoot],
      cwd: this.options.sourceRoot, env, input, signal,
      wallTimeoutMs: this.timeoutMs, idleTimeoutMs: this.timeoutMs,
      maxLineBytes: costs ? READ_API.max_cost_frame_bytes : READ_API.max_response_bytes,
      maxBufferedBytes: READ_API.max_response_bytes + 1,
    })) {
      if (event.type === 'line' && event.stream === 'stdout') {
        if (received || !event.line) throw new QueryError('invalid_response', 'query backend returned unexpected output');
        responseBytes += Buffer.byteLength(event.line) + 1;
        if (responseBytes > READ_API.max_response_bytes) throw new QueryError('response_too_large', 'query response exceeds the response limit');
        try { reply = JSON.parse(event.line); } catch { throw new QueryError('invalid_response', 'query backend returned malformed JSON'); }
        if (!isJsonObject(reply) || reply.protocol !== READ_API.bridge_protocol
          || reply.version !== READ_API.bridge_version || reply.id !== id) {
          throw new QueryError('invalid_response', 'query backend returned an incompatible receipt');
        }
        if (Object.hasOwn(reply, 'kind')) {
          if (!costs) throw new QueryError('invalid_response', 'unexpected cost input stream');
          try { costs.consume(reply); } catch {
            throw new QueryError('invalid_response', 'query backend returned invalid cost inputs');
          }
          continue;
        }
        received = true;
      } else if (event.type === 'exit') {
        if (event.stopKind === 'cancelled') throw new QueryError('aborted', 'query cancelled');
        if (event.stopKind === 'wall_timeout' || event.stopKind === 'idle_timeout') throw new QueryError('timeout', 'query deadline exceeded');
        if (event.stopKind === 'output_limit') throw new QueryError('response_too_large', 'query response exceeds the response limit');
        if (event.stopKind || event.code !== 0) throw new QueryError('unavailable', 'query backend did not exit successfully');
        successfulExit = true;
      }
    }
    if (!successfulExit || !isJsonObject(reply) || reply.protocol !== READ_API.bridge_protocol
      || reply.version !== READ_API.bridge_version || reply.id !== id || typeof reply.ok !== 'boolean') {
      throw new QueryError('invalid_response', 'query backend returned an incompatible receipt');
    }
    if (!reply.ok) {
      const error = isJsonObject(reply.error) ? reply.error : {};
      if (error.code === 'invalid_query') throw new QueryError('invalid_query', 'query backend rejected the read parameters');
      if (error.code === 'response_too_large') throw new QueryError('response_too_large', 'query response exceeds the response limit');
      throw new QueryError('query_failed', 'unable to read project state');
    }
    try {
      let result: ReadQueryResults[ReadMethod];
      switch (method) {
        case 'meta': {
          const meta = requireCompatibleApiMeta(reply.result);
          if (resolve(meta.runtime.source_root) !== resolve(this.options.sourceRoot)) throw new Error('unexpected Python source root');
          result = meta;
          break;
        }
        case 'projects': result = requireProjectList(reply.result); break;
        case 'costs': result = requireProjectCosts(costs!.finish(reply.result)); break;
        case 'snapshot': result = reply.result === null ? null : requireReadSnapshot(reply.result, (params as ReadQueryParams['snapshot']).sid); break;
      }
      return result as ReadQueryResults[M];
    } catch {
      throw new QueryError('invalid_response', 'query response does not satisfy the shared contract');
    }
  }
}
