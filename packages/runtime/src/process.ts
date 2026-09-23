import { execFile, spawn } from 'node:child_process';
import { realpathSync } from 'node:fs';
import { isAbsolute } from 'node:path';
import { StringDecoder } from 'node:string_decoder';
import { PROCESS_GUARD, isJsonObject, type RunnerStopKind } from '@argus/contracts';

export interface ProcessGuardianOptions {
  executable: string;
  sourceRoot: string;
  prefixArgs?: string[];
  env?: NodeJS.ProcessEnv;
}

export interface ProcessOptions {
  executable: string;
  args: string[];
  cwd: string;
  input: string;
  env?: NodeJS.ProcessEnv;
  signal?: AbortSignal;
  wallTimeoutMs?: number;
  idleTimeoutMs?: number;
  terminateGraceMs?: number;
  maxLineBytes?: number;
  maxBufferedBytes?: number;
  /** Native OS shim. Its private lease belongs to this runtime, never the UI. */
  guardian?: ProcessGuardianOptions;
}

type Line = { type: 'line'; stream: 'stdout' | 'stderr'; line: string };
export interface ProcessExit {
  type: 'exit';
  code: number | null;
  signal: string | null;
  stopKind: RunnerStopKind | null;
  error: string | null;
}

/** A bounded channel; an unread stream must not exhaust the supervisor's heap. */
class LineChannel {
  private items: Array<{ value: Line; size: number }> = [];
  private bytes = 0;
  private closed = false;
  private wake: (() => void) | undefined;
  constructor(private readonly limit: number) {}
  push(value: Line): boolean {
    const size = Buffer.byteLength(value.line) + 1;
    if (this.closed || this.items.length >= 1024 || this.bytes + size > this.limit) return false;
    this.items.push({ value, size });
    this.bytes += size;
    this.wake?.();
    return true;
  }
  close(): void { this.closed = true; this.wake?.(); }
  async next(): Promise<Line | undefined> {
    while (true) {
      const item = this.items.shift();
      if (item) { this.bytes -= item.size; return item.value; }
      if (this.closed) return undefined;
      await new Promise<void>(resolve => { this.wake = resolve; });
      this.wake = undefined;
    }
  }
}

function positive(value: number | undefined, fallback: number, name: string): number {
  const result = value ?? fallback;
  if (!Number.isSafeInteger(result) || result <= 0 || result > 2_147_483_647) {
    throw new RangeError(`${name} must be a positive integer <= 2147483647`);
  }
  return result;
}

/** No shell interpolation; stdout/stderr and process lifetime remain independent. */
export async function* executeProcess(options: ProcessOptions): AsyncGenerator<Line | ProcessExit> {
  const wallMs = positive(options.wallTimeoutMs, 300_000, 'wallTimeoutMs');
  const idleMs = positive(options.idleTimeoutMs, 60_000, 'idleTimeoutMs');
  const graceMs = positive(options.terminateGraceMs, 1_000, 'terminateGraceMs');
  const lineLimit = positive(options.maxLineBytes, 1_048_576, 'maxLineBytes');
  const channel = new LineChannel(positive(options.maxBufferedBytes, 4_194_304, 'maxBufferedBytes'));
  if (options.signal?.aborted) {
    yield { type: 'exit', code: null, signal: null, stopKind: 'cancelled', error: 'Run cancelled before spawn.' };
    return;
  }
  const guardian = options.guardian;
  let launchExecutable = options.executable;
  let launchArgs = options.args;
  let launchCwd = options.cwd;
  let launchEnv = options.env ?? process.env;
  let configuration = '';
  if (guardian) {
    if (!isAbsolute(guardian.sourceRoot)) throw new Error('guardian sourceRoot must be absolute');
    launchExecutable = guardian.executable;
    launchCwd = realpathSync(guardian.sourceRoot);
    launchArgs = [...(guardian.prefixArgs ?? []), '-m', 'argus.agent_cli.process_guard'];
    launchEnv = { ...process.env, ...guardian.env, PYTHONPATH: launchCwd, PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1' };
    configuration = JSON.stringify({ protocol: PROCESS_GUARD.protocol, version: PROCESS_GUARD.version,
      command: [options.executable, ...options.args], cwd: options.cwd, env: options.env ?? process.env,
      input: options.input, wall_ms: Math.min(2_147_483_647, wallMs + Math.min(graceMs, 10_000)), grace_ms: graceMs,
    }) + '\n';
    if (Buffer.byteLength(configuration) > PROCESS_GUARD.max_config_bytes) throw new Error('guarded process configuration exceeds its limit');
  }
  const child = spawn(launchExecutable, launchArgs, {
    cwd: launchCwd, env: launchEnv,
    stdio: ['pipe', 'pipe', 'pipe'], shell: false,
    detached: process.platform !== 'win32', windowsHide: true,
  });
  let exitCode: number | null = null;
  let exitSignal: string | null = null;
  let stopKind: RunnerStopKind | null = null;
  let error: string | null = null;
  let closed = false;
  let exited = false;
  let groupCleanupAttempted = false;
  let escalation: NodeJS.Timeout | undefined;
  let drain: NodeJS.Timeout | undefined;
  let windowsKill: Promise<void> | undefined;
  let guardReceipt: { code: number | null; signal: string | null; error: string | null } | undefined;
  let resolveClose: () => void = () => {};
  const closePromise = new Promise<void>(resolve => { resolveClose = resolve; });

  const killTree = (force: boolean): void => {
    if (!child.pid) return;
    if (guardian) {
      // Closing this private pipe means the execution owner cancelled or died.
      // Closing a browser/TUI connection never closes it in a detached daemon.
      if (!force) { child.stdin.end(); return; }
      if (process.platform === 'win32') { if (!exited) child.kill('SIGKILL'); return; }
    }
    if (process.platform === 'win32') {
      if (exited || windowsKill) return;
      windowsKill = new Promise<void>(resolve => {
        execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, timeout: 5_000 }, taskError => {
          if (taskError && !exited) {
            error ??= `Unable to terminate process tree: ${taskError.message}`;
            stopKind ??= 'transport_error';
            child.kill();
          }
          resolve();
        });
      });
      return;
    }
    if (groupCleanupAttempted) return;
    if (force) groupCleanupAttempted = true;
    try { process.kill(-child.pid, force ? 'SIGKILL' : 'SIGTERM'); }
    catch (cause) {
      if ((cause as NodeJS.ErrnoException).code !== 'ESRCH') {
        error ??= `Unable to terminate process group: ${String(cause)}`;
        stopKind ??= 'transport_error';
      }
    }
  };
  const requestStop = (kind: RunnerStopKind, message: string): void => {
    if (stopKind || closed) return;
    stopKind = kind;
    error ??= message;
    killTree(false);
    escalation = setTimeout(() => killTree(true), graceMs);
  };
  const wall = setTimeout(() => requestStop('wall_timeout', 'Runner wall-clock deadline exceeded.'), wallMs);
  const idle = setTimeout(() => requestStop('idle_timeout', 'Runner output idle deadline exceeded.'), idleMs);
  const abort = (): void => requestStop('cancelled', 'Run cancelled.');
  options.signal?.addEventListener('abort', abort, { once: true });
  // Covers cancellation between the preflight and listener installation.
  if (options.signal?.aborted) abort();

  const readers = {} as Record<'stdout' | 'stderr', { data: (chunk: Buffer) => void; end: () => void }>;
  for (const stream of ['stdout', 'stderr'] as const) {
    const decoder = new StringDecoder('utf8');
    let pending = '';
    const emit = (line: string): void => {
      if (stopKind) return;
      if (Buffer.byteLength(line) > lineLimit || !channel.push({ type: 'line', stream, line: line.replace(/\r$/, '') })) {
        requestStop('output_limit', 'Runner output exceeded its bounded buffer.');
      }
    };
    const data = (chunk: Buffer): void => {
      if (!exited) idle.refresh();
      if (stopKind) return;
      pending += decoder.write(chunk);
      let newline: number;
      while ((newline = pending.indexOf('\n')) !== -1) {
        emit(pending.slice(0, newline));
        pending = pending.slice(newline + 1);
        if (stopKind) { pending = ''; return; }
      }
      if (Buffer.byteLength(pending) > lineLimit) {
        pending = '';
        requestStop('output_limit', 'Runner output line exceeded its byte limit.');
      }
    };
    const end = (): void => {
      pending += decoder.end();
      if (pending) emit(pending);
      pending = '';
    };
    readers[stream] = { data, end };
    if (!guardian) { child[stream].on('data', data); child[stream].on('end', end); }
    child[stream].on('error', cause => requestStop('transport_error', `${stream}: ${cause.message}`));
  }
  if (guardian) {
    const decoder = new StringDecoder('utf8');
    let pending = '';
    child.stdout.on('data', (chunk: Buffer) => {
      if (stopKind) return;
      pending += decoder.write(chunk);
      let newline: number;
      while ((newline = pending.indexOf('\n')) >= 0) {
        const line = pending.slice(0, newline); pending = pending.slice(newline + 1);
        try {
          if (Buffer.byteLength(line) > PROCESS_GUARD.max_frame_bytes) throw new Error('oversized guard frame');
          const frame: unknown = JSON.parse(line);
          if (!isJsonObject(frame) || frame.protocol !== PROCESS_GUARD.protocol || frame.version !== PROCESS_GUARD.version || guardReceipt) {
            throw new Error('incompatible process guard receipt');
          }
          if (frame.type === 'data' && (frame.stream === 'stdout' || frame.stream === 'stderr') && typeof frame.data === 'string') {
            const bytes = Buffer.from(frame.data, 'base64');
            if (bytes.length > PROCESS_GUARD.chunk_bytes || bytes.toString('base64') !== frame.data) throw new Error('invalid process data');
            readers[frame.stream].data(bytes);
          } else if (frame.type === 'exit'
            && (frame.code === null || (typeof frame.code === 'number' && Number.isSafeInteger(frame.code) && frame.code >= 0))
            && (frame.signal === null || typeof frame.signal === 'string') && (frame.error === null || typeof frame.error === 'string')) {
            guardReceipt = { code: frame.code, signal: frame.signal, error: frame.error };
            readers.stdout.end(); readers.stderr.end();
            if (frame.error) requestStop('transport_error', frame.error);
          } else throw new Error('invalid process guard frame');
        } catch (cause) { requestStop('transport_error', cause instanceof Error ? cause.message : 'invalid process guard frame'); }
        if (stopKind) { pending = ''; return; }
      }
      if (Buffer.byteLength(pending) > PROCESS_GUARD.max_frame_bytes) {
        pending = ''; requestStop('output_limit', 'process guard frame exceeds its limit');
      }
    });
    child.stderr.on('data', readers.stderr.data);
    child.stdout.on('end', () => {
      pending += decoder.end();
      if (pending && !stopKind) requestStop('transport_error', 'process guard returned an unterminated frame');
    });
  }
  child.stdin.on('error', (cause: NodeJS.ErrnoException) => {
    // A short-lived provider can exit before consuming its entire prompt.
    if (cause.code !== 'EPIPE') requestStop('transport_error', `stdin: ${cause.message}`);
  });
  child.on('error', cause => requestStop('transport_error', cause.message));
  child.on('exit', (code, signal) => {
    exitCode = code;
    exitSignal = signal;
    exited = true;
    if (guardian) killTree(true);
    clearTimeout(wall);
    clearTimeout(idle);
    // Descendants may retain inherited pipes after the CLI itself exits.
    drain = setTimeout(() => {
      requestStop('transport_error', 'Provider exited but its output pipes did not close.');
      killTree(true);
      child.stdout.destroy();
      child.stderr.destroy();
    }, graceMs);
  });
  child.on('close', (code, signal) => {
    exitCode = code;
    exitSignal = signal;
    // Reclaim while ownership is fresh, not after an arbitrarily slow consumer
    // resumes iteration (the numeric PID could have been reused by then).
    killTree(true);
    closed = true;
    channel.close();
    resolveClose();
  });
  if (guardian) child.stdin.write(configuration, 'utf8');
  else child.stdin.end(options.input, 'utf8');
  try {
    let line: Line | undefined;
    while ((line = await channel.next()) !== undefined) yield line;
    await closePromise;
  } finally {
    if (!closed) requestStop('cancelled', 'Runner consumer closed the event stream.');
    await closePromise;
    await windowsKill;
    clearTimeout(wall);
    clearTimeout(idle);
    clearTimeout(escalation);
    clearTimeout(drain);
    options.signal?.removeEventListener('abort', abort);
  }
  if (guardian) {
    const teardown = exitCode === 0 || (process.platform !== 'win32' && exitSignal === 'SIGKILL');
    if (!stopKind && (!guardReceipt || !teardown)) {
      stopKind = 'transport_error'; error ??= 'Process guard exited without a complete provider receipt.';
    }
    exitCode = guardReceipt?.code ?? null;
    exitSignal = guardReceipt?.signal ?? null;
  }
  yield { type: 'exit', code: exitCode, signal: exitSignal, stopKind, error };
}
