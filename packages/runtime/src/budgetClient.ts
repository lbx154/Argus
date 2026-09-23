import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { realpathSync } from 'node:fs';
import { isAbsolute } from 'node:path';
import { StringDecoder } from 'node:string_decoder';
import { BUDGET_BRIDGE, isJsonObject, type JsonObject } from '@argus/contracts';

export interface PythonBudgetOptions {
  executable: string;
  sourceRoot: string;
  globalRoot: string;
  timeoutMs?: number;
  prefixArgs?: string[];
  env?: NodeJS.ProcessEnv;
}

/** One process/one reservation. Killing the broker leaves its locked marker behind. */
export class PythonBudgetSession {
  readonly failure = new AbortController();
  readonly sourceRoot: string;
  readonly globalRoot: string;
  private readonly child: ChildProcessWithoutNullStreams;
  private readonly timeout: number;
  private readonly exited: Promise<void>;
  private closing = false;
  private sequence = 0;
  private error: Error | null = null;
  private pending: { id: number; resolve: (value: JsonObject) => void; reject: (error: Error) => void; timer: NodeJS.Timeout } | null = null;

  constructor(options: PythonBudgetOptions) {
    if (!isAbsolute(options.sourceRoot) || !isAbsolute(options.globalRoot)) throw new Error('budget roots must be absolute');
    this.sourceRoot = realpathSync(options.sourceRoot);
    this.globalRoot = realpathSync(options.globalRoot);
    this.timeout = options.timeoutMs ?? 30_000;
    if (!Number.isSafeInteger(this.timeout) || this.timeout <= 0 || this.timeout > 300_000) throw new Error('invalid budget timeout');
    const env: NodeJS.ProcessEnv = { ...process.env, ...options.env,
      ARGUS_SKILL_HOME: this.globalRoot, ARGUS_SKILL_SOURCE_ROOT: this.sourceRoot,
      PYTHONPATH: this.sourceRoot, PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1',
    };
    delete env.ARGUS_DESKTOP_LAUNCH_NONCE;
    delete env.ARGUS_READ_API_TOKEN;
    this.child = spawn(options.executable, [...(options.prefixArgs ?? []), '-m', 'argus.adapters.budget_bridge', '--global-root', this.globalRoot], {
      cwd: this.sourceRoot, env, stdio: ['pipe', 'pipe', 'pipe'], shell: false, windowsHide: true,
    });
    this.exited = new Promise(resolve => this.child.once('close', () => {
      if (!this.closing || this.pending) this.fail(new Error('budget owner exited before settlement'));
      resolve();
    }));
    this.child.on('error', error => this.fail(error));
    this.child.stdin.on('error', error => this.fail(error));
    this.child.stdout.on('error', error => this.fail(error));
    this.child.stderr.on('error', error => this.fail(error));
    let stderrBytes = 0;
    this.child.stderr.on('data', (chunk: Buffer) => {
      stderrBytes += chunk.length;
      if (stderrBytes > BUDGET_BRIDGE.max_line_bytes) this.fail(new Error('budget diagnostic limit exceeded'));
    });
    const decoder = new StringDecoder('utf8');
    let buffer = '';
    this.child.stdout.on('data', (chunk: Buffer) => {
      if (this.error) return;
      buffer += decoder.write(chunk);
      if (Buffer.byteLength(buffer) > BUDGET_BRIDGE.max_line_bytes) { buffer = ''; this.fail(new Error('budget response limit exceeded')); return; }
      let end: number;
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
        try {
          const response: unknown = JSON.parse(line);
          if (!this.pending || !isJsonObject(response) || response.protocol !== BUDGET_BRIDGE.protocol
            || response.version !== BUDGET_BRIDGE.version || response.id !== this.pending.id
            || response.ok !== true || !isJsonObject(response.result)) throw new Error('budget operation failed or returned an invalid receipt');
          const pending = this.pending; this.pending = null; clearTimeout(pending.timer);
          pending.resolve(response.result);
        } catch (error) { this.fail(error instanceof Error ? error : new Error('invalid budget response')); }
      }
    });
  }

  private fail(error: Error): void {
    this.error ??= error;
    if (this.pending) {
      const pending = this.pending; this.pending = null; clearTimeout(pending.timer); pending.reject(this.error);
    }
    this.failure.abort();
    // This process never spawns descendants. OS lock release exposes its marker.
    if (this.child.exitCode === null) this.child.kill('SIGKILL');
  }

  request(method: string, params: JsonObject = {}): Promise<JsonObject> {
    if (this.error) return Promise.reject(this.error);
    if (this.closing || this.pending) return Promise.reject(new Error('budget session is closed or busy'));
    const id = ++this.sequence;
    const input = JSON.stringify({ protocol: BUDGET_BRIDGE.protocol, version: BUDGET_BRIDGE.version, id, method, params }) + '\n';
    if (Buffer.byteLength(input) > BUDGET_BRIDGE.max_line_bytes) return Promise.reject(new Error('budget request exceeds its limit'));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fail(new Error('budget operation timed out')), this.timeout);
      this.pending = { id, resolve, reject, timer };
      this.child.stdin.write(input, 'utf8', error => { if (error) this.fail(error); });
    });
  }

  async close(): Promise<void> {
    this.closing = true;
    this.child.stdin.end();
    const timer = setTimeout(() => this.fail(new Error('budget owner did not close')), this.timeout);
    try { await this.exited; } finally { clearTimeout(timer); }
  }
}
