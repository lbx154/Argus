import { execFile, spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { realpathSync } from 'node:fs';
import { readFile, readdir } from 'node:fs/promises';
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
  /** Written to stdin, which then closes; an iterable is written as it yields, and stdin closes when it ends. */
  input: string | AsyncIterable<string>;
  /** Told when the process itself exits, before what it started has ended. */
  onExit?: (code: number | null, signal: string | null) => void;
  env?: NodeJS.ProcessEnv;
  signal?: AbortSignal;
  /** Deadlines are the caller's choice; left out, none applies. */
  wallTimeoutMs?: number;
  idleTimeoutMs?: number;
  terminateGraceMs?: number;
  maxLineBytes?: number;
  /** Stop with output_limit on an oversized line; by default only that line is dropped. */
  stopOnLineOverflow?: boolean;
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

/** The processes whose environment holds `entry`. Linux and macOS show a process's environment to
 * its owner. Windows shows it to no other process, and Node offers no job object to hold what a
 * process starts: there a run sees what its CLI started only while the CLI lives (taskkill /T) or
 * while it holds the run's output pipes. */
async function carrying(entry: string): Promise<number[]> {
  if (process.platform === 'win32') return [];
  if (process.platform === 'linux') {
    const pids = (await readdir('/proc').catch(() => [])).filter(name => /^\d+$/.test(name));
    const environs = await Promise.all(pids.map(pid => readFile(`/proc/${pid}/environ`, 'latin1').catch(() => '')));
    return pids.filter((_, at) => environs[at]!.split('\0').includes(entry)).map(Number);
  }
  // ps -E puts a process's environment after its command.
  const listing = await new Promise<string>(resolve => {
    execFile('ps', ['-AEww', '-o', 'pid,command'], { maxBuffer: 2 ** 30 }, (_, stdout) => resolve(stdout ?? ''));
  });
  return listing.split('\n').filter(line => line.includes(` ${entry}`)).map(line => parseInt(line, 10));
}

/** No shell interpolation; stdout/stderr and process lifetime remain independent. A run ends when
 * everything it started has ended: what holds its output pipes, what stays in its process group,
 * and what carries its ARGUS_TURN, which every descendant inherits however it detaches (nohup,
 * setsid, a double fork) unless it clears its environment. A stop ends all of them. Windows
 * shows a run only part of this (see `carrying`). */
export async function* executeProcess(options: ProcessOptions): AsyncGenerator<Line | ProcessExit> {
  const wallMs = options.wallTimeoutMs === undefined ? null : positive(options.wallTimeoutMs, 0, 'wallTimeoutMs');
  const idleMs = options.idleTimeoutMs === undefined ? null : positive(options.idleTimeoutMs, 0, 'idleTimeoutMs');
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
    if (typeof options.input !== 'string') throw new Error('a guarded process takes its input whole');
    launchExecutable = guardian.executable;
    launchCwd = realpathSync(guardian.sourceRoot);
    launchArgs = [...(guardian.prefixArgs ?? []), '-m', 'argus.agent_cli.process_guard'];
    launchEnv = { ...process.env, ...guardian.env, PYTHONPATH: launchCwd, PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1' };
    configuration = JSON.stringify({ protocol: PROCESS_GUARD.protocol, version: PROCESS_GUARD.version,
      command: [options.executable, ...options.args], cwd: options.cwd, env: options.env ?? process.env,
      input: options.input, wall_ms: Math.min(2_147_483_647, (wallMs ?? 2_147_483_647) + Math.min(graceMs, 10_000)), grace_ms: graceMs,
    }) + '\n';
    if (Buffer.byteLength(configuration) > PROCESS_GUARD.max_config_bytes) throw new Error('guarded process configuration exceeds its limit');
  }
  const turn = randomUUID();
  const child = spawn(launchExecutable, launchArgs, {
    cwd: launchCwd, env: { ...launchEnv, ARGUS_TURN: turn },
    stdio: ['pipe', 'pipe', 'pipe'], shell: false,
    detached: process.platform !== 'win32', windowsHide: true,
  });
  let exitCode: number | null = null;
  let exitSignal: string | null = null;
  let stopKind: RunnerStopKind | null = null;
  let error: string | null = null;
  let closed = false;
  let exited = false;
  let forced = false;
  // Once empty, a process group's number is free for another to take.
  let groupGone = process.platform === 'win32' || !child.pid;
  let escalation: NodeJS.Timeout | undefined;
  let wake = (): void => {};
  let windowsKill: Promise<void> | undefined;
  let guardReceipt: { code: number | null; signal: string | null; error: string | null } | undefined;
  let resolveClose: () => void = () => {};
  const closePromise = new Promise<void>(resolve => { resolveClose = resolve; });

  /** What the run still runs, as signal targets: its process group and what carries its turn. */
  const running = async (): Promise<number[]> => {
    const targets = await carrying(`ARGUS_TURN=${turn}`);
    if (!groupGone) {
      try { process.kill(-child.pid!, 0); targets.push(-child.pid!); } catch { groupGone = true; }
    }
    return targets;
  };
  const kill = (targets: number[], signal: NodeJS.Signals): void => {
    for (const target of targets) try { process.kill(target, signal); } catch { /* it has ended */ }
  };
  const killTree = (force: boolean): void => {
    if (!child.pid) return;
    if (guardian) {
      // Closing this private pipe means the execution owner cancelled or died.
      // Closing a browser connection never closes it in a detached daemon.
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
    void running().then(targets => kill(targets, force ? 'SIGKILL' : 'SIGTERM'));
  };
  const requestStop = (kind: RunnerStopKind, message: string): void => {
    if (stopKind || closed) return;
    stopKind = kind;
    error ??= message;
    killTree(false);
    wake();
    escalation = setTimeout(() => {
      forced = true;
      killTree(true);
      // What a stop cannot reach (on Windows, all the CLI started once it has exited) holds the run no longer.
      child.stdout.destroy();
      child.stderr.destroy();
    }, graceMs);
  };
  const wall = wallMs === null ? undefined : setTimeout(() => requestStop('wall_timeout', 'Runner wall-clock deadline exceeded.'), wallMs);
  const idle = idleMs === null ? undefined : setTimeout(() => requestStop('idle_timeout', 'Runner output idle deadline exceeded.'), idleMs);
  const abort = (): void => requestStop('cancelled', 'Run cancelled.');
  options.signal?.addEventListener('abort', abort, { once: true });
  // Covers cancellation between the preflight and listener installation.
  if (options.signal?.aborted) abort();

  const readers = {} as Record<'stdout' | 'stderr', { data: (chunk: Buffer) => void; end: () => void }>;
  for (const stream of ['stdout', 'stderr'] as const) {
    const decoder = new StringDecoder('utf8');
    let pending: string[] = [];
    let pendingBytes = 0;
    // By default a line past the limit is dropped: one oversized event (a command's whole
    // output) costs only itself, and memory stays bounded while the rest of it streams past.
    let skipping = false;
    const emit = (): void => {
      const line = pending.join('');
      pending = [];
      pendingBytes = 0;
      if (stopKind) return;
      if (!channel.push({ type: 'line', stream, line: line.replace(/\r$/, '') })) {
        requestStop('output_limit', 'Runner output exceeded its bounded buffer.');
      }
    };
    const consume = (text: string): void => {
      if (stopKind) return;
      // Scan only new text; a long unterminated line must not rescan its prefix.
      let start = 0;
      while (start < text.length) {
        const newline = text.indexOf('\n', start);
        const part = text.slice(start, newline === -1 ? text.length : newline);
        if (!skipping) {
          pendingBytes += Buffer.byteLength(part);
          if (pendingBytes > lineLimit) {
            pending = [];
            pendingBytes = 0;
            if (options.stopOnLineOverflow) {
              requestStop('output_limit', 'Runner output line exceeded its byte limit.');
              return;
            }
            skipping = true;
          } else if (part) pending.push(part);
        }
        if (newline === -1) return;
        if (skipping) skipping = false;
        else emit();
        if (stopKind) return;
        start = newline + 1;
      }
    };
    const data = (chunk: Buffer): void => {
      idle?.refresh();
      if (!stopKind) consume(decoder.write(chunk));
    };
    const end = (): void => {
      consume(decoder.end());
      if (pending.length) emit();
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
    options.onExit?.(code, signal);
    if (guardian) killTree(true);
  });
  child.on('close', (code, signal) => {
    exitCode = code;
    exitSignal = signal;
    // Polled, as only a parent can wait for a process: seldom while it runs, often once it is
    // stopped; past a stop's grace, what is left is killed.
    void (async () => {
      for (let left = await running(); left.length; left = await running()) {
        if (forced) kill(left, 'SIGKILL');
        await new Promise<void>(resolve => {
          const timer = setTimeout(resolve, stopKind ? 100 : 5_000);
          wake = () => { clearTimeout(timer); resolve(); };
        });
      }
      closed = true;
      channel.close();
      resolveClose();
    })();
  });
  const input = options.input;
  if (guardian) child.stdin.write(configuration, 'utf8');
  else if (typeof input === 'string') child.stdin.end(input, 'utf8');
  else {
    void (async () => {
      try { for await (const chunk of input) if (!child.stdin.destroyed) child.stdin.write(chunk, 'utf8'); } finally { child.stdin.end(); }
    })();
  }
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
