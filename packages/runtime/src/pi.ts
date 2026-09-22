import { isJsonObject, type RunnerResult, type RunnerStreamEvent } from '@argus/contracts';
import { PiEventConsumer } from './piEvents.js';
import { executeProcess, type ProcessOptions } from './process.js';

export interface PiRunRequest {
  prompt: string;
  cwd: string;
  model?: string;
  provider?: string;
  reasoningEffort?: 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';
  sessionDirectory?: string;
  resumeSession?: string;
  toolPolicy: 'disabled' | 'read-only' | 'workspace-write';
  skillPaths?: string[];
  signal?: AbortSignal;
  wallTimeoutMs?: number;
  idleTimeoutMs?: number;
  maxRetainedTextBytes?: number;
}

export interface PiBackendOptions {
  executable?: string;
  /** Supports a direct `node /path/to/pi.js` launcher without a shell. */
  prefixArgs?: string[];
  env?: NodeJS.ProcessEnv;
  terminateGraceMs?: number;
  maxLineBytes?: number;
  maxBufferedBytes?: number;
}

export interface RunnerBackend {
  fork(): RunnerBackend;
  run(request: PiRunRequest): AsyncIterable<RunnerStreamEvent>;
}

export function buildPiCommand(request: PiRunRequest): string[] {
  if (!request.prompt.trim()) throw new Error('prompt must not be empty');
  if (request.resumeSession && !request.sessionDirectory) throw new Error('sessionDirectory is required to resume a session');
  if (!['disabled', 'read-only', 'workspace-write'].includes(request.toolPolicy)) throw new Error('toolPolicy must be explicit');
  const args = ['--mode', 'json'];
  if (request.sessionDirectory) args.push('--session-dir', request.sessionDirectory);
  else args.push('--no-session');
  args.push('--no-extensions', '--no-skills', '--no-prompt-templates', '--no-themes', '--no-context-files', '--no-approve');
  for (const path of request.skillPaths ?? []) args.push('--skill', path);
  const model = request.model?.trim();
  const provider = request.provider?.trim();
  if (model) args.push('--model', provider && !model.includes('/') ? `${provider}/${model}` : model);
  if (request.reasoningEffort) args.push('--thinking', request.reasoningEffort);
  if (request.toolPolicy === 'disabled') args.push('--no-tools');
  else if (request.toolPolicy === 'read-only') args.push('--tools', 'read,grep,find,ls');
  if (request.resumeSession) args.push('--session', request.resumeSession);
  return args;
}

/** Experimental transport adapter; the Python daemon still owns task/budget state. */
export class PiBackend implements RunnerBackend {
  constructor(private readonly options: PiBackendOptions = {}) {}
  fork(): PiBackend { return new PiBackend({ ...this.options }); }

  async *run(request: PiRunRequest): AsyncGenerator<RunnerStreamEvent> {
    const args = [...(this.options.prefixArgs ?? []), ...buildPiCommand(request)];
    const executable = this.options.executable ?? 'pi';
    const limit = request.maxRetainedTextBytes ?? 1_048_576;
    if (!Number.isSafeInteger(limit) || limit <= 0) throw new Error('maxRetainedTextBytes must be a positive integer');
    const consumer = new PiEventConsumer();
    consumer.threadId = request.resumeSession ?? null;
    const controller = new AbortController();
    const signal = request.signal ? AbortSignal.any([request.signal, controller.signal]) : controller.signal;
    let stdoutLineCount = 0;
    let stderrLineCount = 0;
    let jsonEventCount = 0;
    let textLimit = false;
    const processOptions: ProcessOptions = {
      ...this.options, executable, args, cwd: request.cwd, input: request.prompt, signal,
      ...(request.wallTimeoutMs === undefined ? {} : { wallTimeoutMs: request.wallTimeoutMs }),
      ...(request.idleTimeoutMs === undefined ? {} : { idleTimeoutMs: request.idleTimeoutMs }),
    };
    for await (const item of executeProcess(processOptions)) {
      if (item.type === 'line') {
        if (item.stream === 'stdout') stdoutLineCount += 1;
        else stderrLineCount += 1;
        yield item;
        if (item.stream !== 'stdout' || textLimit) continue;
        let parsed: unknown;
        try { parsed = JSON.parse(item.line); } catch { continue; }
        if (!isJsonObject(parsed)) continue;
        jsonEventCount += 1;
        consumer.consume(parsed);
        if (consumer.agentMessages.reduce((bytes, message) => bytes + Buffer.byteLength(message), 0) > limit) {
          textLimit = true;
          controller.abort();
          // Do not expose a truncated reply as a final assistant answer.
          consumer.agentMessages = [];
        }
        yield { type: 'provider_event', event: parsed };
      } else {
        const stopKind = textLimit ? 'output_limit' : item.stopKind;
        const failed = consumer.turnFailed || stopKind !== null || item.code !== 0 || !consumer.turnCompleted;
        const result: RunnerResult = {
          command: [executable, ...args], exitCode: item.code, signal: item.signal,
          threadId: consumer.threadId, agentMessages: [...consumer.agentMessages],
          turnCompleted: consumer.turnCompleted && !failed, turnFailed: failed,
          fatalError: textLimit ? 'Retained assistant text exceeded its byte limit.'
            : item.error ?? consumer.fatalError ?? (failed ? `Pi exited ${item.code ?? item.signal ?? 'without an exit code'} without a successful settled turn.` : null),
          stopKind, stdoutLineCount, stderrLineCount, jsonEventCount,
          providerTurns: consumer.providerTurns, toolActivityObserved: consumer.toolActivityObserved,
        };
        yield { type: 'result', result };
      }
    }
  }
}
